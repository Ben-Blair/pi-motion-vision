#!/bin/bash
set -e

REPO="/opt/motion-config"

echo "Syncing Motion config from live system..."

cp /etc/motion/*.conf "$REPO/etc/motion/"
cp /usr/local/bin/motion_*.sh "$REPO/usr/local/bin/"

cd "$REPO"

if git diff --quiet; then
  echo "No changes to commit."
  exit 0
fi

echo "Changes detected. Running self-test..."

sudo systemctl start motion-selftest.service

if systemctl is-failed --quiet motion-selftest.service; then
  echo "Self-test FAILED. Not committing."
  exit 1
fi

git commit -am "Auto-sync Motion config $(date '+%Y-%m-%d %H:%M:%S')"

echo "Sync complete."
