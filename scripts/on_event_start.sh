#!/bin/bash
set -e

EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"
PID_FILE="/dev/shm/motion_audio.pid"

# NOTE:
# We no longer clear /var/lib/motion/snapshots or /var/lib/motion/best_snapshot.jpg
# here. Cleanup happens at event end so overlapping events can't wipe files needed
# for the previous event's processing/email.

# Generate new event ID
EVENT_ID="$(date +%Y%m%d-%H%M%S)"
echo "$EVENT_ID" > "$EVENT_ID_FILE"
# Per-event WAV so unlink/rm of a stale path cannot steal the inode while arecord
# still writes (global motion_audio.wav caused "recording started" but mux found no file).
AUDIO_FILE="/dev/shm/motion_audio_${EVENT_ID}.wav"

logger -t motion_event "New motion event started: $EVENT_ID"

############################################################
# Audio – start recording from USB mic (if present)
############################################################

# Kill any stale arecord left from a previous event (PID file can be stale if a
# prior arecord kept running without a matching pid file — then new arecord gets
# "Device or resource busy" and exits immediately with no WAV).
if [ -f "$PID_FILE" ]; then
  old_pid=$(cat "$PID_FILE" 2>/dev/null)
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -x -u motion arecord 2>/dev/null || true
sleep 0.2
rm -f "$AUDIO_FILE"

ALSA_DEV=""
# Prefer the dedicated USB microphone; fall back to any capture device
card_info=$(arecord -l 2>/dev/null | grep '^card' | grep -i 'microphone' | head -1 || true)
if [ -z "$card_info" ]; then
  card_info=$(arecord -l 2>/dev/null | grep -m1 '^card' || true)
fi
if [ -n "$card_info" ]; then
  ALSA_DEV="plughw:$(echo "$card_info" | sed 's/card \([0-9]*\):.*device \([0-9]*\):.*/\1,\2/')"
fi

if [ -n "$ALSA_DEV" ]; then
  : > /var/lib/motion/arecord-last.log
  arecord -D "$ALSA_DEV" -f S16_LE -r 44100 -c 1 "$AUDIO_FILE" \
    >/var/lib/motion/arecord-last.log 2>&1 &
  rec_pid=$!
  echo "$rec_pid" > "$PID_FILE"
  sleep 0.3
  if kill -0 "$rec_pid" 2>/dev/null && [ -s "$AUDIO_FILE" ]; then
    logger -t motion_audio "Audio recording started (dev=$ALSA_DEV, pid=$rec_pid, file=$AUDIO_FILE)"
  else
    err=$(head -1 /var/lib/motion/arecord-last.log 2>/dev/null | tr -d '\r\n' | cut -c1-120)
    logger -t motion_audio "arecord failed or no audio file (dev=$ALSA_DEV): ${err:-unknown}"
    rm -f "$PID_FILE"
  fi
else
  logger -t motion_audio "No capture device found, recording video only"
fi