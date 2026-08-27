#!/usr/bin/env bash
# Motion housekeeping: prune old files and enforce a size cap.

set -euo pipefail

MOTION_DIR="${MOTION_DIR:-/var/lib/motion}"
BED_MODEL_DIR="${BED_MODEL_DIR:-/home/bblair23/bed-model}"
REPO_DIR="${REPO_DIR:-/home/bblair23/pi-motion-vision}"

KEEP_DAYS_VIDEOS="${KEEP_DAYS_VIDEOS:-0}"
KEEP_DAYS_EMAILS="${KEEP_DAYS_EMAILS:-30}"
KEEP_DAYS_BEST="${KEEP_DAYS_BEST:-0}"
KEEP_DAYS_QUEUE="${KEEP_DAYS_QUEUE:-2}"
KEEP_DAYS_RESULTS="${KEEP_DAYS_RESULTS:-2}"
MAX_MOTION_GB="${MAX_MOTION_GB:-20}"
PRUNE_BATCH_GB="${PRUNE_BATCH_GB:-2}"
MAX_BEST_GB="${MAX_BEST_GB:-5}"
PRUNE_BEST_BATCH_GB="${PRUNE_BEST_BATCH_GB:-1}"

log() {
  logger -t motion-housekeeping "$*"
  echo "$*"
}

delete_old_files() {
  local dir="$1"
  local age_days="$2"
  local pattern="$3"
  if [ -d "$dir" ]; then
    find "$dir" -type f -name "$pattern" -mtime "+$age_days" -delete 2>/dev/null || true
  fi
}

delete_old_dirs() {
  local dir="$1"
  local age_days="$2"
  if [ -d "$dir" ]; then
    find "$dir" -mindepth 1 -type d -mtime "+$age_days" -exec rm -rf {} + 2>/dev/null || true
  fi
}

log "Starting housekeeping for $MOTION_DIR"

# Age-based cleanup by category.
# KEEP_DAYS_VIDEOS=0 disables age-based video deletion so retention is cap-only.
if [ "${KEEP_DAYS_VIDEOS}" -gt 0 ]; then
  delete_old_files "$MOTION_DIR/videos" "$KEEP_DAYS_VIDEOS" "*.mp4"
  delete_old_files "$MOTION_DIR" "$KEEP_DAYS_VIDEOS" "*.mkv"
fi
delete_old_files "$MOTION_DIR/emailed" "$KEEP_DAYS_EMAILS" "*.jpg"
if [ "${KEEP_DAYS_BEST}" -gt 0 ]; then
  delete_old_files "$MOTION_DIR/best_snapshots" "$KEEP_DAYS_BEST" "*.jpg"
fi
delete_old_dirs "$MOTION_DIR/score_queue" "$KEEP_DAYS_QUEUE"

# Remove stale model prediction artifacts.
if [ -d "$BED_MODEL_DIR/results" ]; then
  find "$BED_MODEL_DIR/results" -mindepth 1 -mtime "+$KEEP_DAYS_RESULTS" -exec rm -rf {} + 2>/dev/null || true
fi
if [ -d "$REPO_DIR/results" ]; then
  find "$REPO_DIR/results" -mindepth 1 -mtime "+$KEEP_DAYS_RESULTS" -exec rm -rf {} + 2>/dev/null || true
fi

# Size-cap cleanup for motion storage: delete oldest videos first.
if [ -d "$MOTION_DIR" ]; then
  max_bytes=$((MAX_MOTION_GB * 1024 * 1024 * 1024))
  batch_bytes=$((PRUNE_BATCH_GB * 1024 * 1024 * 1024))
  target_bytes=$((max_bytes - batch_bytes))
  if [ "$target_bytes" -lt 0 ]; then
    target_bytes=0
  fi
  current_bytes=$(du -sb "$MOTION_DIR" | awk '{print $1}')
  if [ "${current_bytes:-0}" -gt "$max_bytes" ]; then
    log "Motion dir over cap (${current_bytes} bytes > ${max_bytes} bytes), pruning oldest videos toward ${target_bytes} bytes"
    while [ "$current_bytes" -gt "$target_bytes" ]; do
      oldest_file="$(find "$MOTION_DIR" -type f \( -name "*.mp4" -o -name "*.mkv" \) -printf '%T@ %p\n' | sort -n | awk 'NR==1{print $2}')"
      if [ -z "$oldest_file" ] || [ ! -f "$oldest_file" ]; then
        break
      fi
      bytes=$(stat -c%s "$oldest_file" 2>/dev/null || echo 0)
      rm -f "$oldest_file" 2>/dev/null || true
      current_bytes=$((current_bytes - bytes))
    done
  fi
fi

# Size-cap cleanup for best snapshots directory: prune oldest artifacts in batches.
BEST_DIR="$MOTION_DIR/best_snapshots"
if [ -d "$BEST_DIR" ]; then
  best_max_bytes=$((MAX_BEST_GB * 1024 * 1024 * 1024))
  best_batch_bytes=$((PRUNE_BEST_BATCH_GB * 1024 * 1024 * 1024))
  best_target_bytes=$((best_max_bytes - best_batch_bytes))
  if [ "$best_target_bytes" -lt 0 ]; then
    best_target_bytes=0
  fi
  best_current_bytes=$(du -sb "$BEST_DIR" | awk '{print $1}')
  if [ "${best_current_bytes:-0}" -gt "$best_max_bytes" ]; then
    log "best_snapshots over cap (${best_current_bytes} bytes > ${best_max_bytes} bytes), pruning oldest files toward ${best_target_bytes} bytes"
    while [ "$best_current_bytes" -gt "$best_target_bytes" ]; do
      oldest_best="$(find "$BEST_DIR" -type f -printf '%T@ %p\n' | sort -n | awk 'NR==1{print $2}')"
      if [ -z "$oldest_best" ] || [ ! -f "$oldest_best" ]; then
        break
      fi
      bytes=$(stat -c%s "$oldest_best" 2>/dev/null || echo 0)
      rm -f "$oldest_best" 2>/dev/null || true
      best_current_bytes=$((best_current_bytes - bytes))
    done
  fi
fi

# Clean up stale score-cache SQLite DBs (one per event, can accumulate).
if [ -d "$MOTION_DIR" ]; then
  find "$MOTION_DIR" -maxdepth 1 -name "score_cache_*.sqlite3" -mtime +2 -delete 2>/dev/null || true
fi

# Clean up orphan audio WAVs in /dev/shm (safety net for crashes).
for f in /dev/shm/motion_audio_*.wav /dev/shm/motion_audio_*.wav.mux; do
  [ -f "$f" ] || continue
  if ! fuser "$f" >/dev/null 2>&1; then
    rm -f "$f" 2>/dev/null || true
    log "Removed orphan shm file: $f"
  fi
done

# Clean up stale /dev/shm debug scoring dirs older than 1 day.
if [ -d /dev/shm/motion_debug_scoring ]; then
  find /dev/shm/motion_debug_scoring -mindepth 1 -maxdepth 1 -type d -mmin +1440 -exec rm -rf {} + 2>/dev/null || true
fi

# Prune empty directories left after cleanup.
if [ -d "$MOTION_DIR" ]; then
  find "$MOTION_DIR" -type d -empty -delete 2>/dev/null || true
fi

log "Housekeeping complete"
