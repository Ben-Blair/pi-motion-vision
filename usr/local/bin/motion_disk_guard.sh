#!/usr/bin/env bash
# Disk guard: warn at high usage and trigger housekeeping at critical usage.

set -euo pipefail

WARN_PERCENT="${WARN_PERCENT:-80}"
CRITICAL_PERCENT="${CRITICAL_PERCENT:-90}"
HOUSEKEEPING_CMD="${HOUSEKEEPING_CMD:-/usr/local/bin/motion_housekeeping.sh}"

usage_percent() {
  local path="$1"
  df -P "$path" | awk 'NR==2 {gsub("%","",$5); print $5}'
}

check_path() {
  local path="$1"
  local pct
  pct="$(usage_percent "$path")"
  if [ -z "$pct" ]; then
    return
  fi

  if [ "$pct" -ge "$CRITICAL_PERCENT" ]; then
    logger -t motion-disk-guard "CRITICAL: ${path} usage ${pct}% (>= ${CRITICAL_PERCENT}%), running housekeeping"
    "$HOUSEKEEPING_CMD" || true
  elif [ "$pct" -ge "$WARN_PERCENT" ]; then
    logger -t motion-disk-guard "WARNING: ${path} usage ${pct}% (>= ${WARN_PERCENT}%)"
  fi
}

check_path "/"
check_path "/var/lib/motion"
