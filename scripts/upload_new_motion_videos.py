#!/usr/bin/env python3
"""Upload every new motion MP4 via upload_video_to_supabase.upload_mp4_path.

One-shot (best for Motion on_movie_end — pass the finalized file path):

  source etc/supabase.env && .venv/bin/python scripts/upload_new_motion_videos.py /path/to/clip.mp4

Watch a directory (polls for stable new .mp4 files; tracks uploaded names in a state file):

  source etc/supabase.env && .venv/bin/python scripts/upload_new_motion_videos.py --watch --dir /path/to/target_dir

Hook example (non-blocking) at end of on_movie_end.sh after the file exists:

  [ "${SUPABASE_UPLOAD:-0}" = 1 ] && .venv/bin/python /path/to/repo/scripts/upload_new_motion_videos.py "$MOVIE_FILE" >>/var/log/supabase-upload.log 2>&1 &
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from upload_video_to_supabase import upload_mp4_path


def _default_watch_dir() -> Path:
    d = os.environ.get("MOTION_VIDEO_DIR", "").strip()
    if d:
        return Path(d).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "motion-videos"


def _default_state_file() -> Path:
    p = os.environ.get("MOTION_UPLOAD_STATE", "").strip()
    if p:
        return Path(p).expanduser().resolve()
    return Path("/var/lib/motion/supabase_uploaded.log")


def _load_state(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def _save_state(path: Path, names: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(names)) + "\n")


def _file_stable(path: Path) -> bool:
    try:
        a = path.stat().st_size
    except OSError:
        return False
    if a == 0:
        return False
    time.sleep(0.35)
    try:
        b = path.stat().st_size
    except OSError:
        return False
    return a == b


def _watch(watch_dir: Path, state_file: Path, interval: float) -> None:
    uploaded = _load_state(state_file)
    while True:
        for p in sorted(watch_dir.glob("*.mp4")):
            if p.name in uploaded:
                continue
            if not _file_stable(p):
                continue
            try:
                upload_mp4_path(p)
            except Exception as e:
                print(f"{p}: {e}", file=sys.stderr)
                continue
            uploaded.add(p.name)
            _save_state(state_file, uploaded)
        time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "video",
        nargs="?",
        type=Path,
        help="single MP4 path (e.g. Motion on_movie_end %%f)",
    )
    ap.add_argument(
        "--watch",
        action="store_true",
        help="poll --dir for new .mp4 files instead of uploading one path",
    )
    ap.add_argument(
        "--dir",
        type=Path,
        help="folder to watch (default: MOTION_VIDEO_DIR or repo motion-videos)",
    )
    ap.add_argument(
        "--state-file",
        type=Path,
        help="append-only list of uploaded basenames (default: MOTION_UPLOAD_STATE or /var/lib/motion/supabase_uploaded.log)",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between scans in --watch mode (default: 10)",
    )
    args = ap.parse_args()

    if args.watch:
        d = (args.dir or _default_watch_dir()).resolve()
        if not d.is_dir():
            sys.exit(f"not a directory: {d}")
        state = (args.state_file or _default_state_file()).resolve()
        _watch(d, state, max(args.interval, 1.0))
        return

    if not args.video:
        ap.error("pass a video path, or use --watch")
    try:
        upload_mp4_path(args.video.expanduser().resolve())
    except ValueError as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
