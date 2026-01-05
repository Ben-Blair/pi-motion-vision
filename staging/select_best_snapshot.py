#!/usr/bin/env python3

import argparse
import cv2
import json
import os
import math
import logging
import shutil
import tempfile
import time
import re
import sqlite3
from typing import Optional, Tuple

# Filename format from Motion: %Y%m%d-%H%M%S-%q.jpg (e.g. 20260102-123456-01.jpg)
SNAPSHOT_RE = re.compile(r"^\d{8}-\d{6}-\d{2}\.jpg$")

LOG_DIR = "/dev/shm/motion_logs"  # tmpfs log directory (reduces SD card writes)
DEFAULT_LOG_FILE = os.path.join(LOG_DIR, "motion_snapshot_selector.log")
DEFAULT_YUNET_MODEL = "/var/lib/motion/models/face_detection_yunet_2023mar.onnx"


def parse_args():
    p = argparse.ArgumentParser(description="Select the best snapshot from Motion snapshots.")
    p.add_argument("--snapshot-dir", default="/var/lib/motion/snapshots")
    p.add_argument("--output-file", default="/var/lib/motion/best_snapshot.jpg")
    p.add_argument("--log-file", default=DEFAULT_LOG_FILE)

    # Cache (optional)
    p.add_argument(
        "--cache-db",
        default=None,
        help="Optional sqlite DB to reuse cached (tier,score) per snapshot basename.",
    )

    # Face detection (YuNet)
    p.add_argument("--yunet-model", default=DEFAULT_YUNET_MODEL)
    p.add_argument("--yunet-score-threshold", type=float, default=0.5)
    p.add_argument("--yunet-nms-threshold", type=float, default=0.3)
    p.add_argument("--yunet-topk", type=int, default=5000)

    # Pre-processing (helps IR/night contrast)
    p.add_argument("--clahe", action="store_true", default=True)
    p.add_argument("--no-clahe", dest="clahe", action="store_false")
    p.add_argument("--clahe-clip", type=float, default=2.0)
    p.add_argument("--clahe-grid", type=int, default=8)

    # Sampling settings
    p.add_argument("--sample-rate", type=int, default=2, help="Scan 1 every N images (lower = more accurate, higher = faster)")
    p.add_argument("--face-scan-expand", type=int, default=8, help="Scan this many frames before/after a detected face frame")

    # Face filtering / robustness
    p.add_argument("--min-face-size-px", type=int, default=60,
                   help="Ignore detected faces smaller than this (min(width,height) in pixels)")
    p.add_argument("--min-face-area-ratio", type=float, default=0.002,
                   help="Ignore detected faces smaller than this fraction of the full frame area")
    p.add_argument("--require-person-for-face-tier", action="store_true", default=False,
                   help="Only treat a frame as Tier-2 if a person is also detected (stricter)")
    p.add_argument("--no-require-person-for-face-tier", dest="require_person_for_face_tier", action="store_false",
                   help="Allow Tier-2 even if person detection fails (less strict, default)")

    # Debug (frame-by-frame scoring visibility)
    p.add_argument("--debug-write-frames", action="store_true", default=False,
                   help="Write annotated copies of scored frames to --debug-out-dir (for live viewing)")
    p.add_argument("--debug-out-dir", default="/var/lib/motion/debug_scoring",
                   help="Directory to write annotated debug frames")
    p.add_argument("--debug-wait-ms", type=int, default=0,
                   help="Optional delay after writing each debug frame (0 = no delay)")
    p.add_argument("--debug-max-frames", type=int, default=600,
                   help="Safety cap to prevent filling disk")
    return p.parse_args()


def load_img(path: str):
    # Motion may still be writing; retry briefly.
    for _ in range(5):
        img = cv2.imread(path)
        if img is not None:
            return img
        time.sleep(0.1)
    return None


def sharpness(img_gray):
    return cv2.Laplacian(img_gray, cv2.CV_64F).var()


class Scorer:
    def __init__(
        self,
        *,
        yunet_model: str,
        yunet_score_threshold: float,
        yunet_nms_threshold: float,
        yunet_topk: int,
        clahe_enabled: bool,
        clahe_clip: float,
        clahe_grid: int,
        min_face_size_px: int,
        min_face_area_ratio: float,
        require_person_for_face_tier: bool,
        logger: logging.Logger,
    ):
        self.logger = logger
        self.min_face_size_px = max(0, int(min_face_size_px))
        self.min_face_area_ratio = max(0.0, float(min_face_area_ratio))
        self.require_person_for_face_tier = bool(require_person_for_face_tier)

        # People detector (HOG)
        self.person_hog = cv2.HOGDescriptor()
        self.person_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

        # Haar fallback
        self.face_cascade = cv2.CascadeClassifier(
            "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
        )

        # CLAHE (optional)
        self.clahe = None
        if clahe_enabled:
            try:
                self.clahe = cv2.createCLAHE(
                    clipLimit=float(clahe_clip),
                    tileGridSize=(int(clahe_grid), int(clahe_grid)),
                )
            except Exception as e:
                self.logger.warning(f"Failed to init CLAHE, disabling: {e}")
                self.clahe = None

        # YuNet (optional)
        self.yunet = None
        self.yunet_score_threshold = float(yunet_score_threshold)
        self.yunet_nms_threshold = float(yunet_nms_threshold)
        self.yunet_topk = int(yunet_topk)
        self.yunet_model = yunet_model

        if hasattr(cv2, "FaceDetectorYN") and os.path.exists(yunet_model):
            try:
                self.yunet = cv2.FaceDetectorYN.create(
                    yunet_model,
                    "",
                    (320, 320),
                    self.yunet_score_threshold,
                    self.yunet_nms_threshold,
                    self.yunet_topk,
                )
                self.logger.info(
                    f"YuNet enabled model={yunet_model} score_thr={self.yunet_score_threshold} nms_thr={self.yunet_nms_threshold} topk={self.yunet_topk}"
                )
            except Exception as e:
                self.logger.warning(f"Failed to init YuNet ({yunet_model}); falling back to Haar cascade. Error: {e}")
                self.yunet = None
        else:
            if not hasattr(cv2, "FaceDetectorYN"):
                self.logger.warning("YuNet not available in this OpenCV build; falling back to Haar cascade")
            elif not os.path.exists(yunet_model):
                self.logger.warning(f"YuNet model not found at {yunet_model}; falling back to Haar cascade")

    def preprocess_for_face(self, img_bgr):
        """
        Apply mild contrast normalization to help IR/night snapshots.
        Returns (img_bgr_for_detector, gray_for_scoring).
        """
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        if self.clahe is not None:
            try:
                gray = self.clahe.apply(gray)
            except Exception:
                pass
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return bgr, gray

    def detect_faces(self, img_bgr):
        """
        Returns a list of faces as tuples: (x, y, w, h, score)
        """
        h, w = img_bgr.shape[:2]
        if self.yunet is not None:
            try:
                self.yunet.setInputSize((w, h))
                _, faces = self.yunet.detect(img_bgr)
                if faces is None:
                    return []
                out = []
                for f in faces:
                    x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4])
                    if score < self.yunet_score_threshold:
                        continue
                    xi, yi, wi, hi = int(max(0, x)), int(max(0, y)), int(max(0, fw)), int(max(0, fh))
                    if wi <= 0 or hi <= 0:
                        continue
                    if xi >= w or yi >= h:
                        continue
                    wi = min(w - xi, wi)
                    hi = min(h - yi, hi)
                    out.append((xi, yi, wi, hi, score))
                return out
            except Exception as e:
                self.logger.warning(f"YuNet detect failed, falling back to Haar for this frame: {e}")

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        rects = self.face_cascade.detectMultiScale(gray, 1.3, 5)
        return [(int(x), int(y), int(fw), int(fh), 0.0) for (x, y, fw, fh) in rects]


def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)


def score_image(path: str, scorer: Scorer) -> Tuple[int, float]:
    img = load_img(path)
    if img is None:
        return (-1, -1.0)

    det_img, gray = scorer.preprocess_for_face(img)
    h, w = gray.shape

    faces = scorer.detect_faces(det_img)
    if faces:
        frame_area = float(h * w)
        min_area = frame_area * scorer.min_face_area_ratio
        faces = [
            (x, y, fw, fh, conf)
            for (x, y, fw, fh, conf) in faces
            if min(fw, fh) >= scorer.min_face_size_px and (fw * fh) >= min_area
        ]

    if len(faces) > 0:
        if scorer.require_person_for_face_tier:
            people, _ = scorer.person_hog.detectMultiScale(img)
            if len(people) == 0:
                tier = 1
                score = sharpness(gray) * 0.1
                return (tier, score)

        tier = 2
        x, y, fw, fh, conf = max(faces, key=lambda f: f[2] * f[3])
        face_area = fw * fh
        face_patch = gray[y:y + fh, x:x + fw]

        conf_n = max(0.0, min(1.0, float(conf)))

        score = 0.0
        score += face_area * 0.03
        score += sharpness(face_patch) * 0.8
        score += conf_n * 80.0

        m = float(face_patch.mean()) if face_patch.size else 0.0
        if m < 40.0:
            score -= (40.0 - m) * 1.0
        elif m > 220.0:
            score -= (m - 220.0) * 1.0

        cx, cy = x + fw / 2, y + fh / 2
        dist = math.hypot(cx - w / 2, cy - h / 2)
        score -= dist * 0.2
        return (tier, score)

    people, _ = scorer.person_hog.detectMultiScale(img)
    if len(people) > 0:
        tier = 1
        score = sharpness(gray) * 0.1
        return (tier, score)

    tier = 0
    score = min(sharpness(gray) * 0.02, 12.0)
    return (tier, score)


def score_image_with_bbox(path: str, scorer: Scorer) -> Tuple[int, float, Optional[Tuple[int, int, int, int, float]]]:
    """
    Like score_image(), but also returns the 'best' face bbox used for Tier-2 decisions.
    Returns (tier, score, bbox) where bbox is (x,y,w,h,conf) or None.
    """
    img = load_img(path)
    if img is None:
        return (-1, -1.0, None)

    det_img, gray = scorer.preprocess_for_face(img)
    h, w = gray.shape

    faces = scorer.detect_faces(det_img)
    if faces:
        frame_area = float(h * w)
        min_area = frame_area * scorer.min_face_area_ratio
        faces = [
            (x, y, fw, fh, conf)
            for (x, y, fw, fh, conf) in faces
            if min(fw, fh) >= scorer.min_face_size_px and (fw * fh) >= min_area
        ]

    if len(faces) > 0:
        if scorer.require_person_for_face_tier:
            people, _ = scorer.person_hog.detectMultiScale(img)
            if len(people) == 0:
                tier = 1
                score = sharpness(gray) * 0.1
                return (tier, score, None)

        tier = 2
        x, y, fw, fh, conf = max(faces, key=lambda f: f[2] * f[3])
        face_area = fw * fh
        face_patch = gray[y:y + fh, x:x + fw]

        conf_n = max(0.0, min(1.0, float(conf)))

        score = 0.0
        score += face_area * 0.03
        score += sharpness(face_patch) * 0.8
        score += conf_n * 80.0

        m = float(face_patch.mean()) if face_patch.size else 0.0
        if m < 40.0:
            score -= (40.0 - m) * 1.0
        elif m > 220.0:
            score -= (m - 220.0) * 1.0

        cx, cy = x + fw / 2, y + fh / 2
        dist = math.hypot(cx - w / 2, cy - h / 2)
        score -= dist * 0.2
        return (tier, score, (int(x), int(y), int(fw), int(fh), float(conf)))

    people, _ = scorer.person_hog.detectMultiScale(img)
    if len(people) > 0:
        tier = 1
        score = sharpness(gray) * 0.1
        return (tier, score, None)

    tier = 0
    score = min(sharpness(gray) * 0.02, 12.0)
    return (tier, score, None)


def maybe_write_debug_frame(
    path: str,
    tier: int,
    score: float,
    scorer: Scorer,
    *,
    debug_out_dir: str,
    debug_wait_ms: int,
    debug_max_frames: int,
    idx: Optional[int] = None,
    debug_written_ref: Optional[list] = None,
):
    if debug_written_ref is None:
        debug_written_ref = [0]

    if debug_max_frames > 0 and debug_written_ref[0] >= debug_max_frames:
        return

    try:
        os.makedirs(debug_out_dir, exist_ok=True)
    except Exception:
        return

    img = cv2.imread(path)
    if img is None:
        return

    s = _safe_float(score, default=-1.0)
    label = f"T{tier} score={s:.3f}"

    cv2.rectangle(img, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
    cv2.putText(img, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2, cv2.LINE_AA)

    try:
        det_img, _ = scorer.preprocess_for_face(img)
        faces = scorer.detect_faces(det_img)
        for (x, y, w, h, conf) in faces:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if conf > 0:
                conf_n = max(0.0, min(1.0, float(conf)))
                cv2.putText(img, f"{conf_n:.2f}", (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
    except Exception:
        pass

    base = os.path.basename(path)
    prefix = f"{int(idx):04d}_" if idx is not None else ""
    out_path = os.path.join(debug_out_dir, f"{prefix}{base}")
    try:
        cv2.imwrite(out_path, img)
        debug_written_ref[0] += 1
        if debug_wait_ms > 0:
            time.sleep(max(0.0, float(debug_wait_ms) / 1000.0))
    except Exception:
        pass


def atomic_write_json(path: str, payload: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path) or ".", delete=False, mode="w") as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        os.replace(tmp.name, path)


def write_annotated_best(src_path: str, tier: int, score: float, scorer: Scorer, out_path: str) -> bool:
    """
    Always write an annotated copy of the chosen best frame (independent of debug caps),
    so downstream systems can attach a deterministic 'best_snapshot.annotated.jpg'.
    """
    try:
        img = cv2.imread(src_path)
        if img is None:
            return False

        label = f"T{int(tier)} score={float(score):.3f}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
        cv2.putText(img, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2, cv2.LINE_AA)

        try:
            det_img, _ = scorer.preprocess_for_face(img)
            faces = scorer.detect_faces(det_img)
            for (x, y, w, h, conf) in faces:
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                if conf > 0:
                    conf_n = max(0.0, min(1.0, float(conf)))
                    cv2.putText(img, f"{conf_n:.2f}", (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
        except Exception:
            pass

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp_dir = os.path.dirname(out_path) or "."
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False, suffix=".jpg") as tmp:
            tmp_name = tmp.name
        try:
            if not cv2.imwrite(tmp_name, img):
                try:
                    os.unlink(tmp_name)
                except Exception:
                    pass
                return False
            os.replace(tmp_name, out_path)
            return True
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
    except Exception:
        return False


def open_cache(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=2.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scores (
          basename TEXT PRIMARY KEY,
          tier INTEGER NOT NULL,
          score REAL NOT NULL,
          updated_ts REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def cache_get(conn: sqlite3.Connection, basename: str) -> Optional[Tuple[int, float]]:
    row = conn.execute(
        "SELECT tier, score FROM scores WHERE basename = ?",
        (basename,),
    ).fetchone()
    if not row:
        return None
    return (int(row[0]), float(row[1]))


def cache_put(conn: sqlite3.Connection, basename: str, tier: int, score: float):
    conn.execute(
        "INSERT OR REPLACE INTO scores (basename, tier, score, updated_ts) VALUES (?, ?, ?, ?)",
        (basename, int(tier), float(score), float(time.time())),
    )
    conn.commit()


def main():
    args = parse_args()

    os.makedirs(os.path.dirname(args.log_file) or ".", exist_ok=True)
    logging.basicConfig(
        filename=args.log_file,
        level=logging.INFO,
        format="%(asctime)s %(message)s"
    )
    log = logging.getLogger("select_best_snapshot")
    log.info("=== Smart snapshot selector START ===")

    # Settings
    sample_rate = max(1, int(args.sample_rate))
    face_scan_expand = max(0, int(args.face_scan_expand))

    cache_conn = None
    if args.cache_db:
        try:
            cache_conn = open_cache(args.cache_db)
            log.info(f"Cache enabled db={args.cache_db}")
        except Exception as e:
            log.warning(f"Failed to open cache db ({args.cache_db}); continuing without cache. Error: {e}")
            cache_conn = None

    scorer = Scorer(
        yunet_model=args.yunet_model,
        yunet_score_threshold=args.yunet_score_threshold,
        yunet_nms_threshold=args.yunet_nms_threshold,
        yunet_topk=args.yunet_topk,
        clahe_enabled=bool(args.clahe),
        clahe_clip=float(args.clahe_clip),
        clahe_grid=int(args.clahe_grid),
        min_face_size_px=int(args.min_face_size_px),
        min_face_area_ratio=float(args.min_face_area_ratio),
        require_person_for_face_tier=bool(args.require_person_for_face_tier),
        logger=log,
    )

    files = sorted(
        os.path.join(args.snapshot_dir, f)
        for f in os.listdir(args.snapshot_dir)
        if SNAPSHOT_RE.match(f)
    )

    if not files:
        log.warning("No snapshots found")
        return 0

    log.info(f"Loaded {len(files)} snapshots")

    face_detected_frames = set()
    scores = {}
    debug_written_ref = [0]

    def score_path(path: str) -> Tuple[int, float]:
        base = os.path.basename(path)
        if cache_conn is not None:
            cached = cache_get(cache_conn, base)
            if cached is not None:
                return cached
        tier, sc = score_image(path, scorer)
        if cache_conn is not None and tier >= 0:
            try:
                cache_put(cache_conn, base, tier, sc)
            except Exception:
                pass
        return (tier, sc)

    # Pass 1: Sparse scan
    for i, path in enumerate(files):
        if i % sample_rate != 0:
            continue
        tier, sc = score_path(path)
        scores[path] = (tier, sc)
        log.info(f"SAMPLED {path} T{tier} score={sc}")
        if args.debug_write_frames:
            maybe_write_debug_frame(
                path,
                tier,
                sc,
                scorer,
                debug_out_dir=args.debug_out_dir,
                debug_wait_ms=int(args.debug_wait_ms),
                debug_max_frames=int(args.debug_max_frames),
                idx=i,
                debug_written_ref=debug_written_ref,
            )
        if tier == 2:
            face_detected_frames.add(i)

    # Pass 2: Scan around face detections
    for idx in face_detected_frames:
        start = max(0, idx - face_scan_expand)
        end = min(len(files), idx + face_scan_expand + 1)
        for j in range(start, end):
            pth = files[j]
            if pth not in scores:
                tier, sc = score_path(pth)
                scores[pth] = (tier, sc)
                log.info(f"EXPANDED {pth} T{tier} score={sc}")
                if args.debug_write_frames:
                    maybe_write_debug_frame(
                        pth,
                        tier,
                        sc,
                        scorer,
                        debug_out_dir=args.debug_out_dir,
                        debug_wait_ms=int(args.debug_wait_ms),
                        debug_max_frames=int(args.debug_max_frames),
                        idx=j,
                        debug_written_ref=debug_written_ref,
                    )

    # Select best
    best_file = None
    best_score = (-1, -1.0)
    for pth, s in scores.items():
        if s > best_score:
            best_file = pth
            best_score = s

    if best_file:
        try:
            os.makedirs(os.path.dirname(args.output_file) or ".", exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output_file), delete=False) as tmp:
                shutil.copy2(best_file, tmp.name)
                os.replace(tmp.name, args.output_file)
            log.info(f"BEST={best_file} score={best_score}")

            # Also write deterministic metadata + annotated best snapshot for downstream consumers (email/debugging).
            try:
                tier_i = int(best_score[0])
                score_f = float(best_score[1])
                meta_path = args.output_file + ".meta.json"
                ann_path = args.output_file + ".annotated.jpg"
                payload = {
                    "best_source_file": str(best_file),
                    "best_source_basename": os.path.basename(str(best_file)),
                    "tier": tier_i,
                    "score": score_f,
                    "output_file": str(args.output_file),
                    "annotated_file": str(ann_path),
                    "ts": float(time.time()),
                }
                atomic_write_json(meta_path, payload)
                if not write_annotated_best(str(best_file), tier_i, score_f, scorer, ann_path):
                    log.warning(f"Failed to write annotated best snapshot to {ann_path}")
            except Exception as e:
                log.exception(f"Failed to write best metadata/annotated snapshot: {e}")
        except Exception as e:
            log.exception(f"Failed to write best snapshot: {e}")
            return 1
    else:
        log.warning("No valid snapshot selected")

    log.info("=== Smart snapshot selector DONE ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


