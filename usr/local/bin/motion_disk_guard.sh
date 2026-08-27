#!/usr/bin/env bash
# Disk guard: warn at high usage and trigger housekeeping at critical usage.
# Also monitors /dev/shm (tmpfs) and cleans orphan audio files.

set -euo pipefail

TAG="motion-disk-guard"
WARN_PERCENT="${WARN_PERCENT:-80}"
CRITICAL_PERCENT="${CRITICAL_PERCENT:-90}"
SHM_WARN_PERCENT="${SHM_WARN_PERCENT:-60}"
SHM_CRITICAL_PERCENT="${SHM_CRITICAL_PERCENT:-80}"
HOUSEKEEPING_CMD="${HOUSEKEEPING_CMD:-/usr/local/bin/motion_housekeeping.sh}"

usage_percent() {
  local path="$1"
  df -P "$path" | awk 'NR==2 {gsub("%","",$5); print $5}'
}

check_path() {
  local path="$1" warn="$2" crit="$3"
  local pct
  pct="$(usage_percent "$path")"
  if [ -z "$pct" ]; then
    return
  fi

  if [ "$pct" -ge "$crit" ]; then
    logger -t "$TAG" "CRITICAL: ${path} usage ${pct}% (>= ${crit}%), running housekeeping"
    "$HOUSEKEEPING_CMD" || true
  elif [ "$pct" -ge "$warn" ]; then
    logger -t "$TAG" "WARNING: ${path} usage ${pct}% (>= ${warn}%)"
  fi
}

cleanup_shm() {
  local pct
  pct="$(usage_percent /dev/shm)"
  if [ -z "$pct" ]; then
    return
  fi
  if [ "$pct" -ge "$SHM_WARN_PERCENT" ]; then
    logger -t "$TAG" "WARNING: /dev/shm usage ${pct}%, cleaning orphan motion audio files"
    # Only remove WAVs that are not held open by any process
    for f in /dev/shm/motion_audio_*.wav /dev/shm/motion_audio_*.wav.mux; do
      [ -f "$f" ] || continue
      if ! fuser "$f" >/dev/null 2>&1; then
        rm -f "$f" 2>/dev/null || true
        logger -t "$TAG" "Removed orphan: $f"
      fi
    done
  fi
}

check_path "/" "$WARN_PERCENT" "$CRITICAL_PERCENT"
check_path "/var/lib/motion" "$WARN_PERCENT" "$CRITICAL_PERCENT"
cleanup_shm
