#!/usr/bin/env bash
#
# Capture JPEG frames from Motion's HTTP endpoint into a dataset directory.
#
# Typical usage (on the Pi):
#   ./scripts/bed_capture_frames.sh \
#     --out "/home/bblair23/bed-dataset/train/healthy" \
#     --url "http://127.0.0.1:8080/substream" \
#     --count 200 \
#     --interval 5
#
# Test captures:
#   --out "/home/bblair23/bed-dataset/test/healthy"
#   --out "/home/bblair23/bed-dataset/test/anomalous"
#
# Notes:
# - This grabs "current.jpg" directly from Motion (no browser screenshots).
# - Keep the camera fixed; ROI cropping comes later.
#
set -euo pipefail

OUT_DIR=""
# Default to /substream (reliably emits MJPEG frames with curl on Motion 4.x).
URL="http://127.0.0.1:8080/substream"
COUNT=50
INTERVAL=5
TIMEOUT=10

usage() {
  cat <<'EOF'
Usage:
  bed_capture_frames.sh --out OUT_DIR [--url URL] [--count N] [--interval SECONDS] [--timeout SECONDS]

Options:
  --out       Output directory (required)
  --url       Motion endpoint to fetch (default: http://127.0.0.1:8080/)
              Recommended URLs (try in this order):
                - http://127.0.0.1:8080/substream   (MJPEG stream; script extracts first JPEG frame)
                - http://127.0.0.1:8080/motion      (MJPEG stream; motion-image variant)
                - http://127.0.0.1:8080/stream      (MJPEG stream; sometimes disabled)
  --count     Number of images to capture (default: 50)
  --interval  Seconds between captures (default: 5)
  --timeout   Curl timeout seconds (default: 10)

Examples:
  ./scripts/bed_capture_frames.sh --out "/home/bblair23/bed-dataset/train/healthy" --count 300 --interval 4
  ./scripts/bed_capture_frames.sh --out "/home/bblair23/bed-dataset/test/anomalous" --count 30 --interval 6
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --url) URL="${2:-}"; shift 2 ;;
    --count) COUNT="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$OUT_DIR" ]; then
  echo "ERROR: --out is required" >&2
  usage
  exit 2
fi

mkdir -p "$OUT_DIR"

echo "Capturing $COUNT frames"
echo "  from: $URL"
echo "    to: $OUT_DIR"
echo "interval: ${INTERVAL}s (timeout ${TIMEOUT}s)"
echo

failures=0
for i in $(seq 1 "$COUNT"); do
  ts="$(date +%Y%m%d-%H%M%S)"
  out_file="$OUT_DIR/${ts}_$(printf '%04d' "$i").jpg"

  # Fetch bytes and extract the first JPEG frame.
  # This works for both:
  # - direct JPEG responses
  # - MJPEG streams (multipart): we scan for 0xFFD8..0xFFD9 and write the first frame.
  set +e
  tmp="$(mktemp)"
  # Curl will often timeout on MJPEG streams (expected). We still want the partial body.
  curl -fsS --max-time "$TIMEOUT" -o "$tmp" "$URL" >/dev/null 2>&1
  curl_rc=$?

  python3 - "$tmp" "$out_file" <<'PY'
import sys
from pathlib import Path

tmp_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

data = tmp_path.read_bytes() if tmp_path.exists() else b""
if not data:
    raise SystemExit(10)

# Find first JPEG frame markers.
start = data.find(b"\xff\xd8")
end = data.find(b"\xff\xd9", start + 2)
if start == -1 or end == -1:
    raise SystemExit(11)

jpeg = data[start:end + 2]

# Quick sanity: very small "jpeg" usually means we didn't capture a real frame.
if len(jpeg) < 5_000:
    raise SystemExit(12)

out_path.write_bytes(jpeg)
PY
  py_rc=$?

  rm -f "$tmp" >/dev/null 2>&1 || true
  set -e

  if [ "$py_rc" -eq 0 ]; then
    echo "[$i/$COUNT] saved $(basename "$out_file")"
  else
    failures=$((failures + 1))
    rm -f "$out_file" >/dev/null 2>&1 || true
    echo "[$i/$COUNT] WARNING: capture failed (curl_rc=$curl_rc py_rc=$py_rc, $failures failures so far)" >&2
  fi

  if [ "$i" -lt "$COUNT" ]; then
    sleep "$INTERVAL"
  fi
done

echo
echo "Done. Failures: $failures"
