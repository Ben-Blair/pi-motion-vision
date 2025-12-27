#!/bin/bash
set -e

EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"

# NOTE:
# We no longer clear /var/lib/motion/snapshots or /var/lib/motion/best_snapshot.jpg
# here. Cleanup happens at event end so overlapping events can't wipe files needed
# for the previous event's processing/email.

# Generate new event ID
EVENT_ID="$(date +%Y%m%d-%H%M%S)"
echo "$EVENT_ID" > "$EVENT_ID_FILE"

logger -t motion_event "New motion event started: $EVENT_ID"