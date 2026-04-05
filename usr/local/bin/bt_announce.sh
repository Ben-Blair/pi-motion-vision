#!/bin/bash
# Connects to the Logitech BT Adapter (if not already connected),
# plays a TTS announcement via PipeWire, then disconnects.
# Called as a fire-and-forget subprocess by fart_detector.py.
#
# Usage: bt_announce.sh ["message text"]
#   If a message is provided and pico2wave is installed, TTS audio is
#   generated on the fly. Otherwise falls back to a static WAV file.

set -uo pipefail

TAG="fart_bt"
BT_MAC="10:94:97:30:44:66"
STATIC_WAV="/home/bblair23/pi-motion-vision/assets/fart_detected.wav"
GENERATED_WAV="/tmp/fart_tts_$$.wav"
TTS_MESSAGE="${1:-}"
USED_GENERATED=false

# PipeWire runs under UID 1000 (bblair23). Point pw-play at that session.
export XDG_RUNTIME_DIR=/run/user/1000
export PULSE_SERVER=unix:/run/user/1000/pulse/native

# Loudness: pw-play stream volume (0–1, default max) and WirePlumber sink volume.
# Sink is often <100% or muted; that makes TTS sound quiet even with full stream gain.
# Override: BT_SINK_VOLUME=1 BT_DEVICE_VOLUME=1.0 (or 1.5 for 150% software boost).
BT_SINK_VOLUME="${BT_SINK_VOLUME:-1.0}"
BT_DEVICE_VOLUME="${BT_DEVICE_VOLUME:-1.0}"

# --- TTS generation ---

if [ -n "$TTS_MESSAGE" ] && command -v pico2wave >/dev/null 2>&1; then
  if pico2wave -l en-US -w "$GENERATED_WAV" "$TTS_MESSAGE" 2>/dev/null; then
    TTS_WAV="$GENERATED_WAV"
    USED_GENERATED=true
    logger -t "$TAG" "Generated TTS: $TTS_MESSAGE"
  else
    logger -t "$TAG" "pico2wave failed, falling back to static WAV"
    TTS_WAV="$STATIC_WAV"
  fi
else
  TTS_WAV="$STATIC_WAV"
fi

if [ ! -f "$TTS_WAV" ]; then
  logger -t "$TAG" "TTS WAV file missing: $TTS_WAV"
  rm -f "$GENERATED_WAV"
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
  logger -t "$TAG" "Playing TTS on PipeWire sink: $BT_SINK (stream vol=$BT_SINK_VOLUME)"
  pw-play --volume "$BT_SINK_VOLUME" --target "$BT_SINK" "$TTS_WAV" 2>/dev/null || \
    aplay "$TTS_WAV" 2>/dev/null || \
    logger -t "$TAG" "Failed to play TTS audio"
else
  logger -t "$TAG" "No BT sink found, trying aplay"
  aplay "$TTS_WAV" 2>/dev/null || \
    logger -t "$TAG" "Failed to play TTS audio"
fi

sleep 1

if [ "$USED_GENERATED" = true ]; then
  rm -f "$GENERATED_WAV"
fi

if [ "$WE_CONNECTED" = true ]; then
  logger -t "$TAG" "Disconnecting from $BT_MAC"
  bluetoothctl disconnect "$BT_MAC" >/dev/null 2>&1
fi

exit 0
