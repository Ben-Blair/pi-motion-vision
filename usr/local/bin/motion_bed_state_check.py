#!/usr/bin/env python3
"""Periodic bed state checker using PatchCore checkpoint.

Flow:
1) Load one frame from a local file (preferred) or Motion HTTP endpoint.
2) Apply optional ROI crop/mask.
3) Score frame with Anomalib Patchcore checkpoint.
4) Apply hysteresis + consecutive confirmation.
5) Send email only when state changes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from anomalib.data import PredictDataset
    from anomalib.engine import Engine
    from anomalib.models import Patchcore
except Exception as exc:  # pragma: no cover - runtime dependency
    raise SystemExit(
        "Failed to import anomalib runtime. Install on Pi with:\n"
        "  python3 -m pip install 'anomalib[full,cpu]' opencv-python\n"
        f"Original import error: {exc}"
    ) from exc


@dataclass
class RoiSpec:
    bbox: tuple[int, int, int, int]  # x, y, w, h
    polygon: list[tuple[int, int]] | None = None
    ref_size: tuple[int, int] | None = None  # width, height ROI was authored against


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Motion bed state checker")
    p.add_argument("--model-ckpt", required=True, help="Path to Anomalib model.ckpt")
    p.add_argument(
        "--frame-file",
        default="",
        help="Preferred local JPEG path (e.g. /dev/shm/bed_latest.jpg).",
    )
    p.add_argument(
        "--frame-url",
        default="http://127.0.0.1:8080/substream",
        help="Fallback Motion endpoint when --frame-file is not set/available.",
    )
    p.add_argument(
        "--work-dir",
        default="/var/lib/motion/bed_state",
        help="Working directory for temp files/log artifacts.",
    )
    p.add_argument(
        "--state-file",
        default="/var/lib/motion/bed_state_state.json",
        help="Persistent state json (last state, pending state, etc.)",
    )
    p.add_argument(
        "--roi-meta",
        default="",
        help="Optional ROI json path. Supports bbox or polygon-like point lists.",
    )
    p.add_argument("--mask-polygon", action="store_true", help="Mask outside polygon before crop.")
    p.add_argument("--pad", type=int, default=0, help="Pixels padding around bbox.")
    p.add_argument("--t-low", type=float, default=0.25, help="MADE threshold.")
    p.add_argument("--t-high", type=float, default=0.40, help="NOT_MADE threshold.")
    p.add_argument(
        "--confirm-count",
        type=int,
        default=2,
        help="Consecutive checks required before state change.",
    )
    p.add_argument(
        "--double-check-delay-sec",
        type=int,
        default=300,
        help="Delay between first and second check in seconds (default 5 minutes).",
    )
    p.add_argument(
        "--require-second-check-for-email",
        action="store_true",
        help="Require second check agreement before sending email.",
    )
    p.add_argument(
        "--manual-two-step",
        action="store_true",
        help="Manual testing mode: first run saves check, second run compares and can email.",
    )
    p.add_argument(
        "--manual-session-file",
        default="/dev/shm/motion_bed_manual_session.json",
        help="Session file used by --manual-two-step to store the first run.",
    )
    p.add_argument(
        "--manual-expire-sec",
        type=int,
        default=600,
        help="Expiration window for the first manual run before second compare.",
    )
    p.add_argument(
        "--email-script",
        default="/usr/local/bin/motion_bed_state_email.sh",
        help="Script called on state transitions.",
    )
    p.add_argument("--timeout-sec", type=float, default=5.0, help="HTTP read timeout.")
    p.add_argument("--image-size", type=int, default=256, help="PredictDataset resize target.")
    p.add_argument(
        "--save-debug-frames",
        action="store_true",
        help="Persist debug frames under work-dir (off by default to reduce SD writes).",
    )
    return p.parse_args()


def _extract_points(obj: Any) -> list[tuple[int, int]]:
    """Extract points from common JSON shapes."""
    points: list[tuple[int, int]] = []

    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                points.append((int(item[0]), int(item[1])))
            elif isinstance(item, dict) and "x" in item and "y" in item:
                points.append((int(item["x"]), int(item["y"])))
        return points

    if isinstance(obj, dict):
        for key in ("polygon", "points", "vertices", "roi_points"):
            if key in obj:
                got = _extract_points(obj[key])
                if got:
                    return got
        # Common nested shape.
        for key in ("roi", "region", "bed", "mask"):
            if key in obj:
                got = _extract_points(obj[key])
                if got:
                    return got
    return points


def _extract_bbox(obj: Any) -> tuple[int, int, int, int] | None:
    """Extract bbox from common JSON shapes as x,y,w,h."""
    if isinstance(obj, dict):
        if all(k in obj for k in ("x", "y", "w", "h")):
            return int(obj["x"]), int(obj["y"]), int(obj["w"]), int(obj["h"])
        if all(k in obj for k in ("left", "top", "width", "height")):
            return int(obj["left"]), int(obj["top"]), int(obj["width"]), int(obj["height"])
        if "bbox" in obj:
            return _extract_bbox(obj["bbox"])
    if isinstance(obj, (list, tuple)) and len(obj) == 4:
        a, b, c, d = [int(v) for v in obj]
        # Heuristic: if c,d look like max coords, convert to w,h.
        if c > a and d > b and (c - a) > 0 and (d - b) > 0:
            # Ambiguous; prefer x,y,w,h unless values look huge as x2,y2.
            # If values exceed common frame sizes, treat as x2/y2.
            if c > 1920 or d > 1080:
                return a, b, c - a, d - b
        return a, b, c, d
    return None


def load_roi(path: str) -> RoiSpec | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"ROI file not found: {path}")

    data = json.loads(p.read_text())
    ref_size: tuple[int, int] | None = None
    if isinstance(data, dict):
        poly = data.get("polygon")
        if isinstance(poly, dict):
            rw = poly.get("ref_width")
            rh = poly.get("ref_height")
            if isinstance(rw, (int, float)) and isinstance(rh, (int, float)) and rw > 0 and rh > 0:
                ref_size = (int(rw), int(rh))

    bbox = _extract_bbox(data)
    points = _extract_points(data)
    if points and not bbox:
        xs = [pt[0] for pt in points]
        ys = [pt[1] for pt in points]
        bbox = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if not bbox:
        raise ValueError(f"Could not parse bbox or polygon from ROI file: {path}")
    return RoiSpec(bbox=bbox, polygon=points or None, ref_size=ref_size)


def scale_roi_to_frame(roi: RoiSpec | None, frame_w: int, frame_h: int) -> RoiSpec | None:
    if roi is None or roi.ref_size is None:
        return roi
    ref_w, ref_h = roi.ref_size
    if ref_w <= 0 or ref_h <= 0:
        return roi
    sx = frame_w / float(ref_w)
    sy = frame_h / float(ref_h)
    x, y, w, h = roi.bbox
    scaled_bbox = (
        int(round(x * sx)),
        int(round(y * sy)),
        int(round(w * sx)),
        int(round(h * sy)),
    )
    scaled_poly: list[tuple[int, int]] | None = None
    if roi.polygon:
        scaled_poly = [
            (int(round(px * sx)), int(round(py * sy)))
            for px, py in roi.polygon
        ]
    return RoiSpec(bbox=scaled_bbox, polygon=scaled_poly, ref_size=roi.ref_size)


def fetch_first_jpeg(url: str, timeout_sec: float) -> bytes:
    """Fetch first JPEG frame from either JPEG or MJPEG endpoint."""
    req = urllib.request.Request(url, headers={"User-Agent": "motion-bed-check/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = bytearray()
        start = time.time()
        while time.time() - start < timeout_sec:
            chunk = resp.read(8192)
            if not chunk:
                break
            data.extend(chunk)
            s = data.find(b"\xff\xd8")
            if s != -1:
                e = data.find(b"\xff\xd9", s + 2)
                if e != -1:
                    return bytes(data[s : e + 2])
    raise RuntimeError(f"No JPEG frame found from endpoint: {url}")


def read_jpeg_file(path: str) -> bytes | None:
    p = Path(path)
    if not p.is_file():
        return None
    data = p.read_bytes()
    if len(data) < 1000:
        return None
    return data


def apply_roi(img: np.ndarray, roi: RoiSpec | None, mask_polygon: bool, pad: int) -> np.ndarray:
    if roi is None:
        return img
    x, y, w, h = roi.bbox
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = max(1, w + 2 * pad)
    h = max(1, h + 2 * pad)
    x2 = min(img.shape[1], x + w)
    y2 = min(img.shape[0], y + h)
    x = min(x, x2 - 1)
    y = min(y, y2 - 1)

    roi_img = img.copy()
    if mask_polygon and roi.polygon:
        poly = np.array(roi.polygon, dtype=np.int32).reshape((-1, 1, 2))
        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [poly], 255)
        masked = np.zeros_like(img)
        masked[mask == 255] = img[mask == 255]
        roi_img = masked

    return roi_img[y:y2, x:x2]


def draw_roi_outline(img: np.ndarray, roi: RoiSpec | None, pad: int) -> np.ndarray:
    """Return full-frame image with bed ROI outlined for email/debug."""
    out = img.copy()
    if roi is None:
        return out

    if roi.polygon:
        poly = np.array(roi.polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(out, [poly], isClosed=True, color=(0, 255, 0), thickness=2)
    else:
        x, y, w, h = roi.bbox
        x = max(0, x - pad)
        y = max(0, y - pad)
        w = max(1, w + 2 * pad)
        h = max(1, h + 2 * pad)
        x2 = min(out.shape[1], x + w)
        y2 = min(out.shape[0], y + h)
        cv2.rectangle(out, (x, y), (x2, y2), (0, 255, 0), 2)

    return out


def score_image(ckpt_path: str, image_path: str, image_size: int) -> float:
    engine = Engine()
    model = Patchcore()
    dataset = PredictDataset(path=image_path, image_size=(image_size, image_size))
    try:
        preds = engine.predict(model=model, dataset=dataset, ckpt_path=ckpt_path)
    finally:
        # Prevent long-term growth from Anomalib prediction artifacts.
        shutil.rmtree("results", ignore_errors=True)
    if not preds:
        raise RuntimeError("No prediction returned by anomalib.")
    return float(preds[0].pred_score)


def load_state(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {
            "state": "unknown",
            "pending_state": "",
            "pending_count": 0,
            "last_score": None,
            "updated_at": 0,
        }
    try:
        return json.loads(p.read_text())
    except Exception:
        return {
            "state": "unknown",
            "pending_state": "",
            "pending_count": 0,
            "last_score": None,
            "updated_at": 0,
        }


def save_state(path: str, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def load_manual_session(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if "saved_at" not in data or "candidate" not in data or "score" not in data:
        return None
    return data


def save_manual_session(path: str, score: float, candidate: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": int(time.time()),
        "score": score,
        "candidate": candidate,
    }
    p.write_text(json.dumps(payload, indent=2))


def clear_manual_session(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def classify(score: float, t_low: float, t_high: float) -> str:
    if score < t_low:
        return "made"
    if score > t_high:
        return "not_made"
    return "indeterminate"


def score_current_frame(
    *,
    args: argparse.Namespace,
    roi: RoiSpec | None,
) -> tuple[float, str, np.ndarray, np.ndarray]:
    """Capture/score one frame and return score, candidate, full image, and ROI image."""
    if args.frame_file:
        jpeg = read_jpeg_file(args.frame_file)
        if jpeg is None:
            if args.frame_url:
                jpeg = fetch_first_jpeg(args.frame_url, args.timeout_sec)
            else:
                raise RuntimeError(f"Frame file not available: {args.frame_file}")
    else:
        jpeg = fetch_first_jpeg(args.frame_url, args.timeout_sec)

    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Failed to decode fetched JPEG frame.")

    frame_h, frame_w = img.shape[:2]
    roi_scaled = scale_roi_to_frame(roi, frame_w, frame_h)
    roi_img = apply_roi(img, roi_scaled, args.mask_polygon, args.pad)
    if roi_img.size == 0:
        raise RuntimeError("ROI crop resulted in empty image.")

    # Run inference from RAM-backed temp file to avoid per-check SD writes.
    roi_tmp = tempfile.NamedTemporaryFile(
        prefix="bed-roi-",
        suffix=".jpg",
        dir="/dev/shm",
        delete=False,
    )
    roi_tmp_path = Path(roi_tmp.name)
    roi_tmp.close()
    try:
        ok = cv2.imwrite(str(roi_tmp_path), roi_img)
        if not ok:
            raise RuntimeError(f"Failed to write temp ROI image: {roi_tmp_path}")
        score = score_image(args.model_ckpt, str(roi_tmp_path), args.image_size)
    finally:
        try:
            roi_tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    candidate = classify(score, args.t_low, args.t_high)
    return score, candidate, img, roi_img


def maybe_transition(
    state: dict[str, Any],
    candidate: str,
    confirm_count: int,
) -> tuple[bool, str]:
    """Return (changed, new_state)."""
    current = state.get("state", "unknown")
    if candidate == "indeterminate":
        state["pending_state"] = ""
        state["pending_count"] = 0
        return False, current

    if current == "unknown":
        state["state"] = candidate
        state["pending_state"] = ""
        state["pending_count"] = 0
        return False, candidate  # initialize silently

    if candidate == current:
        state["pending_state"] = ""
        state["pending_count"] = 0
        return False, current

    pending_state = state.get("pending_state", "")
    pending_count = int(state.get("pending_count", 0))
    if pending_state == candidate:
        pending_count += 1
    else:
        pending_state = candidate
        pending_count = 1

    state["pending_state"] = pending_state
    state["pending_count"] = pending_count

    if pending_count >= confirm_count:
        state["state"] = candidate
        state["pending_state"] = ""
        state["pending_count"] = 0
        return True, candidate
    return False, current


def send_transition_email(
    script: str,
    new_state: str,
    prev_state: str,
    score: float,
    image_path: str,
    t_low: float,
    t_high: float,
) -> None:
    subprocess.run(
        [
            script,
            new_state,
            prev_state,
            f"{score:.6f}",
            image_path,
            f"{t_low:.6f}",
            f"{t_high:.6f}",
        ],
        check=False,
    )


def send_state_email_with_annotation(
    *,
    args: argparse.Namespace,
    img: np.ndarray,
    roi: RoiSpec | None,
    new_state: str,
    prev_state: str,
    score: float,
) -> None:
    roi_for_annotated = scale_roi_to_frame(roi, img.shape[1], img.shape[0])
    annotated = draw_roi_outline(img, roi_for_annotated, args.pad)
    ann_tmp = tempfile.NamedTemporaryFile(
        prefix="bed-annotated-",
        suffix=".jpg",
        dir="/dev/shm",
        delete=False,
    )
    ann_tmp_path = Path(ann_tmp.name)
    ann_tmp.close()
    ok = cv2.imwrite(str(ann_tmp_path), annotated)
    if not ok:
        raise RuntimeError(f"Failed to write temp annotated image: {ann_tmp_path}")
    send_transition_email(
        script=args.email_script,
        new_state=new_state,
        prev_state=prev_state,
        score=score,
        image_path=str(ann_tmp_path),
        t_low=args.t_low,
        t_high=args.t_high,
    )
    try:
        ann_tmp_path.unlink(missing_ok=True)
    except Exception:
        pass


def main() -> int:
    args = parse_args()

    if args.t_low >= args.t_high:
        raise SystemExit("--t-low must be strictly less than --t-high")

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    frame_full = work / "latest_full.jpg"
    frame_roi = work / "latest_roi.jpg"
    frame_annotated = work / "latest_annotated.jpg"

    roi = load_roi(args.roi_meta) if args.roi_meta else None

    if args.frame_file and (not Path(args.frame_file).is_file()) and not args.frame_url:
        print(
            json.dumps(
                {
                    "skipped": True,
                    "reason": "frame_file_missing",
                    "frame_file": args.frame_file,
                }
            )
        )
        return 0

    score, candidate, img, roi_img = score_current_frame(args=args, roi=roi)

    # Optional second check gate: require agreement 10 minutes later before emailing.
    # This filters transient events (for example, something briefly on the bed).
    second_score: float | None = None
    second_candidate: str | None = None
    gate_passed = True
    gate_reason = "not_required"
    manual_step = ""
    manual_pair_passed: bool | None = None
    manual_pair_reason = ""
    manual_session_file = args.manual_session_file if args.manual_two_step else ""
    if args.manual_two_step:
        manual_step = "1_saved"
        max_age = max(1, int(args.manual_expire_sec))
        session = load_manual_session(args.manual_session_file)
        if session is None:
            save_manual_session(args.manual_session_file, score, candidate)
            print(
                json.dumps(
                    {
                        "score": score,
                        "candidate": candidate,
                        "second_score": None,
                        "second_candidate": None,
                        "email_gate_passed": False,
                        "email_gate_reason": "manual_step1_saved",
                        "manual_two_step": True,
                        "manual_step": manual_step,
                        "manual_pair_passed": None,
                        "manual_pair_reason": "saved_first_check",
                        "manual_session_file": args.manual_session_file,
                        "state": load_state(args.state_file).get("state", "unknown"),
                        "changed": False,
                        "emailed": False,
                        "prev_state": load_state(args.state_file).get("state", "unknown"),
                        "t_low": args.t_low,
                        "t_high": args.t_high,
                        "frame": str(frame_annotated) if args.save_debug_frames else "",
                    }
                )
            )
            return 0

        saved_at = int(session.get("saved_at", 0))
        age_sec = max(0, int(time.time()) - saved_at)
        if age_sec > max_age:
            clear_manual_session(args.manual_session_file)
            save_manual_session(args.manual_session_file, score, candidate)
            print(
                json.dumps(
                    {
                        "score": score,
                        "candidate": candidate,
                        "second_score": None,
                        "second_candidate": None,
                        "email_gate_passed": False,
                        "email_gate_reason": "manual_step1_saved",
                        "manual_two_step": True,
                        "manual_step": manual_step,
                        "manual_pair_passed": None,
                        "manual_pair_reason": "expired_previous_first_check",
                        "manual_session_file": args.manual_session_file,
                        "state": load_state(args.state_file).get("state", "unknown"),
                        "changed": False,
                        "emailed": False,
                        "prev_state": load_state(args.state_file).get("state", "unknown"),
                        "t_low": args.t_low,
                        "t_high": args.t_high,
                        "frame": str(frame_annotated) if args.save_debug_frames else "",
                    }
                )
            )
            return 0

        manual_step = "2_compared"
        second_score = float(session.get("score", 0.0))
        second_candidate = str(session.get("candidate", "indeterminate"))
        clear_manual_session(args.manual_session_file)

        manual_pair_passed = (
            candidate in {"made", "not_made"}
            and second_candidate in {"made", "not_made"}
            and candidate == second_candidate
        )
        gate_passed = bool(manual_pair_passed)
        if gate_passed:
            manual_pair_reason = "passed"
            gate_reason = "manual_passed"
        elif second_candidate not in {"made", "not_made"}:
            manual_pair_reason = "first_check_indeterminate"
            gate_reason = "manual_first_check_indeterminate"
        elif candidate not in {"made", "not_made"}:
            manual_pair_reason = "second_check_indeterminate"
            gate_reason = "manual_second_check_indeterminate"
        elif second_candidate != candidate:
            manual_pair_reason = "check_mismatch"
            gate_reason = "manual_check_mismatch"
        else:
            manual_pair_reason = "blocked_unknown"
            gate_reason = "manual_blocked_unknown"
    elif args.require_second_check_for_email:
        delay = max(0, int(args.double_check_delay_sec))
        if delay > 0:
            time.sleep(delay)
        second_score, second_candidate, _, _ = score_current_frame(args=args, roi=roi)
        gate_passed = (
            candidate in {"made", "not_made"}
            and second_candidate in {"made", "not_made"}
            and candidate == second_candidate
        )
        if gate_passed:
            gate_reason = "passed"
        elif candidate not in {"made", "not_made"}:
            gate_reason = "first_check_indeterminate"
        elif second_candidate not in {"made", "not_made"}:
            gate_reason = "second_check_indeterminate"
        elif candidate != second_candidate:
            gate_reason = "check_mismatch"
        else:
            gate_reason = "blocked_unknown"

    indeterminate_reasons = {
        "first_check_indeterminate",
        "second_check_indeterminate",
        "manual_first_check_indeterminate",
        "manual_second_check_indeterminate",
    }

    if args.manual_two_step and not gate_passed:
        state = load_state(args.state_file)
        prev_state = state.get("state", "unknown")
        if gate_reason in indeterminate_reasons:
            send_state_email_with_annotation(
                args=args,
                img=img,
                roi=roi,
                new_state="indeterminate",
                prev_state=prev_state,
                score=score,
            )
        print(
            json.dumps(
                {
                    "score": score,
                    "candidate": candidate,
                    "second_score": second_score,
                    "second_candidate": second_candidate,
                    "email_gate_passed": False,
                    "email_gate_reason": gate_reason,
                    "manual_two_step": True,
                    "manual_step": manual_step,
                    "manual_pair_passed": manual_pair_passed,
                    "manual_pair_reason": manual_pair_reason,
                    "manual_session_file": manual_session_file,
                    "state": state.get("state", "unknown"),
                    "changed": False,
                    "emailed": False,
                    "prev_state": prev_state,
                    "t_low": args.t_low,
                    "t_high": args.t_high,
                    "frame": str(frame_annotated) if args.save_debug_frames else "",
                }
            )
        )
        return 0

    state = load_state(args.state_file)
    prev_state = state.get("state", "unknown")
    changed, effective_state = maybe_transition(state, candidate, args.confirm_count)
    state["last_score"] = score
    state["last_candidate"] = candidate
    state["updated_at"] = int(time.time())
    save_state(args.state_file, state)

    # Send transition email as before, and also send same-state status emails
    # when the current check confidently classifies made/not_made.
    stable_state = state.get("state")
    send_same_state_status = (not changed) and (candidate in {"made", "not_made"}) and (candidate == stable_state)
    email_intended = bool(changed or send_same_state_status)
    send_indeterminate_status = (not gate_passed) and (gate_reason in indeterminate_reasons)

    if email_intended and not gate_passed:
        print(
            json.dumps(
                {
                    "email_blocked": True,
                    "reason": gate_reason,
                    "first_candidate": candidate,
                    "second_candidate": second_candidate,
                    "first_score": score,
                    "second_score": second_score,
                }
            )
        )

    if email_intended and gate_passed:
        send_state_email_with_annotation(
            args=args,
            img=img,
            roi=roi,
            new_state=effective_state,
            prev_state=prev_state,
            score=score,
        )
    elif send_indeterminate_status:
        send_state_email_with_annotation(
            args=args,
            img=img,
            roi=roi,
            new_state="indeterminate",
            prev_state=prev_state,
            score=score,
        )

    if args.save_debug_frames:
        ok, full_buf = cv2.imencode(".jpg", img)
        if not ok:
            raise RuntimeError("Failed to encode full frame for debug output.")
        frame_full.write_bytes(bytes(full_buf))
        ok = cv2.imwrite(str(frame_roi), roi_img)
        if not ok:
            raise RuntimeError(f"Failed to write ROI image: {frame_roi}")
        roi_for_annotated = scale_roi_to_frame(roi, img.shape[1], img.shape[0])
        annotated = draw_roi_outline(img, roi_for_annotated, args.pad)
        ok = cv2.imwrite(str(frame_annotated), annotated)
        if not ok:
            raise RuntimeError(f"Failed to write annotated image: {frame_annotated}")

    print(
        json.dumps(
            {
                "score": score,
                "candidate": candidate,
                "second_score": second_score,
                "second_candidate": second_candidate,
                "email_gate_passed": gate_passed,
                "email_gate_reason": gate_reason,
                "manual_two_step": args.manual_two_step,
                "manual_step": manual_step,
                "manual_pair_passed": manual_pair_passed,
                "manual_pair_reason": manual_pair_reason,
                "manual_session_file": manual_session_file,
                "state": state.get("state", "unknown"),
                "changed": changed,
                "emailed": bool((email_intended and gate_passed) or send_indeterminate_status),
                "prev_state": prev_state,
                "t_low": args.t_low,
                "t_high": args.t_high,
                "frame": str(frame_annotated) if args.save_debug_frames else "",
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - runtime path
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
