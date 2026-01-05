#!/bin/bash
# Runs at motion event end

set -euo pipefail

SNAPDIR="/var/lib/motion/snapshots"
EVENT_DIR="/var/lib/motion"

# Best-effort: use current_event_id to select the live-score cache DB.
# If events overlap, this may point to the newer event; in that case we simply
# lose some cache benefit, but correctness remains (we can always rescore).
EVENT_ID_FILE="$EVENT_DIR/current_event_id"
EVENT_ID="unknown"
if [ -r "$EVENT_ID_FILE" ]; then
  EVENT_ID="$(tr -d '\r\n' < "$EVENT_ID_FILE")"
fi
CACHE_DB="$EVENT_DIR/score_cache_${EVENT_ID}.sqlite3"

# Serialize event-end processing so we never mix two events' snapshots.
LOCKFILE="/var/lib/motion/.on_event_end_pipeline.lock"
exec 9>"$LOCKFILE"
flock 9

WORKDIR="$(mktemp -d -p /var/lib/motion eventproc.XXXXXX)"
cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

mkdir -p "$WORKDIR/snapshots"

# Cutoff marker: anything newer than this belongs to a newer event and must not be deleted.
CUTOFF_FILE="$WORKDIR/cutoff"
touch "$CUTOFF_FILE"

# Freeze a copy of the event's snapshots (everything not newer than cutoff)
find "$SNAPDIR" -maxdepth 1 -type f -name "*.jpg" ! -newer "$CUTOFF_FILE" -exec cp -a {} "$WORKDIR/snapshots/" \; || true

OUT="$WORKDIR/best_snapshot.jpg"

# Optional debug: write annotated scored frames to a persistent directory so you can
# watch the scoring live even though snapshots are stored in tmpfs and get cleared.
# Enable by exporting MOTION_DEBUG_SCORING=1 in the motion service environment.
DEBUG_ARGS=()
if [ "${MOTION_DEBUG_SCORING:-0}" = "1" ]; then
  DEBUG_DIR="/var/lib/motion/debug_scoring/$(basename "$WORKDIR")"
  mkdir -p "$DEBUG_DIR" || true
  DEBUG_ARGS+=( --debug-write-frames --debug-out-dir "$DEBUG_DIR" --debug-max-frames 600 )
fi

# Analyze only the frozen copies (reuse live-score cache when available)
/usr/local/bin/select_best_snapshot.py \
  --snapshot-dir "$WORKDIR/snapshots" \
  --output-file "$OUT" \
  --cache-db "$CACHE_DB" \
  "${DEBUG_ARGS[@]}"

# Email THIS event's best snapshot (path passed in)
/usr/local/bin/motion_email_alert.sh "$OUT"

# Housekeeping (mp4 pruning, etc.)
/usr/local/bin/motion_cleanup.sh

# Clear snapshots for the finished event ONLY (anything not newer than cutoff)
find "$SNAPDIR" -maxdepth 1 -type f -name "*.jpg" ! -newer "$CUTOFF_FILE" -delete || true

exit 0








