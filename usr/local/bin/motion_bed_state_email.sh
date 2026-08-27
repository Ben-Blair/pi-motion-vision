#!/bin/bash
set -euo pipefail

NEW_STATE="${1:-unknown}"
PREV_STATE="${2:-unknown}"
SCORE="${3:-na}"
IMAGE_PATH="${4:-}"
T_LOW="${5:-na}"
T_HIGH="${6:-na}"

# Recipient lives in /etc/motion-alerts.env so it stays out of version control.
# See etc/motion-alerts.env.example for the template.
if [ -r /etc/motion-alerts.env ]; then . /etc/motion-alerts.env; fi
TO_EMAIL="${TO_EMAIL:-alerts@example.com}"
LOG_TAG="motion_bed_state_email"
NOW="$(date '+%A, %b %-d at %-I:%M %p')"

# ── Confidence calculation ───────────────────────────────────────────
read -r MADE_PCT UNMADE_PCT <<EOF
$(python3 - <<'PY' "$SCORE" "$T_LOW" "$T_HIGH"
import math, sys
try:
    score, t_low, t_high = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
except Exception:
    print("0 0"); raise SystemExit(0)
if t_high <= t_low:
    print("50 50"); raise SystemExit(0)
midpoint = (t_low + t_high) / 2.0
k = 8.0 / max(1e-6, (t_high - t_low))
x = max(-60.0, min(60.0, k * (score - midpoint)))
unmade = 100.0 / (1.0 + math.exp(-x))
made = 100.0 - unmade
print(f"{round(made):.0f} {round(unmade):.0f}")
PY
)
EOF

# ── State-dependent copy & colors ────────────────────────────────────
ACCENT="#007aff"
CONF_BG="#f3f4f6"
CONF_COLOR="#6b7280"
CONF_TEXT=""

if [ "$NEW_STATE" = "made" ]; then
    SUBJECT="Bedroom Camera \xC2\xB7 Bed Made"
    HEADLINE="Your bed is made"
    MESSAGE="Nice work \xe2\x80\x94 everything looks tidy."
    ACCENT="#34c759"
    CONF_TEXT="${MADE_PCT}% confident"
    CONF_BG="#ecfdf5"
    CONF_COLOR="#059669"
elif [ "$NEW_STATE" = "not_made" ]; then
    SUBJECT="Bedroom Camera \xC2\xB7 Bed Check"
    HEADLINE="Your bed needs attention"
    MESSAGE="It looks like your bed hasn't been made yet."
    ACCENT="#ff9500"
    CONF_TEXT="${UNMADE_PCT}% confident"
    CONF_BG="#fffbeb"
    CONF_COLOR="#b45309"
elif [ "$NEW_STATE" = "indeterminate" ]; then
    SUBJECT="Bedroom Camera \xC2\xB7 Bed Check"
    HEADLINE="Hard to tell right now"
    MESSAGE="The camera couldn't get a clear read. It may need a quick straighten-up."
    ACCENT="#af52de"
    if [ "$UNMADE_PCT" -ge "$MADE_PCT" ]; then
        CONF_TEXT="${UNMADE_PCT}% leaning unmade"
    else
        CONF_TEXT="${MADE_PCT}% leaning made"
    fi
    CONF_BG="#f5f3ff"
    CONF_COLOR="#7c3aed"
else
    SUBJECT="Bedroom Camera \xC2\xB7 Status Update"
    HEADLINE="Bed status updated"
    MESSAGE="A status change was detected."
    ACCENT="#007aff"
    if [ "$UNMADE_PCT" -ge "$MADE_PCT" ]; then
        CONF_TEXT="${UNMADE_PCT}% leaning unmade"
    else
        CONF_TEXT="${MADE_PCT}% leaning made"
    fi
    CONF_BG="#f3f4f6"
    CONF_COLOR="#6b7280"
fi

# ── Build conditional HTML fragments ─────────────────────────────────
IMAGE_HTML=""
if [ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ]; then
    IMAGE_HTML='<tr><td style="line-height:0;font-size:0;background:#f9f9f9;"><img src="cid:BEDIMG" alt="Bedroom snapshot" style="display:block;width:100%;height:auto;"></td></tr>'
fi

CONF_PILL=""
if [ -n "$CONF_TEXT" ]; then
    CONF_PILL="<td align=\"right\"><table cellspacing=\"0\" cellpadding=\"0\"><tr><td style=\"background:${CONF_BG};color:${CONF_COLOR};font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;letter-spacing:0.2px;\">${CONF_TEXT}</td></tr></table></td>"
fi

logger -t "$LOG_TAG" "Sending bed state email: prev=$PREV_STATE new=$NEW_STATE score=$SCORE"

{
    printf 'Subject: %b\nTo: %s\nMIME-Version: 1.0\nContent-Type: multipart/related; boundary="BOUNDARY"\n\n' \
        "$SUBJECT" "$TO_EMAIL"
    printf -- '--BOUNDARY\nContent-Type: text/html; charset="utf-8"\n\n'

    cat <<HTML
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f2f2f7;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI','Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f2f7;padding:28px 16px 20px 16px;">
  <tr><td align="center">
    <table role="presentation" width="520" cellspacing="0" cellpadding="0" style="max-width:520px;width:100%;">

      <!-- Brand -->
      <tr><td style="padding:0 4px 14px 4px;">
        <table width="100%" cellspacing="0" cellpadding="0"><tr>
          <td style="font-size:13px;font-weight:700;color:#1c1c1e;letter-spacing:0.1px;">Bedroom Camera</td>
          <td align="right" style="font-size:11px;color:#aeaeb2;letter-spacing:0.2px;">Bed check</td>
        </tr></table>
      </td></tr>

      <!-- Card -->
      <tr><td>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #e5e5ea;">

          <!-- Accent bar (color changes with state) -->
          <tr><td style="height:3px;background:${ACCENT};font-size:0;line-height:0;">&nbsp;</td></tr>

          <!-- Snapshot (if available) -->
          ${IMAGE_HTML}

          <!-- Timestamp + confidence bar -->
          <tr><td style="background:#fafafa;border-top:1px solid #f0f0f0;border-bottom:1px solid #f0f0f0;padding:9px 20px;">
            <table width="100%" cellspacing="0" cellpadding="0"><tr>
              <td style="font-size:12px;color:#8e8e93;letter-spacing:0.1px;">${NOW}</td>
              ${CONF_PILL}
            </tr></table>
          </td></tr>

          <!-- Heading + description -->
          <tr><td style="padding:22px 32px 6px 32px;">
            <div style="font-size:22px;font-weight:700;color:#1c1c1e;letter-spacing:-0.4px;line-height:1.25;">${HEADLINE}</div>
            <div style="font-size:14px;color:#8e8e93;margin-top:5px;line-height:1.55;">${MESSAGE}</div>
          </td></tr>

          <!-- Bottom spacer -->
          <tr><td style="height:24px;font-size:0;line-height:0;">&nbsp;</td></tr>

        </table>
      </td></tr>

      <!-- Footer -->
      <tr><td style="padding:14px 4px 0 4px;text-align:center;">
        <div style="font-size:11px;color:#c7c7cc;letter-spacing:0.2px;">Bedroom Camera &middot; Automated alert</div>
      </td></tr>

    </table>
  </td></tr>
</table>

</body>
</html>
HTML

    if [ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ]; then
        printf '\n--BOUNDARY\nContent-Type: image/jpeg\nContent-Disposition: inline; filename="bedroom.jpg"\nContent-Transfer-Encoding: base64\nContent-ID: <BEDIMG>\n\n'
        base64 "$IMAGE_PATH"
        printf '\n'
    fi

    printf -- '--BOUNDARY--\n'
} | msmtp "$TO_EMAIL"

logger -t "$LOG_TAG" "Bed state email sent: new=$NEW_STATE"
