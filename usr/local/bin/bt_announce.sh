#!/bin/bash
# Connects to the Logitech BT Adapter (if not already connected),
# plays a weather-matched voice recording via PipeWire, then disconnects.
# Called as a fire-and-forget subprocess by fart_detector.py.
#
# Usage: bt_announce.sh [weather_descriptor]
#   weather_descriptor: lowercase word like "warm", "cold", "mild", etc.
#   The script picks a recording whose filename contains that word.
#   If no match or no descriptor given, falls back to a file containing "fart detected".
#   Falls back to the static WAV if the voice-recordings folder is empty.

set -uo pipefail

TAG="fart_bt"
BT_MAC="10:94:97:30:44:66"
VOICE_DIR="/home/bblair23/pi-motion-vision/assets/voice-recordings"
FALLBACK_WAV="/home/bblair23/pi-motion-vision/assets/fart_detected.wav"
DESCRIPTOR="${1:-}"

# PipeWire runs under UID 1000 (bblair23). Point pw-play at that session.
export XDG_RUNTIME_DIR=/run/user/1000
export PULSE_SERVER=unix:/run/user/1000/pulse/native

# Loudness: pw-play stream volume (0–1, default max) and WirePlumber sink volume.
# Sink is often <100% or muted; that makes TTS sound quiet even with full stream gain.
# Override: BT_SINK_VOLUME=1 BT_DEVICE_VOLUME=1.0 (or 1.5 for 150% software boost).
BT_SINK_VOLUME="${BT_SINK_VOLUME:-1.0}"
BT_DEVICE_VOLUME="${BT_DEVICE_VOLUME:-1.0}"

# --- Pick a matching voice recording ---
# Strategy:
#   1. If a descriptor is given, find files whose name contains it (case-insensitive).
#   2. Otherwise find files whose name contains "fart detected" (the generic recording).
#   3. If still nothing, pick any file in the directory.
#   4. Fall back to the static WAV.

find_recordings() {
  local pattern="${1:-}"
  local files=()
  while IFS= read -r -d '' f; do
    local base
    base="$(basename "$f" | tr '[:upper:]' '[:lower:]')"
    if [ -z "$pattern" ] || [[ "$base" == *"$pattern"* ]]; then
      files+=("$f")
    fi
  done < <(find "$VOICE_DIR" -maxdepth 1 -type f \( -iname "*.wav" -o -iname "*.mp3" -o -iname "*.ogg" -o -iname "*.m4a" \) -print0 2>/dev/null)
  printf '%s\0' "${files[@]+"${files[@]}"}"
}

pick_from_list() {
  local files=()
  while IFS= read -r -d '' f; do
    files+=("$f")
  done
  if [ "${#files[@]}" -eq 0 ]; then
    echo ""
    return
  fi
  local idx=$(( RANDOM % ${#files[@]} ))
  echo "${files[$idx]}"
}

PLAY_FILE=""

if [ -n "$DESCRIPTOR" ]; then
  PLAY_FILE="$(find_recordings "$DESCRIPTOR" | pick_from_list)"
fi

if [ -z "$PLAY_FILE" ]; then
  PLAY_FILE="$(find_recordings "fartdetected" | pick_from_list)"
fi

if [ -z "$PLAY_FILE" ]; then
  PLAY_FILE="$(find_recordings "" | pick_from_list)"
fi

if [ -z "$PLAY_FILE" ]; then
  logger -t "$TAG" "No recordings in $VOICE_DIR, using fallback WAV"
  PLAY_FILE="$FALLBACK_WAV"
else
  logger -t "$TAG" "Playing recording: $(basename "$PLAY_FILE") (descriptor=${DESCRIPTOR:-none})"
fi

if [ ! -f "$PLAY_FILE" ]; then
  logger -t "$TAG" "Audio file missing: $PLAY_FILE"
  exit 1
fi

# --- Bluetooth connection ---

already_connected() {
  bluetoothctl info "$BT_MAC" 2>/dev/null | grep -q "Connected: yes"
}

# Wait for the bluez A2DP sink to register in PipeWire after BT connect.
wait_for_bt_sink() {
  for _ in $(seq 1 16); do
    if pw-cli ls Node 2>/dev/null | grep -q "bluez_output"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

WE_CONNECTED=false

if already_connected; then
  logger -t "$TAG" "BT adapter already connected"
else
  logger -t "$TAG" "Connecting to $BT_MAC ..."
  bluetoothctl connect "$BT_MAC" >/dev/null 2>&1
  sleep 2

  if ! already_connected; then
    logger -t "$TAG" "Failed to connect to $BT_MAC"
    exit 1
  fi
  WE_CONNECTED=true
  logger -t "$TAG" "Connected to $BT_MAC"
fi

if ! wait_for_bt_sink; then
  logger -t "$TAG" "BT audio sink not available after connect"
  if [ "$WE_CONNECTED" = true ]; then
    bluetoothctl disconnect "$BT_MAC" >/dev/null 2>&1
  fi
  exit 1
fi

# --- Playback via PipeWire ---

# pw-cli "id N" matches wpctl sink IDs for the same node.
bluez_sink_node_id() {
  pw-cli ls Node 2>/dev/null | awk '
    $1 == "id" && $2 ~ /^[0-9]+,$/ { id = substr($2, 1, length($2)-1) }
    /node\.name = "bluez_output/ { print id; exit }
  '
}

maximize_bt_sink_volume() {
  local nid
  nid="$(bluez_sink_node_id)"
  if [ -z "$nid" ] || ! command -v wpctl >/dev/null 2>&1; then
    return 0
  fi
  wpctl set-mute "$nid" 0 2>/dev/null || true
  wpctl set-volume "$nid" "$BT_DEVICE_VOLUME" 2>/dev/null || true
  logger -t "$TAG" "BT sink id=$nid volume=$BT_DEVICE_VOLUME (unmuted)"
}

BT_SINK=$(pw-cli ls Node 2>/dev/null | grep -B1 "bluez_output" | grep "node.name" | head -1 | sed 's/.*= "\(.*\)"/\1/')

if [ -n "$BT_SINK" ]; then
  maximize_bt_sink_volume
  logger -t "$TAG" "Playing on PipeWire sink: $BT_SINK (stream vol=$BT_SINK_VOLUME)"
  pw-play --volume "$BT_SINK_VOLUME" --target "$BT_SINK" "$PLAY_FILE" 2>/dev/null || \
    aplay "$PLAY_FILE" 2>/dev/null || \
    logger -t "$TAG" "Failed to play audio"
else
  logger -t "$TAG" "No BT sink found, trying aplay"
  aplay "$PLAY_FILE" 2>/dev/null || \
    logger -t "$TAG" "Failed to play audio"
fi

sleep 1

if [ "$WE_CONNECTED" = true ]; then
  logger -t "$TAG" "Disconnecting from $BT_MAC"
  bluetoothctl disconnect "$BT_MAC" >/dev/null 2>&1
fi

exit 0
