#!/usr/bin/env bash
#
# Install cleanup + disk guard timers for long-term motion storage health.
#
# Usage:
#   cd ~/pi-motion-vision
#   ./scripts/install_motion_housekeeping_timer.sh
#
set -euo pipefail

ENV_FILE="/etc/default/motion-housekeeping"
HOUSEKEEPING_SERVICE="/etc/systemd/system/motion-housekeeping.service"
HOUSEKEEPING_TIMER="/etc/systemd/system/motion-housekeeping.timer"
DISK_GUARD_SERVICE="/etc/systemd/system/motion-disk-guard.service"
DISK_GUARD_TIMER="/etc/systemd/system/motion-disk-guard.timer"

echo "Installing motion housekeeping + disk guard timers"

if [ ! -f "$ENV_FILE" ]; then
  echo "Writing default env file: $ENV_FILE"
  sudo tee "$ENV_FILE" >/dev/null <<'EOF'
MOTION_DIR=/var/lib/motion
BED_MODEL_DIR=/home/bblair23/bed-model
REPO_DIR=/home/bblair23/pi-motion-vision
KEEP_DAYS_VIDEOS=0
KEEP_DAYS_EMAILS=30
KEEP_DAYS_BEST=0
KEEP_DAYS_QUEUE=2
KEEP_DAYS_RESULTS=2
MAX_MOTION_GB=20
PRUNE_BATCH_GB=2
MAX_BEST_GB=5
PRUNE_BEST_BATCH_GB=1
WARN_PERCENT=80
CRITICAL_PERCENT=90
EOF
else
  echo "Keeping existing env file: $ENV_FILE"
fi

sudo tee "$HOUSEKEEPING_SERVICE" >/dev/null <<'EOF'
[Unit]
Description=Prune old Motion artifacts and enforce storage cap

[Service]
Type=oneshot
EnvironmentFile=/etc/default/motion-housekeeping
ExecStart=/usr/local/bin/motion_housekeeping.sh
User=root
Group=root
EOF

sudo tee "$HOUSEKEEPING_TIMER" >/dev/null <<'EOF'
[Unit]
Description=Run Motion housekeeping daily

[Timer]
OnCalendar=*-*-* 03:30:00
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo tee "$DISK_GUARD_SERVICE" >/dev/null <<'EOF'
[Unit]
Description=Check disk usage and trigger Motion housekeeping if critical

[Service]
Type=oneshot
EnvironmentFile=/etc/default/motion-housekeeping
ExecStart=/usr/local/bin/motion_disk_guard.sh
User=root
Group=root
EOF

sudo tee "$DISK_GUARD_TIMER" >/dev/null <<'EOF'
[Unit]
Description=Run Motion disk guard hourly

[Timer]
OnCalendar=hourly
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo chmod 755 /usr/local/bin/motion_housekeeping.sh /usr/local/bin/motion_disk_guard.sh 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable --now motion-housekeeping.timer motion-disk-guard.timer

echo
echo "Installed."
echo "  systemctl status motion-housekeeping.timer"
echo "  systemctl status motion-disk-guard.timer"
echo "  journalctl -u motion-housekeeping.service -n 50 --no-pager"
echo "  journalctl -u motion-disk-guard.service -n 50 --no-pager"
