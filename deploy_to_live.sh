#!/bin/bash
#
# Deploy Motion configuration and scripts from this repository
# into the live system (/etc/motion and /usr/local/bin).
#
# For daily editing in Cursor with no copy step: run scripts/link_repo_to_live.sh
# once (or scripts/link_motion_config_to_repo.sh — same thing). Then the repo is
# the live config, hooks, and /usr/local/bin helpers via symlinks.
#
# Usage:
#   cd ~/pi-motion-vision
#   ./deploy_to_live.sh
#
# You may be prompted for sudo if your user cannot write to
# /etc/motion or /usr/local/bin.

set -euo pipefail

# Resolve repo root (directory containing this script)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying Motion config and scripts from: $REPO_DIR"

echo
echo "Step 1/3: Copying Motion config files into /etc/motion..."
sudo mkdir -p /etc/motion
if compgen -G "$REPO_DIR/etc/motion/*.conf" > /dev/null; then
  for src in "$REPO_DIR"/etc/motion/*.conf; do
    base="$(basename "$src")"
    dst="/etc/motion/$base"
    # If /etc/motion is symlinked back to this repo file, skip (cp would fail with "same file").
    if [ -e "$dst" ] && [ "$(readlink -f "$src")" = "$(readlink -f "$dst")" ]; then
      echo "  Skipping $base (already linked to repo file)"
      continue
    fi
    sudo cp "$src" "$dst"
  done
else
  echo "  (No *.conf files found under etc/motion in the repo.)"
fi

echo
echo "Step 2/3: Copying Motion hook scripts into /etc/motion/scripts..."
if [ -d "$REPO_DIR/scripts" ]; then
  sudo mkdir -p /etc/motion/scripts
  sudo cp "$REPO_DIR"/scripts/* /etc/motion/scripts/
fi

echo
echo "Step 3/3: Copying Motion helper binaries into /usr/local/bin..."
if [ -d "$REPO_DIR/usr/local/bin" ]; then
  sudo mkdir -p /usr/local/bin
  sudo cp "$REPO_DIR"/usr/local/bin/motion_*.sh /usr/local/bin/
  sudo chmod 755 /usr/local/bin/motion_*.sh
  if [ -f "$REPO_DIR/usr/local/bin/on_picture_save_score.sh" ]; then
    sudo cp "$REPO_DIR/usr/local/bin/on_picture_save_score.sh" /usr/local/bin/
    sudo chmod 755 /usr/local/bin/on_picture_save_score.sh
  fi
  for hook in on_event_start on_event_end_pipeline on_movie_end; do
    if [ -f "$REPO_DIR/scripts/${hook}.sh" ]; then
      sudo cp "$REPO_DIR/scripts/${hook}.sh" "/usr/local/bin/${hook}.sh"
      sudo chmod 755 "/usr/local/bin/${hook}.sh"
    fi
  done
  if compgen -G "$REPO_DIR/usr/local/bin/motion_*.py" > /dev/null; then
    sudo cp "$REPO_DIR"/usr/local/bin/motion_*.py /usr/local/bin/
    sudo chmod 755 /usr/local/bin/motion_*.py
  fi
fi

echo
echo "Restarting motion service to apply new config..."
if sudo systemctl restart motion; then
  echo "motion service restarted successfully."
  echo
  echo "Recent motion status:"
  sudo systemctl status motion --no-pager -l | sed -n '1,15p' || true
else
  echo "WARNING: motion service failed to restart. Run 'sudo systemctl status motion' for details."
fi

echo
echo "Deployment complete."

