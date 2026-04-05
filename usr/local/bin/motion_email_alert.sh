#!/bin/bash
export HOME=/var/lib/motion

TO_EMAIL="ben0r0blair@gmail.com"
NOW_HUMAN="$(date '+%A, %b %-d at %-I:%M %p')"
COOLDOWN_SECONDS=10

STAMP_FILE="/var/lib/motion/.motion_email_last_sent"
BEST_SNAPSHOT="${1:-/var/lib/motion/best_snapshot.jpg}"
META_FILE="${BEST_SNAPSHOT}.meta.json"
ANNOTATED_SNAPSHOT="${BEST_SNAPSHOT}.annotated.jpg"
BED_STATE_FILE="/var/lib/motion/bed_state_state.json"

ARCHIVE_ROOT="/var/lib/motion/emailed"
LOG_TAG="motion_email"

TEST_MODE="${TEST_MODE:-0}"
[ "$TEST_MODE" = "1" ] && COOLDOWN_SECONDS=0

EMAIL_MODE_CONFIG="/var/lib/motion/.email_mode"
if [ -f "$EMAIL_MODE_CONFIG" ]; then
    EMAIL_MODE="$(tr -d '[:space:]' < "$EMAIL_MODE_CONFIG" 2>/dev/null || echo "user")"
else
    EMAIL_MODE="${EMAIL_MODE:-user}"
fi

NOW=$(date +%s)
logger -t "$LOG_TAG" "Hook started (TEST_MODE=$TEST_MODE, EMAIL_MODE=$EMAIL_MODE)"

if [ "$TEST_MODE" = "0" ] && [ -f "$STAMP_FILE" ]; then
    LAST_SENT=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
    if [ $((NOW - LAST_SENT)) -lt "$COOLDOWN_SECONDS" ]; then
        logger -t "$LOG_TAG" "Cooldown active, skipping email"
        exit 0
    fi
fi

if [ "$TEST_MODE" = "0" ] && [ ! -f "$BEST_SNAPSHOT" ]; then
    logger -t "$LOG_TAG" "Best snapshot not found, skipping email"
    exit 0
fi

# ── Parse snapshot metadata ──────────────────────────────────────────
QUALITY_LABEL=""
QUALITY_BG="#f3f4f6"
QUALITY_COLOR="#6b7280"
QUALITY_DOT="#9ca3af"
BEST_SOURCE_FILE=""

if [ -f "$META_FILE" ]; then
    mapfile -t _meta < <(python3 - "$META_FILE" 2>/dev/null <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get("best_source_file") or "")
    print(d.get("tier") if d.get("tier") is not None else "")
except Exception:
    print(""); print("")
PY
)
    BEST_SOURCE_FILE="${_meta[0]:-}"
    TIER_RAW="${_meta[1]:-}"
fi

case "$TIER_RAW" in
    1) QUALITY_LABEL="Excellent"; QUALITY_BG="#ecfdf5"; QUALITY_COLOR="#059669"; QUALITY_DOT="#10b981" ;;
    2) QUALITY_LABEL="Good";      QUALITY_BG="#fffbeb"; QUALITY_COLOR="#b45309"; QUALITY_DOT="#f59e0b" ;;
    3) QUALITY_LABEL="Fair";      QUALITY_BG="#fef2f2"; QUALITY_COLOR="#dc2626"; QUALITY_DOT="#ef4444" ;;
esac

# ── Read bed state ───────────────────────────────────────────────────
BED_IS_MADE=""
if [ -f "$BED_STATE_FILE" ]; then
    BED_STATE="$(python3 -c "
import json, sys
try: print(json.load(open('$BED_STATE_FILE')).get('state',''))
except: print('')
" 2>/dev/null)"
    [ "$BED_STATE" = "made" ] && BED_IS_MADE="1"
fi

# ── Subject ──────────────────────────────────────────────────────────
if [ "$TEST_MODE" = "1" ]; then
    SUBJECT="[TEST] Bedroom Camera"
else
    SUBJECT="Bedroom Camera \xC2\xB7 Activity Detected"
fi

# ── Archive artifacts ────────────────────────────────────────────────
ARCHIVE_DIR="$ARCHIVE_ROOT/$(date '+%Y%m%d-%H%M%S')"
RAW_ARCHIVE="$ARCHIVE_DIR/best_snapshot.jpg"
ANN_ARCHIVE="$ARCHIVE_DIR/best_snapshot.annotated.jpg"
META_ARCHIVE="$ARCHIVE_DIR/best_snapshot.meta.json"

if [ "$TEST_MODE" = "0" ]; then
    mkdir -p "$ARCHIVE_DIR" || true
    if [ -n "$BEST_SOURCE_FILE" ] && [ -f "$BEST_SOURCE_FILE" ]; then
        cp -a "$BEST_SOURCE_FILE" "$RAW_ARCHIVE" || true
    else
        cp -a "$BEST_SNAPSHOT" "$RAW_ARCHIVE" || true
    fi
    [ -f "$ANNOTATED_SNAPSHOT" ] && cp -a "$ANNOTATED_SNAPSHOT" "$ANN_ARCHIVE" || true
    [ -f "$META_FILE" ]          && cp -a "$META_FILE"           "$META_ARCHIVE" || true
    logger -t "$LOG_TAG" "Archived to $ARCHIVE_DIR"
fi

# ── Build conditional HTML fragments ─────────────────────────────────
QUALITY_PILL=""
if [ -n "$QUALITY_LABEL" ]; then
    QUALITY_PILL="<td align=\"right\"><table cellspacing=\"0\" cellpadding=\"0\"><tr><td style=\"background:${QUALITY_BG};color:${QUALITY_COLOR};font-size:11px;font-weight:600;padding:4px 10px 4px 8px;border-radius:20px;letter-spacing:0.2px;\"><span style=\"color:${QUALITY_DOT};font-size:8px;\">&#9679;</span>&ensp;${QUALITY_LABEL}</td></tr></table></td>"
fi

BED_BADGE=""
if [ "$BED_IS_MADE" = "1" ]; then
    BED_BADGE='<tr><td style="padding:4px 32px 0 32px;"><table cellspacing="0" cellpadding="0"><tr><td style="background:#ecfdf5;color:#059669;font-size:12px;font-weight:600;padding:5px 12px 5px 10px;border-radius:20px;letter-spacing:0.2px;"><span style="font-size:13px;">&#10003;</span>&ensp;Bed is made</td></tr></table></td></tr>'
fi

# ── Send email ───────────────────────────────────────────────────────
if [ "$TEST_MODE" = "0" ]; then
    logger -t "$LOG_TAG" "Sending email with inline image $BEST_SNAPSHOT"

    {
        printf 'Subject: %b\nTo: %s\nMIME-Version: 1.0\nContent-Type: multipart/related; boundary="BOUNDARY"\n\n' \
            "$SUBJECT" "$TO_EMAIL"
        printf -- '--BOUNDARY\nContent-Type: text/html; charset="utf-8"\n\n'

        cat <<HTML
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f2f2f7;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI','Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

<!-- Outer wrapper -->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f2f7;padding:28px 16px 20px 16px;">
  <tr><td align="center">
    <table role="presentation" width="520" cellspacing="0" cellpadding="0" style="max-width:520px;width:100%;">

      <!-- Brand -->
      <tr><td style="padding:0 4px 14px 4px;">
        <table width="100%" cellspacing="0" cellpadding="0"><tr>
          <td style="font-size:13px;font-weight:700;color:#1c1c1e;letter-spacing:0.1px;">Bedroom Camera</td>
          <td align="right" style="font-size:11px;color:#aeaeb2;letter-spacing:0.2px;">Just now</td>
        </tr></table>
      </td></tr>

      <!-- Main card -->
      <tr><td>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #e5e5ea;">

          <!-- Accent bar -->
          <tr><td style="height:3px;background:#007aff;font-size:0;line-height:0;">&nbsp;</td></tr>

          <!-- Snapshot image -->
          <tr><td style="line-height:0;font-size:0;background:#f9f9f9;">
            <img src="cid:SNAPSHOTIMG" alt="Camera snapshot" style="display:block;width:100%;height:auto;">
          </td></tr>

          <!-- Timestamp + quality bar -->
          <tr><td style="background:#fafafa;border-top:1px solid #f0f0f0;border-bottom:1px solid #f0f0f0;padding:9px 20px;">
            <table width="100%" cellspacing="0" cellpadding="0"><tr>
              <td style="font-size:12px;color:#8e8e93;letter-spacing:0.1px;">$NOW_HUMAN</td>
              $QUALITY_PILL
            </tr></table>
          </td></tr>

          <!-- Heading + description -->
          <tr><td style="padding:22px 32px 6px 32px;">
            <div style="font-size:22px;font-weight:700;color:#1c1c1e;letter-spacing:-0.4px;line-height:1.25;">Activity Detected</div>
            <div style="font-size:14px;color:#8e8e93;margin-top:5px;line-height:1.55;">Your bedroom camera captured motion.</div>
          </td></tr>

          <!-- Bed made badge (shown only when bed is made) -->
          $BED_BADGE

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

        printf '\n--BOUNDARY\nContent-Type: image/jpeg\nContent-Disposition: inline; filename="snapshot.jpg"\nContent-Transfer-Encoding: base64\nContent-ID: <SNAPSHOTIMG>\n\n'
        if [ -f "$RAW_ARCHIVE" ]; then
            base64 "$RAW_ARCHIVE"
        else
            base64 "$BEST_SNAPSHOT"
        fi
        printf '\n'

        if [ -f "$ANN_ARCHIVE" ] || [ -f "$ANNOTATED_SNAPSHOT" ]; then
            printf -- '--BOUNDARY\nContent-Type: image/jpeg\nContent-Disposition: attachment; filename="snapshot_annotated.jpg"\nContent-Transfer-Encoding: base64\n\n'
            if [ -f "$ANN_ARCHIVE" ]; then
                base64 "$ANN_ARCHIVE"
            else
                base64 "$ANNOTATED_SNAPSHOT"
            fi
            printf '\n'
        fi

        printf -- '--BOUNDARY--\n'
    } | msmtp "$TO_EMAIL"

else
    logger -t "$LOG_TAG" "Sending self-test email"
    {
        printf 'Subject: %b\nTo: %s\n\nSelf-test: snapshot intentionally skipped.\n' \
            "$SUBJECT" "$TO_EMAIL"
    } | msmtp "$TO_EMAIL"
fi

if [ "$TEST_MODE" = "0" ]; then
    echo "$NOW" > "$STAMP_FILE"
fi

logger -t "$LOG_TAG" "Hook finished (TEST_MODE=$TEST_MODE)"
