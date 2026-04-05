#!/bin/bash
# Runs at motion event end

set -euo pipefail

SNAPDIR="/var/lib/motion/snapshots"
EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"

# Serialize event-end processing so we never mix two events' snapshots.
LOCKFILE="/var/lib/motion/.on_event_end_pipeline.lock"
exec 9>"$LOCKFILE"
flock 9

############################################################
# Audio – do NOT kill arecord here. on_movie_end owns the full
# arecord lifecycle (stop → mux → restart). Killing here raced
# with on_movie_end and caused the WAV file to be missing or
# in a transient state when the mux ran. on_event_start handles
# stale arecord cleanup for the next event.
############################################################

WORKDIR="$(mktemp -d -p /var/lib/motion eventproc.XXXXXX)"
cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

mkdir -p "$WORKDIR/snapshots"

# Cutoff marker: anything newer than this belongs to a newer event and must not be deleted.
CUTOFF_FILE="$WORKDIR/cutoff"
touch "$CUTOFF_FILE"

# Find the most recently flushed event (the one that just ended)
# This is more reliable than using current_event_id which may have already been updated to a new event
BEST_SNAPSHOTS_ROOT="$EVENT_DIR/best_snapshots"
EVENT_ID="unknown"
BEST_CURRENT=""

# Look for the most recent metadata file that was modified within the last 5 minutes
# The metadata file is updated when the worker does a final checkpoint, so it's more accurate
NOW=$(date +%s)
RECENT_THRESHOLD=$((NOW - 300))  # 5 minutes ago

if [ -d "$BEST_SNAPSHOTS_ROOT" ]; then
  # Find the MOST RECENT current_best.jpg (regardless of final status)
  # Prioritize final checkpoints, but use most recent if no final found
  BEST_META_TIME=0
  BEST_META_FILE=""
  BEST_META_EVENT=""
  
  # First pass: find most recent metadata file (final or not)
  while IFS= read -r meta_file; do
    if [ -f "$meta_file" ]; then
      mtime=$(stat -c %Y "$meta_file" 2>/dev/null || echo 0)
      if [ "$mtime" -gt "$RECENT_THRESHOLD" ] && [ "$mtime" -gt "$BEST_META_TIME" ]; then
        event_dir=$(dirname "$meta_file")
        candidate_id=$(basename "$event_dir")
        if [ -n "$candidate_id" ] && [ "$candidate_id" != "best_snapshots" ]; then
          BEST_META_TIME=$mtime
          BEST_META_FILE="$meta_file"
          BEST_META_EVENT="$candidate_id"
        fi
      fi
    fi
  done < <(find "$BEST_SNAPSHOTS_ROOT" -name "current_best.jpg.meta.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | awk '{print $2}')
  
  # Use the most recent metadata file found (even if final=false)
  if [ -n "$BEST_META_FILE" ] && [ -f "$BEST_META_FILE" ]; then
    EVENT_ID="$BEST_META_EVENT"
    BEST_CURRENT="$(dirname "$BEST_META_FILE")/current_best.jpg"
  fi
fi

# Fallback: if no recent best found, try using current_event_id (for backward compatibility)
if [ "$EVENT_ID" = "unknown" ] || [ ! -f "$BEST_CURRENT" ]; then
  if [ -r "$EVENT_ID_FILE" ]; then
    EVENT_ID="$(tr -d '\r\n' < "$EVENT_ID_FILE")"
    BEST_DIR="$EVENT_DIR/best_snapshots/$EVENT_ID"
    BEST_CURRENT="$BEST_DIR/current_best.jpg"
  fi
fi

# Ask the live scoring worker to flush the final best (low SD writes during event, final commit now).
QUEUE_DIR="$EVENT_DIR/score_queue/$EVENT_ID"
FLUSH_FILE="$QUEUE_DIR/flush"
FLUSHED_FILE="$QUEUE_DIR/flushed"

mkdir -p "$QUEUE_DIR" "$(dirname "$BEST_CURRENT" 2>/dev/null || echo "$EVENT_DIR")" || true
touch "$FLUSH_FILE" || true

# Check worker health before waiting
WORKER_HEALTH_FILE="$EVENT_DIR/.worker_health"
WORKER_HEALTHY=false
if [ -f "$WORKER_HEALTH_FILE" ]; then
  HEALTH_AGE=$(($(date +%s) - $(stat -c %Y "$WORKER_HEALTH_FILE" 2>/dev/null || echo 0)))
  if [ "$HEALTH_AGE" -lt 5 ]; then
    WORKER_HEALTHY=true
  fi
fi

# Wait briefly for worker flush (for cleanup, but we'll use batch selection for best snapshot).
# Skip wait if worker is not healthy.
if [ "$WORKER_HEALTHY" = "true" ]; then
  for _ in {1..60}; do
    if [ -f "$FLUSHED_FILE" ]; then
      break
    fi
    sleep 0.5
  done
fi

# Optional debug: write annotated scored frames to a persistent directory so you can
# watch the scoring live even though snapshots are stored in tmpfs and get cleared.
# Enable by exporting MOTION_DEBUG_SCORING=1 in the motion service environment.
DEBUG_ARGS=()
if [ "${MOTION_DEBUG_SCORING:-0}" = "1" ]; then
  DEBUG_ROOT="/var/lib/motion/debug_scoring"
  DEBUG_DIR="$DEBUG_ROOT/$(basename "$WORKDIR")"
  mkdir -p "$DEBUG_DIR" || true
  # Stable pointer for viewing without hunting random eventproc.* directory names.
  ln -sfn "$DEBUG_DIR" "$DEBUG_ROOT/latest" || true
  DEBUG_ARGS+=( --debug-write-frames --debug-out-dir "$DEBUG_DIR" --debug-max-frames 600 )
fi

# Always run batch selection at event end to get the true best snapshot (no stability gates).
# This ensures we select the highest-scoring frame regardless of stability gate filtering.
# Freeze a copy of the event's snapshots (everything not newer than cutoff)
find "$SNAPDIR" -maxdepth 1 -type f -name "*.jpg" ! -newer "$CUTOFF_FILE" -exec cp -a {} "$WORKDIR/snapshots/" \; || true
OUT="$WORKDIR/best_snapshot.jpg"
/usr/local/bin/select_best_snapshot.py \
  --snapshot-dir "$WORKDIR/snapshots" \
  --output-file "$OUT" \
  "${DEBUG_ARGS[@]}"

# Email THIS event's best snapshot (path passed in)
/usr/local/bin/motion_email_alert.sh "$OUT"

# Clean up queue directory (safety net if worker didn't clean it up)
if [ -d "$QUEUE_DIR" ]; then
  # Remove any remaining .q files
  find "$QUEUE_DIR" -maxdepth 1 -name "*.q" -delete 2>/dev/null || true
  # Remove queue directory if empty or only contains flush markers
  remaining=$(find "$QUEUE_DIR" -maxdepth 1 -mindepth 1 ! -name "flush" ! -name "flushed" 2>/dev/null | wc -l)
  if [ "$remaining" -eq 0 ]; then
    rm -rf "$QUEUE_DIR" 2>/dev/null || true
  fi
fi

# Housekeeping (mp4 pruning, etc.)
/usr/local/bin/motion_cleanup.sh

# Clear snapshots for the finished event ONLY (anything not newer than cutoff)
find "$SNAPDIR" -maxdepth 1 -type f -name "*.jpg" ! -newer "$CUTOFF_FILE" -delete || true

exit 0
