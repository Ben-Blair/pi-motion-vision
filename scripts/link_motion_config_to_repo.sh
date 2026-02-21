#!/bin/bash
#
# Symlink /etc/motion config files to this repo so you can edit them
# directly in your editor (e.g. Cursor). No copy/sync step needed.
#
# Run once on the Pi (with sudo). After this:
#   - Edit files under ~/pi-motion-vision/etc/motion/ as usual
#   - Run: sudo systemctl restart motion
#   - Changes take effect immediately (no deploy_to_live.sh)
#
# To go back to "copy on deploy" mode, run deploy_to_live.sh again;
# it overwrites /etc/motion/*.conf with copies from the repo.
#
# Note: Motion runs as user "motion". If your repo is under your home directory,
# ensure the motion user can traverse to the config files (e.g. chmod o+x $HOME).
# Otherwise motion will use default config and the stream will not start.
#
# Usage:
#   cd ~/pi-motion-vision
#   ./scripts/link_motion_config_to_repo.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Linking /etc/motion config to repo: $REPO_DIR"
echo

sudo mkdir -p /etc/motion

for name in motion.conf camera0.conf; do
  src="$REPO_DIR/etc/motion/$name"
  dest="/etc/motion/$name"
  if [ ! -f "$src" ]; then
    echo "  Skip $name (not found in repo)"
    continue
  fi
  if [ -L "$dest" ]; then
    echo "  Already linked: $dest -> $(readlink "$dest")"
  elif [ -f "$dest" ]; then
    echo "  Backing up and linking: $dest"
    sudo mv "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)"
    sudo ln -s "$src" "$dest"
  else
    echo "  Linking: $dest -> $src"
    sudo ln -s "$src" "$dest"
  fi
done

echo
echo "Done. Edit files in $REPO_DIR/etc/motion/ and run: sudo systemctl restart motion"
