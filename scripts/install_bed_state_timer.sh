#!/usr/bin/env bash
#
# Install systemd service/timer for periodic bed-state checks.
#
# Usage:
#   cd ~/pi-motion-vision
#   ./scripts/install_bed_state_timer.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_FILE="/etc/default/motion-bed-state"
SERVICE_FILE="/etc/systemd/system/motion-bed-state.service"
TIMER_FILE="/etc/systemd/system/motion-bed-state.timer"

echo "Installing motion-bed-state systemd unit/timer"
echo "Repo: $REPO_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "Writing default env file: $ENV_FILE"
  sudo tee "$ENV_FILE" >/dev/null <<EOF
# Bed state checker config
MODEL_CKPT=/home/bblair23/bed-model/model.ckpt
ROI_META=/home/bblair23/bed-model/roi_meta.json
FRAME_FILE=/dev/shm/bed_latest.jpg
FRAME_URL=
STATE_FILE=/home/bblair23/bed-model/bed_state_state.json
WORK_DIR=/home/bblair23/bed-model/work
T_LOW=0.25
T_HIGH=0.40
CONFIRM_COUNT=2
MASK_POLYGON=0
PAD=0
CHECK_INTERVAL_MINUTES=5
CHECK_TIMES=10:00,16:00
TO_EMAIL=ben0r0blair@gmail.com
EOF
else
  echo "Keeping existing env file: $ENV_FILE"
fi

sudo tee "$SERVICE_FILE" >/dev/null <<'EOF'
[Unit]
Description=Motion bed state check (PatchCore)
After=network-online.target motion.service
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/default/motion-bed-state
WorkingDirectory=/home/bblair23/bed-model
ExecStart=/usr/bin/python3 /usr/local/bin/motion_bed_state_check.py --model-ckpt ${MODEL_CKPT} --frame-file ${FRAME_FILE} --frame-url ${FRAME_URL} --state-file ${STATE_FILE} --work-dir ${WORK_DIR} --t-low ${T_LOW} --t-high ${T_HIGH} --confirm-count ${CONFIRM_COUNT} --roi-meta ${ROI_META} --pad ${PAD} --mask-polygon
ExecStartPost=/bin/true
User=bblair23
Group=bblair23
EOF

sudo tee "$TIMER_FILE" >/dev/null <<'EOF'
[Unit]
Description=Run motion-bed-state service every few minutes

[Timer]
OnCalendar=*-*-* 10:00:00
OnCalendar=*-*-* 16:00:00
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "Enabling timer..."
sudo systemctl daemon-reload
sudo systemctl enable --now motion-bed-state.timer

echo
echo "Installed. Verify with:"
echo "  systemctl status motion-bed-state.timer"
echo "  systemctl list-timers | rg motion-bed-state"
echo "  journalctl -u motion-bed-state.service -n 50 --no-pager"
echo
echo "Edit config as needed:"
echo "  sudo nano $ENV_FILE"
