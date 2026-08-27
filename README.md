# pi-motion-vision

A Raspberry Pi security camera system built on top of [Motion](https://motion-project.github.io/).
Instead of emailing every frame that trips the detector, it scores each snapshot from an event,
picks the single best one, and sends that in an alert email. It also runs a PatchCore anomaly model
on a schedule to report whether a bed has been made, and classifies event audio with YAMNet.

Everything runs on the Pi itself. There is no cloud dependency beyond optional Supabase clip upload
and outbound SMTP.

## What it does

- **Motion capture.** Motion watches `/dev/video0` at 1280x720/15fps, writes MP4 clips and JPEG
  snapshots, and calls hook scripts on event boundaries.
- **Best-snapshot selection.** Every saved snapshot is queued to a background worker that scores it.
  At event end, a batch pass re-scores the whole event and picks the highest-scoring frame, so the
  alert email contains the clearest shot rather than an arbitrary one.
- **Email alerts.** The chosen snapshot is embedded inline in an HTML email sent via `msmtp`,
  with a cooldown to avoid floods.
- **Bed-state detection.** A timer periodically scores the latest frame against an
  [Anomalib](https://github.com/openvinotoolkit/anomalib) PatchCore checkpoint, applies hysteresis
  plus repeat confirmation, and emails only when the made/not-made state actually changes.
- **Audio classification.** During an event, `fart_detector.py` records from a USB mic and runs
  YAMNet (TFLite) over the stream, optionally announcing results over Bluetooth audio.
- **Housekeeping.** Timers prune old clips, guard against filling the disk, and run a self-test.

## Architecture

Motion drives the whole pipeline through its hook scripts:

| Motion hook | Script | Purpose |
| --- | --- | --- |
| `on_event_start` | `scripts/on_event_start.sh` | Assign an event ID, start audio capture |
| `on_picture_save` | `usr/local/bin/on_picture_save_score.sh` | Enqueue snapshot, refresh `/dev/shm/bed_latest.jpg` |
| `on_movie_end` | `scripts/on_movie_end.sh` | Mux audio, optionally upload the clip to Supabase |
| `on_event_end` | `scripts/on_event_end_pipeline.sh` | Flush scoring, select best frame, email, clean up |

The hooks stay fast and non-blocking; the expensive scoring happens in
`motion-score-worker.service`, which consumes a queue under `/var/lib/motion/score_queue`.

Snapshots are written to a tmpfs (`/var/lib/motion/snapshots`) to spare the SD card, and are
deleted per-event once the best frame has been chosen.

### systemd units

| Unit | Role |
| --- | --- |
| `motion.service` | The Motion daemon (with a drop-in override for config path and env) |
| `motion-score-worker.service` | Queue-based snapshot scoring worker |
| `motion-bed-state.timer` | Periodic bed-state check |
| `motion-housekeeping.timer` | Nightly pruning of clips and artifacts |
| `motion-disk-guard.timer` | Emergency cleanup when disk runs low |
| `motion-selftest.service` | Verifies services, writable paths, and the email path |
| `fart-api.service` | JSON API backing the dashboard |

## Repository layout

```
etc/motion/          Motion daemon and per-camera configs
etc/systemd/         Unit files for the API and audio ACL helper
scripts/             Motion hook scripts and installers
usr/local/bin/       Long-running scripts installed onto PATH
staging/             Worker/scoring scripts staged before promotion
models/              YAMNet TFLite model and class map
assets/              Alert sounds and voice recordings
fart-dashboard/      React + Vite dashboard for audio events
school-supabase-upload/  Standalone clip-upload demo (separate git root)
```

`scripts/link_repo_to_live.sh` symlinks the live system paths (`/etc/motion`, `/usr/local/bin`)
straight into this repo, so editing a file here changes what actually runs. That is how the Pi in
this project is set up. `deploy_to_live.sh` does the opposite, replacing symlinks with copies.

## Setup

### Requirements

- Raspberry Pi (developed on a Pi 5 running Raspberry Pi OS, 64-bit)
- A V4L2-compatible camera at `/dev/video0`
- `motion`, `msmtp`, `ffmpeg`, `python3`
- Python packages: `opencv-python`, `numpy`, and for bed state `anomalib[full,cpu]`

### 1. Configure outbound email

Alert mail is sent with `msmtp`. Create `~/.msmtprc` with your SMTP provider's details and lock it
down; for Gmail this means an app password, not your account password:

```bash
chmod 600 ~/.msmtprc
```

### 2. Set the recipient address

The recipient is read at runtime from `/etc/motion-alerts.env`, which is deliberately **not** in
version control:

```bash
sudo cp etc/motion-alerts.env.example /etc/motion-alerts.env
sudo nano /etc/motion-alerts.env      # set TO_EMAIL
sudo chmod 644 /etc/motion-alerts.env
```

It must stay world-readable, because the alert scripts run as the unprivileged `motion` user.
Any script also honours a `TO_EMAIL` environment variable, which takes precedence.

### 3. Install the scripts and configs

```bash
./scripts/link_repo_to_live.sh
sudo systemctl restart motion
```

### 4. Optional: Supabase clip upload

```bash
cp etc/supabase.env.example etc/supabase.env   # gitignored
# fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_BUCKET
pip install -r requirements-supabase.txt
```

Then set `SUPABASE_UPLOAD=1` in the `motion.service` drop-in to enable upload from `on_movie_end`.

### 5. Optional: weather-aware audio announcements

```bash
sudo cp etc/fart-detector.env.example /etc/fart-detector.env
sudo nano /etc/fart-detector.env      # OpenWeatherMap key + coordinates
sudo chmod 600 /etc/fart-detector.env
```

### 6. Optional: bed-state detection

Bed state needs a trained Anomalib PatchCore checkpoint. **The checkpoint is not in this repo** —
it is ~135 MB, above GitHub's file size limit, and is specific to one camera and one bed anyway.

Train your own against images of the made bed, then place the artifacts outside the repo:

```
~/bed-model/model.ckpt        PatchCore checkpoint
~/bed-model/roi_meta.json     ROI crop/mask describing the bed region
~/bed-model/work/             Scratch space for annotated debug frames
```

Then install the timer, which writes `/etc/default/motion-bed-state`:

```bash
./scripts/install_bed_state_timer.sh
```

Tune `T_LOW` / `T_HIGH` (hysteresis thresholds) and `CONFIRM_COUNT` in that env file.

## Configuration reference

Sensitivity lives in `etc/motion/camera0.conf`. The values that matter most:

| Setting | Meaning |
| --- | --- |
| `threshold` | Pixels that must change before motion triggers |
| `minimum_motion_frames` | Consecutive frames required, filters single-frame noise |
| `lightswitch_percent` | Suppresses triggers from sudden IR/exposure shifts |
| `event_gap` | Seconds of quiet before an event is considered over |

Scoring behaviour is tuned through environment variables on `motion-score-worker.service`, such as
`MOTION_SNAPSHOT_KEEP_N` and the `MOTION_T2_STABILITY_*` stability-gate settings.

## Manual two-run bed check (for testing)

Automatic scheduled checks are unchanged and still use the in-run confirmation. For fast manual
testing, run the checker twice from a terminal. The first run stores a check and the second compares
against it; an email is sent only if both checks agree confidently.

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

Run the same command again to perform the step 2 comparison.

Expected JSON output fields:

- `manual_step`: `1_saved` or `2_compared`
- `manual_pair_passed`: `true` / `false` / `null`
- `manual_pair_reason`: e.g. `passed`, `check_mismatch`, `first_check_indeterminate`
- `email_gate_passed` and `emailed`: the final email decision for that run

## Troubleshooting

Run the self-test, which checks the services, writable paths, and the email pipeline end to end:

```bash
sudo /usr/local/bin/motion_self_test.sh
```

Follow the pipeline live in the journal:

```bash
journalctl -t motion_event -t motion_email -t motion_bed_state_email -f
```

Set `MOTION_DEBUG_SCORING=1` in the `motion.service` drop-in to have the event-end pass write
annotated scoring frames to `/var/lib/motion/debug_scoring/latest`.

## Secrets

No credentials are stored in this repository. These files are gitignored and must be created
locally from their `.example` counterparts:

| File | Contains |
| --- | --- |
| `/etc/motion-alerts.env` | Alert recipient address |
| `etc/supabase.env` | Supabase project URL and service role key |
| `/etc/fart-detector.env` | OpenWeatherMap API key and coordinates |
| `~/.msmtprc` | SMTP credentials |
