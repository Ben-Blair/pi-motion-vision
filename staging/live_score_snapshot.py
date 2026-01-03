#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import shutil
import tempfile
import time

# Ensure we can import the sibling selector module when installed into /usr/local/bin.
import sys
sys.path.insert(0, os.path.dirname(__file__))

import select_best_snapshot as sbs  # noqa: E402


SEQ_RE = re.compile(r"-(\d+)\.jpg$")


def parse_args():
    p = argparse.ArgumentParser(description="Live-score a single Motion snapshot and cache the result.")
    p.add_argument("--snapshot-file", required=True)
    p.add_argument("--cache-db", required=True)
    p.add_argument("--log-file", default="/var/lib/motion/live_snapshot_scoring.log")
    p.add_argument("--sample-mod", type=int, default=2, help="Score only frames where (seq % sample_mod) == sample_offset")
    p.add_argument("--sample-offset", type=int, default=1, help="See --sample-mod (default scores 01,03,05,...)")
    return p.parse_args()


def should_score(basename: str, sample_mod: int, sample_offset: int) -> bool:
    sample_mod = max(1, int(sample_mod))
    sample_offset = int(sample_offset) % sample_mod
    if sample_mod == 1:
        return True
    m = SEQ_RE.search(basename)
    if not m:
        # If we can't parse the sequence, score it to be safe.
        return True
    try:
        seq = int(m.group(1))
    except Exception:
        return True
    return (seq % sample_mod) == sample_offset


def atomic_write_json(path: str, payload: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path) or ".", delete=False, mode="w") as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        os.replace(tmp.name, path)


def atomic_copy(src: str, dst: str):
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(dst) or ".", delete=False) as tmp:
        shutil.copy2(src, tmp.name)
        os.replace(tmp.name, dst)


def main():
    args = parse_args()

    os.makedirs(os.path.dirname(args.log_file) or ".", exist_ok=True)
    logging.basicConfig(
        filename=args.log_file,
        level=logging.INFO,
        format="%(asctime)s %(message)s",
    )
    log = logging.getLogger("live_score_snapshot")

    snapshot_file = args.snapshot_file
    basename = os.path.basename(snapshot_file)

    if not sbs.SNAPSHOT_RE.match(basename):
        # Ignore non-snapshot artifacts.
        return 0

    if not should_score(basename, args.sample_mod, args.sample_offset):
        return 0

    try:
        conn = sbs.open_cache(args.cache_db)
    except Exception as e:
        log.warning(f"Failed to open cache db ({args.cache_db}): {e}")
        return 0

    # Skip if already scored
    try:
        cached = sbs.cache_get(conn, basename)
        if cached is not None:
            return 0
    except Exception:
        pass

    scorer = sbs.Scorer(
        yunet_model=sbs.DEFAULT_YUNET_MODEL,
        yunet_score_threshold=0.5,
        yunet_nms_threshold=0.3,
        yunet_topk=5000,
        clahe_enabled=True,
        clahe_clip=2.0,
        clahe_grid=8,
        min_face_size_px=60,
        min_face_area_ratio=0.002,
        require_person_for_face_tier=False,
        logger=log,
    )

    tier, score = sbs.score_image(snapshot_file, scorer)
    if tier < 0:
        return 0

    try:
        sbs.cache_put(conn, basename, tier, score)
    except Exception as e:
        log.warning(f"Failed to write cache row for {basename}: {e}")

    # Maintain best-so-far artifacts adjacent to the cache db.
    base_no_ext = os.path.splitext(args.cache_db)[0]
    best_json = base_no_ext + ".best.json"
    best_jpg = base_no_ext + ".best.jpg"

    # Compare against current best by scanning cache (cheap, 1-row query would be nicer, but keep minimal).
    try:
        row = conn.execute(
            "SELECT basename, tier, score FROM scores ORDER BY tier DESC, score DESC LIMIT 1"
        ).fetchone()
    except Exception:
        row = None

    if row:
        best_base, best_tier, best_score = str(row[0]), int(row[1]), float(row[2])
        if best_base == basename:
            try:
                atomic_copy(snapshot_file, best_jpg)
                atomic_write_json(
                    best_json,
                    {
                        "basename": basename,
                        "tier": tier,
                        "score": score,
                        "snapshot_file": snapshot_file,
                        "updated_ts": time.time(),
                    },
                )
            except Exception:
                pass

    log.info(f"SCORED {snapshot_file} T{tier} score={score}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




