#!/bin/bash
# Motion hook: called after each snapshot is saved.
# Keep this fast and non-blocking for Motion.

set -euo pipefail

SNAPSHOT_FILE="${1:-}"
if [ -z "$SNAPSHOT_FILE" ] || [ ! -f "$SNAPSHOT_FILE" ]; then
  exit 0
fi

EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"

EVENT_ID="unknown"
if [ -r "$EVENT_ID_FILE" ]; then
  EVENT_ID="$(tr -d '\r\n' < "$EVENT_ID_FILE")"
fi

# Keep a single latest frame in RAM for bed-state checks.
# Atomic rename avoids readers seeing partial writes.
LATEST_DIR="/dev/shm"
LATEST_FILE="$LATEST_DIR/bed_latest.jpg"
mkdir -p "$LATEST_DIR" || true
tmp_file="$(mktemp "$LATEST_DIR/bed_latest.XXXXXX.jpg")"
if cp "$SNAPSHOT_FILE" "$tmp_file" 2>/dev/null; then
  mv -f "$tmp_file" "$LATEST_FILE" 2>/dev/null || rm -f "$tmp_file"
  chmod 644 "$LATEST_FILE" 2>/dev/null || true
else
  rm -f "$tmp_file" 2>/dev/null || true
fi

# Enqueue snapshot for scorer worker. No scoring here.
QUEUE_ROOT="$EVENT_DIR/score_queue"
QUEUE_DIR="$QUEUE_ROOT/$EVENT_ID"
mkdir -p "$QUEUE_DIR" || true

TS="$(date +%s%N 2>/dev/null || date +%s)"
QFILE="$QUEUE_DIR/${TS}_$$_$RANDOM.q"
printf '%s\n' "$SNAPSHOT_FILE" > "$QFILE" || true

exit 0

