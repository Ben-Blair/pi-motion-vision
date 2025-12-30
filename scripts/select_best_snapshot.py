#!/usr/bin/env python3

import argparse
import cv2
import os
import math
import logging
import shutil
import tempfile
import time
import re

def parse_args():
    p = argparse.ArgumentParser(description="Select the best snapshot from Motion snapshots.")
    p.add_argument("--snapshot-dir", default="/var/lib/motion/snapshots")
    p.add_argument("--output-file", default="/var/lib/motion/best_snapshot.jpg")
    p.add_argument("--log-file", default="/var/lib/motion/motion_snapshot_selector.log")
    # Face detection (YuNet)
    p.add_argument("--yunet-model", default="/var/lib/motion/models/face_detection_yunet_2023mar.onnx")
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


args = parse_args()

# =========================
# Settings (overridable)
# =========================
SNAPSHOT_DIR = args.snapshot_dir
OUTPUT_FILE  = args.output_file
LOG_FILE     = args.log_file

# Sampling settings
SAMPLE_RATE = max(1, int(args.sample_rate))         # scan 1 every N images
FACE_SCAN_EXPAND = max(0, int(args.face_scan_expand))  # scan this many frames before/after face

DEBUG_WRITE_FRAMES = bool(args.debug_write_frames)
DEBUG_OUT_DIR = args.debug_out_dir
DEBUG_WAIT_MS = max(0, int(args.debug_wait_ms))
DEBUG_MAX_FRAMES = max(0, int(args.debug_max_frames))
_debug_written = 0

MIN_FACE_SIZE_PX = max(0, int(args.min_face_size_px))
MIN_FACE_AREA_RATIO = max(0.0, float(args.min_face_area_ratio))
REQUIRE_PERSON_FOR_FACE_TIER = bool(args.require_person_for_face_tier)

os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)

logging.info("=== Smart snapshot selector START ===")

# Face + People detectors
person_hog = cv2.HOGDescriptor()
person_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

face_cascade = cv2.CascadeClassifier(
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)

YUNET_MODEL = args.yunet_model
YUNET_SCORE_THRESHOLD = float(args.yunet_score_threshold)
YUNET_NMS_THRESHOLD = float(args.yunet_nms_threshold)
YUNET_TOPK = int(args.yunet_topk)

clahe = None
if args.clahe:
    try:
        clahe = cv2.createCLAHE(clipLimit=float(args.clahe_clip), tileGridSize=(int(args.clahe_grid), int(args.clahe_grid)))
    except Exception as e:
        logging.warning(f"Failed to init CLAHE, disabling: {e}")
        clahe = None

yunet = None
if hasattr(cv2, "FaceDetectorYN") and os.path.exists(YUNET_MODEL):
    try:
        # input size gets set per-image via setInputSize()
        yunet = cv2.FaceDetectorYN.create(
            YUNET_MODEL,
            "",
            (320, 320),
            YUNET_SCORE_THRESHOLD,
            YUNET_NMS_THRESHOLD,
            YUNET_TOPK,
        )
        logging.info(
            f"YuNet enabled model={YUNET_MODEL} score_thr={YUNET_SCORE_THRESHOLD} nms_thr={YUNET_NMS_THRESHOLD} topk={YUNET_TOPK}"
        )
    except Exception as e:
        logging.warning(f"Failed to init YuNet ({YUNET_MODEL}); falling back to Haar cascade. Error: {e}")
        yunet = None
else:
    if not hasattr(cv2, "FaceDetectorYN"):
        logging.warning("YuNet not available in this OpenCV build; falling back to Haar cascade")
    elif not os.path.exists(YUNET_MODEL):
        logging.warning(f"YuNet model not found at {YUNET_MODEL}; falling back to Haar cascade")

SNAPSHOT_RE = re.compile(r"^\d{8}-\d{6}-\d{2}\.jpg$")


# =========================
# Helper functions
# =========================
def load_img(path):
    for _ in range(5):
        img = cv2.imread(path)
        if img is not None:
            return img
        time.sleep(0.1)
    return None


def sharpness(img):
    return cv2.Laplacian(img, cv2.CV_64F).var()

def preprocess_for_face(img_bgr):
    """
    Apply mild contrast normalization to help IR/night snapshots.
    Returns (img_bgr_for_detector, gray_for_scoring).
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if clahe is not None:
        try:
            gray = clahe.apply(gray)
        except Exception:
            pass
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return bgr, gray

def detect_faces(img_bgr):
    """
    Returns a list of faces as tuples: (x, y, w, h, score)
    """
    h, w = img_bgr.shape[:2]
    if yunet is not None:
        try:
            yunet.setInputSize((w, h))
            _, faces = yunet.detect(img_bgr)
            if faces is None:
                return []
            out = []
            for f in faces:
                # YuNet format: [x, y, w, h, score, lmkx1, lmky1, ...]
                x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4])
                if score < YUNET_SCORE_THRESHOLD:
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
            logging.warning(f"YuNet detect failed, falling back to Haar for this frame: {e}")

    # Haar fallback
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    rects = face_cascade.detectMultiScale(gray, 1.3, 5)
    return [(int(x), int(y), int(fw), int(fh), 0.0) for (x, y, fw, fh) in rects]

def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)

def maybe_write_debug_frame(path, tier, score, idx=None):
    """
    Writes an annotated copy of the frame to DEBUG_OUT_DIR so you can watch
    what is being scored before snapshots/eventproc dirs get deleted.
    """
    global _debug_written
    if not DEBUG_WRITE_FRAMES:
        return
    if DEBUG_MAX_FRAMES > 0 and _debug_written >= DEBUG_MAX_FRAMES:
        return

    try:
        os.makedirs(DEBUG_OUT_DIR, exist_ok=True)
    except Exception:
        return

    img = cv2.imread(path)
    if img is None:
        return

    s = _safe_float(score, default=-1.0)
    label = f"T{tier} score={s:.3f}"

    # Draw tier/score banner
    cv2.rectangle(img, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
    cv2.putText(img, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2, cv2.LINE_AA)

    # Draw face boxes (helps verify YuNet is finding faces)
    try:
        det_img, _ = preprocess_for_face(img)
        faces = detect_faces(det_img)
        for (x, y, w, h, conf) in faces:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if conf > 0:
                # Some builds/models may report non-normalized scores; clamp for readability.
                conf_n = max(0.0, min(1.0, float(conf)))
                cv2.putText(img, f"{conf_n:.2f}", (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
    except Exception:
        pass

    base = os.path.basename(path)
    prefix = f"{int(idx):04d}_" if idx is not None else ""
    out_path = os.path.join(DEBUG_OUT_DIR, f"{prefix}{base}")
    try:
        cv2.imwrite(out_path, img)
        _debug_written += 1
        if DEBUG_WAIT_MS > 0:
            time.sleep(DEBUG_WAIT_MS / 1000.0)
    except Exception:
        pass


def score_image(path):
    img = load_img(path)
    if img is None:
        return (-1, -1.0)

    det_img, gray = preprocess_for_face(img)
    h, w = gray.shape

    faces = detect_faces(det_img)
    # Filter tiny faces (common false positives)
    if faces:
        frame_area = float(h * w)
        min_area = frame_area * MIN_FACE_AREA_RATIO
        faces = [
            (x, y, fw, fh, conf)
            for (x, y, fw, fh, conf) in faces
            if min(fw, fh) >= MIN_FACE_SIZE_PX and (fw * fh) >= min_area
        ]

    if len(faces) > 0:
        # Optionally require a person detection for Tier-2 to avoid static/poster faces.
        # NOTE: HOG can miss sometimes; if it does, we downgrade this frame instead of promoting it.
        if REQUIRE_PERSON_FOR_FACE_TIER:
            people, _ = person_hog.detectMultiScale(img)
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
        # Prefer closer faces
        score += face_area * 0.03
        # Prefer sharp faces
        score += sharpness(face_patch) * 0.8
        # Prefer higher-confidence detections (YuNet)
        score += conf_n * 80.0
        # Avoid extreme exposure (helps IR glare / underexposure)
        m = float(face_patch.mean()) if face_patch.size else 0.0
        if m < 40.0:
            score -= (40.0 - m) * 1.0
        elif m > 220.0:
            score -= (m - 220.0) * 1.0
        cx, cy = x + fw / 2, y + fh / 2
        dist = math.hypot(cx - w / 2, cy - h / 2)
        score -= dist * 0.2
        return (tier, score)

    # Only run heavier people detection if no face found
    people, _ = person_hog.detectMultiScale(img)
    if len(people) > 0:
        tier = 1
        score = sharpness(gray) * 0.1
        return (tier, score)

    tier = 0
    score = min(sharpness(gray) * 0.02, 12.0)
    return (tier, score)


# =========================
# MAIN
# =========================
files = sorted(
    os.path.join(SNAPSHOT_DIR, f)
    for f in os.listdir(SNAPSHOT_DIR)
    if SNAPSHOT_RE.match(f)
)

if not files:
    logging.warning("No snapshots found")
    exit(0)

logging.info(f"Loaded {len(files)} snapshots")

face_detected_frames = set()
scores = {}

# Pass 1: Sparse scan
for i, path in enumerate(files):
    # ---- Sample rate ----
    if i % SAMPLE_RATE != 0:
        continue

    tier, sc = score_image(path)
    scores[path] = (tier, sc)

    logging.info(f"SAMPLED {path} T{tier} score={sc}")
    maybe_write_debug_frame(path, tier, sc, idx=i)

    if tier == 2:
        face_detected_frames.add(i)


# Pass 2: Scan around face detections
for idx in face_detected_frames:
    start = max(0, idx - FACE_SCAN_EXPAND)
    end = min(len(files), idx + FACE_SCAN_EXPAND + 1)
    for j in range(start, end):
        p = files[j]
        if p not in scores:
            scores[p] = score_image(p)
            logging.info(f"EXPANDED {p} score={scores[p]}")
            try:
                t2, s2 = scores[p]
                maybe_write_debug_frame(p, t2, s2, idx=j)
            except Exception:
                pass


# =========================
# Select best
# =========================
best_file = None
best_score = (-1, -1.0)

for p, s in scores.items():
    if s > best_score:
        best_file = p
        best_score = s

if best_file:
    try:
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(OUTPUT_FILE),
            delete=False
        ) as tmp:
            shutil.copy2(best_file, tmp.name)
            os.replace(tmp.name, OUTPUT_FILE)

        logging.info(f"BEST={best_file} score={best_score}")

    except Exception as e:
        logging.exception(f"Failed to write best snapshot: {e}")
else:
    logging.warning("No valid snapshot selected")

logging.info("=== Smart snapshot selector DONE ===")
