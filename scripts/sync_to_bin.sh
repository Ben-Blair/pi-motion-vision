#!/bin/bash
# Sync scripts from /etc/motion/scripts to /usr/local/bin
# This script requires sudo to copy files to /usr/local/bin

set -euo pipefail

SCRIPT_DIR="/etc/motion/scripts"
BIN_DIR="/usr/local/bin"

echo "Syncing scripts from $SCRIPT_DIR to $BIN_DIR..."

# Sync Python scripts
for script in "$SCRIPT_DIR"/*.py; do
  if [ -f "$script" ]; then
    script_name=$(basename "$script")
    echo "Copying $script_name..."
    sudo cp "$script" "$BIN_DIR/$script_name"
    sudo chmod +x "$BIN_DIR/$script_name"
  fi
done

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
