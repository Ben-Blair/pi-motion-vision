#!/bin/bash
set -e

TO_EMAIL="ben0r0blair@gmail.com"
HOST="$(hostname)"
LOG_TAG="motion_self_test"

fail() {
  MSG="$1"

  # Log to journal
  logger -t "$LOG_TAG" "FAIL: $MSG"

  # Email on failure
  {
    echo "Subject: Motion self-test FAILED on $HOST"
    echo "To: $TO_EMAIL"
    echo
    echo "Motion self-test FAILED on $HOST"
    echo
    echo "Reason:"
    echo "$MSG"
    echo
    echo "Time: $(date)"
  } | msmtp "$TO_EMAIL"

  exit 1
}

echo "== Motion self-test started =="

# 1. Motion service
if ! systemctl is-active --quiet motion; then
  fail "Motion service is not running"
fi
echo "OK: Motion service running"

# 2. Snapshot directory writable (RAM tmpfs)
if ! sudo -u motion test -w /var/lib/motion/snapshots; then
  fail "Motion cannot write to /var/lib/motion/snapshots (RAM snapshots)"
fi
echo "OK: Snapshot directory writable (RAM)"

# 3. Video directory writable
if ! sudo -u motion test -w /var/lib/motion; then
  fail "Motion cannot write to /var/lib/motion (videos)"
fi
echo "OK: Video directory writable"

# 4. Email pipeline test
if ! sudo -u motion TEST_MODE=1 /usr/local/bin/motion_email_alert.sh >/dev/null 2>&1; then
  fail "Motion email script failed"
fi

echo "OK: Email test sent"

echo "== Motion self-test PASSED =="
logger -t "$LOG_TAG" "PASS: Motion self-test passed"
