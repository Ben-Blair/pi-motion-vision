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
    return p.parse_args()


args = parse_args()

# =========================
# Settings (overridable)
# =========================
SNAPSHOT_DIR = args.snapshot_dir
OUTPUT_FILE  = args.output_file
LOG_FILE     = args.log_file

# Sampling settings
SAMPLE_RATE = 5        # scan 1 every N images
FACE_SCAN_EXPAND = 6   # scan this many frames before/after face

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


def score_image(path):
    img = load_img(path)
    if img is None:
        return (-1, -1.0)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    people, _ = person_hog.detectMultiScale(img)

    if len(faces) > 0:
        tier = 2
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        face_area = fw * fh
        score = face_area * 0.03
        score += sharpness(gray[y:y + fh, x:x + fw]) * 0.8
        cx, cy = x + fw / 2, y + fh / 2
        dist = math.hypot(cx - w / 2, cy - h / 2)
        score -= dist * 0.2
        return (tier, score)

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
