#!/usr/bin/env python3
import cv2
import numpy as np
import os
import glob
import math
import logging
import shutil
import tempfile

# =========================
# Paths (match your architecture)
# =========================
SNAPSHOT_DIR = "/dev/shm/motion"                 # RAM snapshots
OUTPUT_FILE = "/var/lib/motion/best_snapshot.jpg"
LOG_FILE = "/var/lib/motion/motion_snapshot_selector.log"

# Ensure log directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)

# =========================
# Initialize detectors
# =========================
person_hog = cv2.HOGDescriptor()
person_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

face_cascade = cv2.CascadeClassifier(
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)

# =========================
# Scoring helpers
# =========================
def sharpness(img):
    return cv2.Laplacian(img, cv2.CV_64F).var()

def score_image(path):
    img = cv2.imread(path)
    if img is None:
        return -1

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    score = 0

    # Require a person
    people, _ = person_hog.detectMultiScale(img, winStride=(8, 8))
    if len(people) == 0:
        return -1
    score += 100

    # Face detection bonus
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    if len(faces) == 0:
        return score

    # Largest face
    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    score += (fw * fh) * 0.01

    # Face sharpness
    face = gray[y:y + fh, x:x + fw]
    score += sharpness(face) * 0.5

    # Centering penalty
    cx, cy = x + fw / 2, y + fh / 2
    dist = math.hypot(cx - w / 2, cy - h / 2)
    score -= dist * 0.1

    return score

# =========================
# Main selection logic
# =========================
best_score = -1
best_file = None

files = glob.glob(os.path.join(SNAPSHOT_DIR, "*.jpg"))
logging.info(f"Found {len(files)} snapshot files in RAM")

for path in files:
    s = score_image(path)
    logging.info(f"{path} score={s}")

    if s > best_score:
        best_score = s
        best_file = path

# =========================
# Write result atomically
# =========================
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
    logging.info("No suitable snapshot found")
