#!/bin/bash
# Sync scripts from /etc/motion/scripts to /usr/local/bin
# This script requires sudo to copy files to /usr/local/bin

set -euo pipefail

SCRIPT_DIR="/etc/motion/scripts"
STAGING_DIR="/etc/motion/staging"
BIN_DIR="/usr/local/bin"

echo "Syncing scripts from $SCRIPT_DIR to $BIN_DIR..."

# Sync Python scripts
for script in "$SCRIPT_DIR"/*.py; do
  if [ -f "$script" ]; then
    script_name=$(basename "$script")
    # IMPORTANT:
    # /usr/local/bin/select_best_snapshot.py must remain the modular implementation,
    # because live scoring imports it as a module. The modular version lives in staging.
    if [ "$script_name" = "select_best_snapshot.py" ]; then
      continue
    fi
    echo "Copying $script_name..."
    sudo cp "$script" "$BIN_DIR/$script_name"
    sudo chmod +x "$BIN_DIR/$script_name"
  fi
done

# Install modular selector used by both event-end selection and live scoring imports.
if [ -f "$STAGING_DIR/select_best_snapshot.py" ]; then
  echo "Copying select_best_snapshot.py (modular) from staging..."
  sudo cp "$STAGING_DIR/select_best_snapshot.py" "$BIN_DIR/select_best_snapshot.py"
  sudo chmod +x "$BIN_DIR/select_best_snapshot.py"
fi

# Install live scoring components (staging)
if [ -f "$STAGING_DIR/live_score_snapshot.py" ]; then
  echo "Copying live_score_snapshot.py..."
  sudo cp "$STAGING_DIR/live_score_snapshot.py" "$BIN_DIR/live_score_snapshot.py"
  sudo chmod +x "$BIN_DIR/live_score_snapshot.py"
fi
if [ -f "$STAGING_DIR/on_picture_save_score.sh" ]; then
  echo "Copying on_picture_save_score.sh..."
  sudo cp "$STAGING_DIR/on_picture_save_score.sh" "$BIN_DIR/on_picture_save_score.sh"
  sudo chmod +x "$BIN_DIR/on_picture_save_score.sh"
fi
if [ -f "$STAGING_DIR/motion_score_worker.py" ]; then
  echo "Copying motion_score_worker.py..."
  sudo cp "$STAGING_DIR/motion_score_worker.py" "$BIN_DIR/motion_score_worker.py"
  sudo chmod +x "$BIN_DIR/motion_score_worker.py"
fi

# Sync shell scripts
for script in "$SCRIPT_DIR"/*.sh; do
  if [ -f "$script" ] && [ "$(basename "$script")" != "sync_to_bin.sh" ]; then
    script_name=$(basename "$script")
    echo "Copying $script_name..."
    sudo cp "$script" "$BIN_DIR/$script_name"
    sudo chmod +x "$BIN_DIR/$script_name"
  fi
done

echo "Sync complete!"
