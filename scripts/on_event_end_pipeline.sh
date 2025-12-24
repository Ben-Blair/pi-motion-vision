#!/bin/bash
# Runs at motion event end

/usr/local/bin/select_best_snapshot.py
/usr/local/bin/motion_email_alert.sh
/usr/local/bin/motion_cleanup.sh

exit 0
