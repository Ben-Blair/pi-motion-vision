#!/bin/bash
#
# Deploy Motion configuration and scripts from this repository
# into the live system (/etc/motion and /usr/local/bin).
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
  sudo cp "$REPO_DIR"/etc/motion/*.conf /etc/motion/
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

