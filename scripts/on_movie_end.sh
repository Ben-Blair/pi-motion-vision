#!/bin/bash
# Called by Motion when a movie file is finalized: on_movie_end %f
# Muxes audio (if recorded) into the video, then restarts audio capture
# for the next segment (event may still be active due to movie_max_time splits).

set -uo pipefail

MOVIE_FILE="${1:-}"
EVENT_ID_FILE="/var/lib/motion/current_event_id"
PID_FILE="/dev/shm/motion_audio.pid"
TAG="motion_audio"

# Audio for this clip: one file per motion event (current_event_id), so later
# rm of motion_audio.wav cannot unlink the inode arecord is still writing to.
audio_wav_path() {
  local id stem
  id="$(tr -d '\r\n' < "$EVENT_ID_FILE" 2>/dev/null || true)"
  stem=$(basename "${MOVIE_FILE:-x}" .mp4)
  if [ -n "$id" ]; then
    echo "/dev/shm/motion_audio_${id}.wav"
  elif [ -n "$stem" ] && [ "$stem" != "x" ]; then
    echo "/dev/shm/motion_audio_${stem}.wav"
  else
    echo "/dev/shm/motion_audio.wav"
  fi
}

record_wav_path() {
  local id
  id="$(tr -d '\r\n' < "$EVENT_ID_FILE" 2>/dev/null || true)"
  if [ -n "$id" ]; then
    echo "/dev/shm/motion_audio_${id}.wav"
  else
    echo "/dev/shm/motion_audio.wav"
  fi
}

stop_audio() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
      done
    fi
    rm -f "$PID_FILE"
  fi
  pkill -x -u motion arecord 2>/dev/null || true
  for _ in $(seq 1 30); do
    pgrep -x -u motion arecord >/dev/null 2>&1 || break
    sleep 0.1
  done
}

detect_mic() {
  local card
  # Prefer the dedicated USB microphone; fall back to any capture device
  card=$(arecord -l 2>/dev/null \
    | grep '^card' \
    | grep -i 'microphone' \
    | head -1 \
    | sed 's/card \([0-9]*\):.*device \([0-9]*\):.*/\1,\2/')
  if [ -z "$card" ]; then
    card=$(arecord -l 2>/dev/null \
      | grep -m1 '^card' \
      | sed 's/card \([0-9]*\):.*device \([0-9]*\):.*/\1,\2/')
  fi
  if [ -n "$card" ]; then
    echo "plughw:$card"
  fi
}

start_audio() {
  local dev rec_pid out
  dev=$(detect_mic)
  if [ -z "$dev" ]; then
    logger -t "$TAG" "No capture device found, skipping audio"
    return 1
  fi
  out="$(record_wav_path)"
  pkill -x -u motion arecord 2>/dev/null || true
  sleep 0.2
  rm -f "$out"
  : > /var/lib/motion/arecord-last.log
  arecord -D "$dev" -f S16_LE -r 44100 -c 1 "$out" \
    >/var/lib/motion/arecord-last.log 2>&1 &
  rec_pid=$!
  echo "$rec_pid" > "$PID_FILE"
  sleep 0.3
  if kill -0 "$rec_pid" 2>/dev/null && [ -s "$out" ]; then
    logger -t "$TAG" "Audio recording restarted (dev=$dev, pid=$rec_pid, file=$out)"
  else
    logger -t "$TAG" "arecord restart failed (dev=$dev)"
    rm -f "$PID_FILE"
    return 1
  fi
}

# --- Main ---

if [ -z "$MOVIE_FILE" ] || [ ! -f "$MOVIE_FILE" ]; then
  logger -t "$TAG" "on_movie_end: movie file missing or not provided"
  exit 0
fi

stop_audio

AUDIO_FILE="$(audio_wav_path)"

# Give arecord a moment to finalize the WAV header after SIGTERM.
# On tmpfs the I/O is instant, but the process needs a brief window
# to seek back and update the RIFF size fields before exiting.
for _ in $(seq 1 50); do
  [ -f "$AUDIO_FILE" ] && [ -s "$AUDIO_FILE" ] && break
  AUDIO_FILE="$(audio_wav_path)"
  sleep 0.1
done

if [ ! -f "$AUDIO_FILE" ] || [ ! -s "$AUDIO_FILE" ]; then
  logger -t "$TAG" "No audio file to mux into $MOVIE_FILE (expected $(audio_wav_path))"
  rm -f "$AUDIO_FILE"
  start_audio || true
  exit 0
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  logger -t "$TAG" "ffmpeg not installed, skipping audio mux"
  rm -f "$AUDIO_FILE"
  start_audio || true
  exit 0
fi

TEMP_OUTPUT="${MOVIE_FILE%.*}.mux_tmp.mp4"

if ffmpeg -y -nostdin -loglevel error \
    -i "$MOVIE_FILE" -i "$AUDIO_FILE" \
    -c:v copy -c:a aac -b:a 128k \
    -shortest -movflags +faststart \
    "$TEMP_OUTPUT"; then
  mv "$TEMP_OUTPUT" "$MOVIE_FILE"
  logger -t "$TAG" "Audio muxed into $MOVIE_FILE"
else
  logger -t "$TAG" "ffmpeg mux failed for $MOVIE_FILE, keeping original"
  rm -f "$TEMP_OUTPUT"
fi

rm -f "$AUDIO_FILE"

start_audio || true

exit 0
