# pi-motion-vision

## Manual Two-Run Bed Check (for testing)

Automatic scheduled checks remain unchanged and still use the 10-minute in-run confirmation.

For fast manual testing, run the checker twice from terminal. The first run stores a check, and
the second run compares against it. An email is sent only if both checks are confidently the same
(`made` + `made` or `not_made` + `not_made`).

```bash
set -a
source /etc/default/motion-bed-state
set +a

/usr/bin/python3 /usr/local/bin/motion_bed_state_check.py \
  --model-ckpt "$MODEL_CKPT" \
  --frame-file "$FRAME_FILE" \
  --frame-url "${FRAME_URL:-}" \
  --state-file "$STATE_FILE" \
  --work-dir "$WORK_DIR" \
  --t-low "$T_LOW" \
  --t-high "$T_HIGH" \
  --confirm-count "$CONFIRM_COUNT" \
  --roi-meta "$ROI_META" \
  --pad "$PAD" \
  --mask-polygon \
  --manual-two-step \
  --manual-session-file "${MANUAL_SESSION_FILE:-/dev/shm/motion_bed_manual_session.json}" \
  --manual-expire-sec "${MANUAL_EXPIRE_SEC:-600}"
```

Run the same command again to perform step 2 comparison.

Expected JSON output fields:
- `manual_step`: `1_saved` or `2_compared`
- `manual_pair_passed`: `true`/`false`/`null`
- `manual_pair_reason`: reason such as `passed`, `check_mismatch`, or `first_check_indeterminate`
- `email_gate_passed` and `emailed`: final email decision for that run

