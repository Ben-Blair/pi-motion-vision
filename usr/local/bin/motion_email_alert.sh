#!/bin/bash
export HOME=/var/lib/motion

TO_EMAIL="ben0r0blair@gmail.com"
SUBJECT="Motion Detected on Raspberry Pi"
BODY="Motion was detected. See attached snapshot."
COOLDOWN_SECONDS=10

STAMP_FILE="/var/lib/motion/.motion_email_last_sent"
SNAPSHOT_DIR="/dev/shm/motion"
LOG_TAG="motion_email"

# =========================
# Test mode (used by self-test)
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
# Cooldown check (skip in test mode)
# =========================
if [ "$TEST_MODE" = "0" ] && [ -f "$STAMP_FILE" ]; then
    LAST_SENT=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
    if [ $((NOW - LAST_SENT)) -lt "$COOLDOWN_SECONDS" ]; then
        logger -t "$LOG_TAG" "Cooldown active, skipping email"
        exit 0
    fi
fi

# =========================
# Find newest snapshot
# =========================
SNAPSHOT=""
if [ "$TEST_MODE" = "0" ]; then
    for i in {1..10}; do
        SNAPSHOT=$(ls -t "$SNAPSHOT_DIR"/*.jpg 2>/dev/null | head -n 1)
        [ -n "$SNAPSHOT" ] && break
        sleep 0.1
    done
fi

# =========================
# Send email
# =========================
if [ -n "$SNAPSHOT" ] && [ "$TEST_MODE" = "0" ]; then
    logger -t "$LOG_TAG" "Sending email with attachment $SNAPSHOT"
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
        echo "Content-Disposition: attachment; filename=\"$(basename "$SNAPSHOT")\""
        echo "Content-Transfer-Encoding: base64"
        echo
        base64 "$SNAPSHOT"
        echo
        echo "--BOUNDARY--"
    } | msmtp "$TO_EMAIL"

else
    logger -t "$LOG_TAG" "Sending text-only email"
    {
        echo "Subject: $SUBJECT"
        echo "To: $TO_EMAIL"
        echo
        if [ "$TEST_MODE" = "1" ]; then
            echo "Self-test email: snapshot intentionally skipped."
        else
            echo "Motion detected, but no snapshot found."
        fi
    } | msmtp "$TO_EMAIL"
fi

# =========================
# Cleanup RAM snapshots (real mode only)
# =========================
if [ "$TEST_MODE" = "0" ]; then
    logger -t "$LOG_TAG" "Cleaning old RAM snapshots"
    find "$SNAPSHOT_DIR" -type f -name "*.jpg" -mmin +1 -delete
fi

# =========================
# Update cooldown timestamp (real mode only)
# =========================
if [ "$TEST_MODE" = "0" ]; then
    echo "$NOW" > "$STAMP_FILE"
fi

logger -t "$LOG_TAG" "Hook finished (TEST_MODE=$TEST_MODE)"
