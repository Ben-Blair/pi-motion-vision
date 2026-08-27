#!/bin/bash
set -e

EVENT_DIR="/var/lib/motion"
EVENT_ID_FILE="$EVENT_DIR/current_event_id"
PID_FILE="/dev/shm/motion_audio.pid"
TAG="motion_event"

EVENT_ID="$(date +%Y%m%d-%H%M%S)"
echo "$EVENT_ID" > "$EVENT_ID_FILE"
AUDIO_FILE="/dev/shm/motion_audio_${EVENT_ID}.wav"

shm_usage() { df -P /dev/shm | awk 'NR==2 {print $5}'; }

logger -t "$TAG" "EVENT_START event=$EVENT_ID shm_usage=$(shm_usage)"

############################################################
# Audio + Fart Detection – start fart_detector.py
# (replaces arecord: records WAV + runs YAMNet fart classification)
############################################################

# Kill any stale detector or arecord left from a previous event
if [ -f "$PID_FILE" ]; then
  old_pid=$(cat "$PID_FILE" 2>/dev/null)
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "fart_detector.py" -u motion 2>/dev/null || true
pkill -x -u motion arecord 2>/dev/null || true
sleep 0.2
rm -f /dev/shm/motion_audio_*.wav /dev/shm/motion_audio_*.wav.mux 2>/dev/null || true

FART_DETECTOR="/usr/local/bin/fart_detector.py"

if [ -x "$FART_DETECTOR" ]; then
  : > /var/lib/motion/fart-detector-last.log
  python3 "$FART_DETECTOR" \
    --wav-path "$AUDIO_FILE" \
    --event-id "$EVENT_ID" \
    --pid-file "$PID_FILE" \
    >/var/lib/motion/fart-detector-last.log 2>&1 &
  rec_pid=$!
  sleep 1
  if kill -0 "$rec_pid" 2>/dev/null; then
    logger -t motion_audio "Fart detector started (pid=$rec_pid, event=$EVENT_ID, wav=$AUDIO_FILE)"
  else
    err=$(head -3 /var/lib/motion/fart-detector-last.log 2>/dev/null | tr -d '\r\n' | cut -c1-200)
    logger -t motion_audio "fart_detector.py failed to start: ${err:-unknown}"
    rm -f "$PID_FILE"
  fi
else
  logger -t motion_audio "fart_detector.py not found, falling back to arecord"
  # Fallback: plain arecord (no fart detection)
  ALSA_DEV=""
  card_info=$(arecord -l 2>/dev/null | grep '^card' | grep -i 'microphone' | head -1 || true)
  if [ -z "$card_info" ]; then
    card_info=$(arecord -l 2>/dev/null | grep -m1 '^card' || true)
  fi
  if [ -n "$card_info" ]; then
    ALSA_DEV="plughw:$(echo "$card_info" | sed 's/card \([0-9]*\):.*device \([0-9]*\):.*/\1,\2/')"
  fi
  if [ -n "$ALSA_DEV" ]; then
    arecord -D "$ALSA_DEV" -f S16_LE -r 44100 -c 1 "$AUDIO_FILE" \
      >/dev/null 2>&1 &
    echo "$!" > "$PID_FILE"
  fi
fi