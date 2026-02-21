#!/bin/bash
set -euo pipefail

NEW_STATE="${1:-unknown}"
PREV_STATE="${2:-unknown}"
SCORE="${3:-na}"
IMAGE_PATH="${4:-}"
T_LOW="${5:-na}"
T_HIGH="${6:-na}"

TO_EMAIL="${TO_EMAIL:-ben0r0blair@gmail.com}"
HOST="$(hostname)"
LOG_TAG="motion_bed_state_email"
# Human-friendly timestamp (no year/seconds/timezone)
NOW="$(date '+%b %d, %I:%M %p')"

# Convert anomaly score to human-readable likelihood percentages.
# Lower score => more likely made. Higher score => more likely not made.
read -r MADE_PCT UNMADE_PCT <<EOF
$(python3 - <<'PY' "$SCORE" "$T_LOW" "$T_HIGH"
import sys
try:
    score = float(sys.argv[1])
    t_low = float(sys.argv[2])
    t_high = float(sys.argv[3])
except Exception:
    print("0 0")
    raise SystemExit(0)

if t_high <= t_low:
    made = 0.0
    unmade = 0.0
elif score <= t_low:
    made = 100.0
    unmade = 0.0
elif score >= t_high:
    made = 0.0
    unmade = 100.0
else:
    unmade = ((score - t_low) / (t_high - t_low)) * 100.0
    made = 100.0 - unmade

print(f"{round(made):.0f} {round(unmade):.0f}")
PY
)
EOF

if [ "$NEW_STATE" = "made" ]; then
  HUMAN_STATE="Bed Made"
  SUBJECT="[$HOST] Nice work - your bed is made"
  HEADLINE="Good job making your bed!"
  MESSAGE="Everything looks tidy right now."
  CARD_COLOR="#1f8f4e"
  CARD_BG="#eaf7ef"
elif [ "$NEW_STATE" = "not_made" ]; then
  HUMAN_STATE="Bed Not Made"
  SUBJECT="[$HOST] Reminder: your bed is not made"
  HEADLINE="Reminder: your bed needs attention"
  MESSAGE="Your bed is currently marked as not made."
  CARD_COLOR="#b85d00"
  CARD_BG="#fff4e8"
else
  HUMAN_STATE="Bed State: $NEW_STATE"
  SUBJECT="[$HOST] Bed status update"
  HEADLINE="Bed status updated"
  MESSAGE="A bed state transition was detected."
  CARD_COLOR="#3d4f78"
  CARD_BG="#eef1f8"
fi

logger -t "$LOG_TAG" "Sending bed state email: prev=$PREV_STATE new=$NEW_STATE score=$SCORE"

{
  echo "Subject: $SUBJECT"
  echo "To: $TO_EMAIL"
  echo "MIME-Version: 1.0"
  echo "Content-Type: multipart/related; boundary=\"BOUNDARY\""
  echo
  echo "--BOUNDARY"
  echo "Content-Type: text/html; charset=\"utf-8\""
  echo
  echo "<html><body style=\"margin:0;padding:0;background:#f6f7fb;font-family:Arial,Helvetica,sans-serif;\">"
  echo "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f6f7fb;padding:20px 0;\">"
  echo "  <tr><td align=\"center\">"
  echo "    <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e7e9f2;\">"
  echo "      <tr><td style=\"padding:22px 24px;background:$CARD_BG;border-bottom:1px solid #e7e9f2;\">"
  echo "        <div style=\"font-size:12px;color:#6b7280;letter-spacing:0.3px;text-transform:uppercase;\">Bedroom status</div>"
  echo "        <div style=\"font-size:26px;line-height:1.3;color:$CARD_COLOR;font-weight:700;margin-top:8px;\">$HEADLINE</div>"
  echo "        <div style=\"font-size:15px;line-height:1.6;color:#334155;margin-top:10px;\">$MESSAGE</div>"
  echo "      </td></tr>"
  echo "      <tr><td style=\"padding:18px 24px 8px 24px;color:#334155;font-size:14px;line-height:1.5;\">"
  echo "        <div><b>Status:</b> $HUMAN_STATE</div>"
  echo "        <div><b>Transition:</b> $PREV_STATE &rarr; $NEW_STATE</div>"
  echo "        <div><b>Time:</b> $NOW</div>"
  echo "        <div><b>Likelihood:</b> $MADE_PCT% made / $UNMADE_PCT% not made</div>"
  echo "      </td></tr>"
  if [ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ]; then
    echo "      <tr><td style=\"padding:8px 24px 8px 24px;\"><img src=\"cid:BEDIMG\" style=\"display:block;width:100%;height:auto;border-radius:10px;border:1px solid #e2e8f0;\"></td></tr>"
  fi
  echo "      <tr><td style=\"padding:10px 24px 22px 24px;color:#64748b;font-size:12px;line-height:1.5;\">"
  echo "        <div><b>Model score:</b> $SCORE</div>"
  echo "        <div><b>Thresholds:</b> T_LOW=$T_LOW, T_HIGH=$T_HIGH</div>"
  echo "        <div><b>Source:</b> $HOST</div>"
  echo "      </td></tr>"
  echo "    </table>"
  echo "  </td></tr>"
  echo "</table>"
  echo "</body></html>"
  echo

  if [ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ]; then
    echo "--BOUNDARY"
    echo "Content-Type: image/jpeg"
    echo "Content-Disposition: inline; filename=\"bed_state.jpg\""
    echo "Content-Transfer-Encoding: base64"
    echo "Content-ID: <BEDIMG>"
    echo
    base64 "$IMAGE_PATH"
    echo
  fi

  echo "--BOUNDARY--"
} | msmtp "$TO_EMAIL"

logger -t "$LOG_TAG" "Bed state email sent: $SUBJECT"
