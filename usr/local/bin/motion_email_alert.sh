#!/bin/bash
export HOME=/var/lib/motion

TO_EMAIL="ben0r0blair@gmail.com"
SUBJECT="Motion Detected on Raspberry Pi"
BODY="Motion was detected. See attached snapshot."
COOLDOWN_SECONDS=10

STAMP_FILE="/var/lib/motion/.motion_email_last_sent"
BEST_SNAPSHOT="${1:-/var/lib/motion/best_snapshot.jpg}"

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

NOW=$(date +%s)

logger -t "$LOG_TAG" "Hook started (TEST_MODE=$TEST_MODE)"

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
# Send email
# =========================
if [ "$TEST_MODE" = "0" ]; then
    logger -t "$LOG_TAG" "Sending email with attachment $BEST_SNAPSHOT"
    {
        echo "Subject: $SUBJECT"
        echo "To: $TO_EMAIL"
        echo "MIME-Version: 1.0"
        echo "Content-Type: multipart/mixed; boundary=\"BOUNDARY\""
        echo
        echo "--BOUNDARY"
        echo "Content-Type: text/plain"
        echo
        echo "$BODY"
        echo
        echo "--BOUNDARY"
        echo "Content-Type: image/jpeg"
        echo "Content-Disposition: attachment; filename=\"best_snapshot.jpg\""
        echo "Content-Transfer-Encoding: base64"
        echo
        base64 "$BEST_SNAPSHOT"
        echo
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
