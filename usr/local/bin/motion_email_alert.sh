#!/bin/bash
export HOME=/var/lib/motion

TO_EMAIL="ben0r0blair@gmail.com"
SUBJECT="Motion Detected on Raspberry Pi"
BODY="Motion was detected. See attached snapshot."
COOLDOWN_SECONDS=10

STAMP_FILE="/var/lib/motion/.motion_email_last_sent"
BEST_SNAPSHOT="${1:-/var/lib/motion/best_snapshot.jpg}"
META_FILE="${BEST_SNAPSHOT}.meta.json"
ANNOTATED_SNAPSHOT="${BEST_SNAPSHOT}.annotated.jpg"

ARCHIVE_ROOT="/var/lib/motion/emailed"

# RAM snapshots (tmpfs – single source of truth)
RAM_SNAPSHOT_DIR="/var/lib/motion/snapshots"

LOG_TAG="motion_email"

# =========================
# Test mode
# =========================
TEST_MODE="${TEST_MODE:-0}"

if [ "$TEST_MODE" = "1" ]; then
    SUBJECT="Motion EMAIL SELF-TEST on $(hostname)"
    BODY="This is a self-test email. No motion was detected."
    COOLDOWN_SECONDS=0
fi

# =========================
# Email mode (debug or user)
# Read from config file first, then environment, then default to user
# =========================
EMAIL_MODE_CONFIG="/var/lib/motion/.email_mode"
if [ -f "$EMAIL_MODE_CONFIG" ]; then
    EMAIL_MODE="$(tr -d '
 ' < "$EMAIL_MODE_CONFIG" 2>/dev/null || echo "user")"
else
    EMAIL_MODE="${EMAIL_MODE:-user}"
fi


NOW=$(date +%s)

logger -t "$LOG_TAG" "Hook started (TEST_MODE=$TEST_MODE, EMAIL_MODE=$EMAIL_MODE)"

# =========================
# Cooldown check
# =========================
if [ "$TEST_MODE" = "0" ] && [ -f "$STAMP_FILE" ]; then
    LAST_SENT=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
    if [ $((NOW - LAST_SENT)) -lt "$COOLDOWN_SECONDS" ]; then
        logger -t "$LOG_TAG" "Cooldown active, skipping email"
        exit 0
    fi
fi

# =========================
# Verify best snapshot exists
# =========================
if [ "$TEST_MODE" = "0" ] && [ ! -f "$BEST_SNAPSHOT" ]; then
    logger -t "$LOG_TAG" "Best snapshot not found, skipping email"
    exit 0
fi

# =========================
# Archive artifacts (so we can prove what was emailed even after eventproc dirs are deleted)
# =========================
ARCHIVE_DIR="$ARCHIVE_ROOT/$(date '+%Y%m%d-%H%M%S')"
RAW_ARCHIVE="$ARCHIVE_DIR/best_snapshot.jpg"
ANN_ARCHIVE="$ARCHIVE_DIR/best_snapshot.annotated.jpg"
META_ARCHIVE="$ARCHIVE_DIR/best_snapshot.meta.json"

if [ "$TEST_MODE" = "0" ]; then
    mkdir -p "$ARCHIVE_DIR" || true
    # Prefer archiving/attaching the actual BEST source frame (if present in meta),
    # otherwise fall back to the selector output file passed to this script.
    if [ -n "$BEST_SOURCE_FILE" ] && [ -f "$BEST_SOURCE_FILE" ]; then
        cp -a "$BEST_SOURCE_FILE" "$RAW_ARCHIVE" || true
    else
        cp -a "$BEST_SNAPSHOT" "$RAW_ARCHIVE" || true
    fi
    if [ -f "$ANNOTATED_SNAPSHOT" ]; then
        cp -a "$ANNOTATED_SNAPSHOT" "$ANN_ARCHIVE" || true
    fi
    if [ -f "$META_FILE" ]; then
        cp -a "$META_FILE" "$META_ARCHIVE" || true
    fi
    logger -t "$LOG_TAG" "Archived email artifacts to $ARCHIVE_DIR"
fi

# Read metadata (best source, tier, score) if available
BEST_META_TEXT=""
BEST_META_SUBJ=""
BEST_SOURCE_FILE=""
if [ -f "$META_FILE" ]; then
    BEST_META_TEXT="$(python3 - <<'PY' "$META_FILE" 2>/dev/null || true
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    raise SystemExit(0)
src = d.get("best_source_file") or ""
bn = d.get("best_source_basename") or ""
t = d.get("tier")
s = d.get("score")
print(src)
print(f"Best source: {bn}")
print(f"Score: T{t} {s}")
PY
)"
    # First line is best_source_file, remainder is human-readable text
    BEST_SOURCE_FILE="$(printf '%s\n' "$BEST_META_TEXT" | head -n 1)"
    BEST_META_TEXT="$(printf '%s\n' "$BEST_META_TEXT" | tail -n +2)"
    BEST_META_SUBJ="$(python3 - <<'PY' "$META_FILE" 2>/dev/null || true
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    raise SystemExit(0)
bn = d.get("best_source_basename") or ""
t = d.get("tier")
s = d.get("score")
if t is None or s is None:
    raise SystemExit(0)
print(f" [T{t} {float(s):.1f} {bn}]")
PY
)"
fi

# =========================
# Send email
# =========================
if [ "$TEST_MODE" = "0" ]; then
    SUBJECT="${SUBJECT}${BEST_META_SUBJ}"
    logger -t "$LOG_TAG" "Sending email with inline image $BEST_SNAPSHOT"

    {
        echo "Subject: $SUBJECT"
        echo "To: $TO_EMAIL"
        echo "MIME-Version: 1.0"
        echo "Content-Type: multipart/related; boundary=\"BOUNDARY\""
        echo
        echo "--BOUNDARY"
        echo "Content-Type: text/html; charset=\"utf-8\""
        echo
        echo "<html>"
        echo "<body>"
        echo "<p>${BODY}</p>"
        if [ -n "$BEST_META_TEXT" ]; then
            echo "<pre>"
            echo "$BEST_META_TEXT"
            echo "</pre>"
        fi
        if [ -d "$ARCHIVE_DIR" ]; then
            echo "<p>Archive: $ARCHIVE_DIR</p>"
        fi
        echo "<p><img src=\"cid:SNAPSHOTIMG\" style=\"max-width:100%; height:auto;\"></p>"
        echo "</body>"
        echo "</html>"
        echo
        echo "--BOUNDARY"
        echo "Content-Type: image/jpeg"
        echo "Content-Disposition: inline; filename=\"best_snapshot.jpg\""
        echo "Content-Transfer-Encoding: base64"
        echo "Content-ID: <SNAPSHOTIMG>"
        echo
        # Prefer archived copy (persists after eventproc cleanup), fallback to original.
        if [ -f "$RAW_ARCHIVE" ]; then
            base64 "$RAW_ARCHIVE"
        else
            base64 "$BEST_SNAPSHOT"
        fi
        echo

        # Optional annotated attachment (helps debug 'why this frame')
        if [ -f "$ANN_ARCHIVE" ] || [ -f "$ANNOTATED_SNAPSHOT" ]; then
            echo "--BOUNDARY"
            echo "Content-Type: image/jpeg"
            echo "Content-Disposition: attachment; filename=\"best_snapshot.annotated.jpg\""
            echo "Content-Transfer-Encoding: base64"
            echo
            if [ -f "$ANN_ARCHIVE" ]; then
                base64 "$ANN_ARCHIVE"
            else
                base64 "$ANNOTATED_SNAPSHOT"
            fi
            echo
        fi

        echo "--BOUNDARY--"
    } | msmtp "$TO_EMAIL"
else
    logger -t "$LOG_TAG" "Sending self-test email (no attachment)"
    {
        echo "Subject: $SUBJECT"
        echo "To: $TO_EMAIL"
        echo
        echo "Self-test email: snapshot intentionally skipped."
    } | msmtp "$TO_EMAIL"
fi


# =========================
# Update cooldown timestamp
# =========================
if [ "$TEST_MODE" = "0" ]; then
    echo "$NOW" > "$STAMP_FILE"
fi

logger -t "$LOG_TAG" "Hook finished (TEST_MODE=$TEST_MODE)"
