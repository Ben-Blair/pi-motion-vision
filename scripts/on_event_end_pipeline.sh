#!/bin/bash
# Runs at motion event end

set -euo pipefail

SNAPDIR="/var/lib/motion/snapshots"

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

# Analyze only the frozen copies
/usr/local/bin/select_best_snapshot.py \
  --snapshot-dir "$WORKDIR/snapshots" \
  --output-file "$OUT"

# Email THIS event's best snapshot (path passed in)
/usr/local/bin/motion_email_alert.sh "$OUT"

# Housekeeping (mp4 pruning, etc.)
/usr/local/bin/motion_cleanup.sh

# Clear snapshots for the finished event ONLY (anything not newer than cutoff)
find "$SNAPDIR" -maxdepth 1 -type f -name "*.jpg" ! -newer "$CUTOFF_FILE" -delete || true

exit 0
