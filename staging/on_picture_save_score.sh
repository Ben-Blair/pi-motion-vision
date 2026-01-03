#!/bin/bash
# Motion hook: called after each snapshot is saved.
# Must return quickly so Motion isn't blocked.

set -euo pipefail

SNAPSHOT_FILE="${1:-}"
if [ -z "$SNAPSHOT_FILE" ]; then
  exit 0
fi

EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"

EVENT_ID="unknown"
if [ -r "$EVENT_ID_FILE" ]; then
  EVENT_ID="$(tr -d '\r\n' < "$EVENT_ID_FILE")"
fi

# Enqueue snapshot for the scorer worker. No scoring here (prevents skipped frames and reduces process churn).
QUEUE_ROOT="$EVENT_DIR/score_queue"
QUEUE_DIR="$QUEUE_ROOT/$EVENT_ID"
mkdir -p "$QUEUE_DIR" || true

# Monotonic-ish name for ordering; fall back if %N isn't supported.
TS="$(date +%s%N 2>/dev/null || date +%s)"
QFILE="$QUEUE_DIR/${TS}_$$_$RANDOM.q"
printf '%s\n' "$SNAPSHOT_FILE" > "$QFILE" || true

exit 0

