#!/usr/bin/env python3
"""
Fart detection daemon for the motion pipeline.

Replaces arecord: records mic audio to WAV while simultaneously running
YAMNet inference to detect fart sounds. On detection, logs the event to
SQLite and triggers the Bluetooth announcement script.

Responds to SIGTERM/SIGINT for clean shutdown (WAV header finalized).
Responds to SIGUSR1 to stop recording, finalize WAV, and start a new one
(used by on_movie_end.sh for segment splits).
"""

import argparse
import csv
import json
import logging
import os
import re
import signal
import sqlite3
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fart_detector")

# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------
MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "models"
FALLBACK_MODEL_DIR = Path("/home/bblair23/pi-motion-vision/models")
DB_PATH = Path("/var/lib/motion/fart_events.db")
SETTINGS_PATH = Path("/var/lib/motion/fart_settings.json")
BT_SCRIPT = Path("/usr/local/bin/bt_announce.sh")
SNAPSHOT_DIR = Path("/var/lib/motion/snapshots")
FART_THUMBNAILS_DIR = Path("/var/lib/motion/fart_thumbnails")
YAMNET_SAMPLE_RATE = 16000
RECORD_SAMPLE_RATE = 44100
FART_CLASS_INDEX = 55
# Lower = more detections (more false positives). YAMNet "Fart" scores are often modest.
DEFAULT_THRESHOLD = 0.18
COOLDOWN_SECONDS = 3
# Classify overlapping 1s windows so brief sounds are not split across chunk edges.
CLASSIFY_WINDOW_SEC = 1.0
CLASSIFY_HOP_SEC = 0.5
THRESHOLD_REFRESH_SEC = 5.0
WEATHER_ENV_FILE = Path("/etc/fart-detector.env")
WEATHER_CACHE_TTL = 600  # 10 minutes

# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

_weather_cache: dict = {"temp_f": None, "fetched_at": 0.0}


def _load_weather_env() -> dict[str, str]:
    """Read key=value pairs from /etc/fart-detector.env (no shell expansion)."""
    env: dict[str, str] = {}
    if not WEATHER_ENV_FILE.exists():
        return env
    try:
        text = WEATHER_ENV_FILE.read_text()
    except OSError as e:
        log.warning("Cannot read %s: %s", WEATHER_ENV_FILE, e)
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _fetch_weather() -> float | None:
    """Return current temperature in Fahrenheit, or None on failure. Cached."""
    now = time.time()
    if _weather_cache["temp_f"] is not None and (now - _weather_cache["fetched_at"]) < WEATHER_CACHE_TTL:
        return _weather_cache["temp_f"]

    env = _load_weather_env()
    api_key = env.get("OPENWEATHER_API_KEY", "")
    lat = env.get("OPENWEATHER_LAT", "")
    lon = env.get("OPENWEATHER_LON", "")
    if not api_key or not lat or not lon or api_key == "your_key_here":
        log.debug("Weather env not configured, skipping weather fetch")
        return None

    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lon}&units=imperial&appid={api_key}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fart-detector/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        temp_f = float(data["main"]["temp"])
        _weather_cache["temp_f"] = temp_f
        _weather_cache["fetched_at"] = now
        log.info("Weather fetched: %.1f°F", temp_f)
        return temp_f
    except Exception as e:
        log.warning("Weather fetch failed: %s", e)
        return _weather_cache["temp_f"]  # stale cache is better than nothing


def _temp_to_descriptor(temp_f: float) -> str:
    if temp_f < 20:
        return "Frozen"
    if temp_f < 32:
        return "Frigid"
    if temp_f < 45:
        return "Brisk"
    if temp_f < 60:
        return "Crisp"
    if temp_f < 75:
        return "Mild"
    if temp_f < 85:
        return "Warm"
    if temp_f < 95:
        return "Toasty"
    return "Scorching"


def build_tts_message() -> str:
    """Return a weather-qualified announcement, or a plain fallback."""
    try:
        temp_f = _fetch_weather()
        if temp_f is None:
            return "Fart detected"
        desc = _temp_to_descriptor(temp_f)
        return f"{desc} fart detected"
    except Exception as e:
        log.warning("Failed to build weather TTS message: %s", e)
        return "Fart detected"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fart_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_id TEXT,
            confidence REAL,
            thumbnail_path TEXT,
            video_path TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    defaults = {
        "threshold": str(DEFAULT_THRESHOLD),
        "bt_enabled": "true",
        "tts_message": "Fart Detected",
        "bt_mac": "",
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def log_fart_event(conn: sqlite3.Connection, event_id: str, confidence: float, thumb: str):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO fart_events (timestamp, event_id, confidence, thumbnail_path) "
        "VALUES (?, ?, ?, ?)",
        (ts, event_id, round(confidence, 4), thumb),
    )
    conn.commit()
    log.info("Fart event logged: confidence=%.4f event=%s", confidence, event_id)

# ---------------------------------------------------------------------------
# YAMNet loader
# ---------------------------------------------------------------------------

def load_yamnet(model_path: str):
    """Load YAMNet TFLite model, return interpreter with allocated tensors."""
    from ai_edge_litert.interpreter import Interpreter
    interp = Interpreter(model_path=model_path)
    interp.allocate_tensors()
    return interp


def load_class_names(csv_path: str) -> list[str]:
    names: list[str] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            names.append(row["display_name"])
    return names

# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def detect_alsa_plughw_mic() -> str | None:
    """Pick capture device like on_movie_end.sh: 'microphone' in card name, else first card."""
    try:
        r = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("card ")]
    pick = None
    for ln in lines:
        if "microphone" in ln.lower():
            pick = ln
            break
    if pick is None and lines:
        pick = lines[0]
    if not pick:
        return None
    m = re.search(r"card (\d+):.*device (\d+):", pick)
    if not m:
        return None
    return f"plughw:{m.group(1)},{m.group(2)}"


def find_input_device() -> int | None:
    """Find the best PortAudio input device index; prefer one named 'microphone'."""
    try:
        devs = sd.query_devices()
    except Exception:
        return None
    fallback = None
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) < 1:
            continue
        if fallback is None:
            fallback = i
        if "microphone" in d.get("name", "").lower():
            return i
    return fallback


def resample_to_yamnet(audio_f32: np.ndarray, src_sr: int) -> np.ndarray:
    """Resample mono float32 audio from src_sr to YAMNet rate (linear interpolation)."""
    if src_sr == YAMNET_SAMPLE_RATE:
        return audio_f32.astype(np.float32)
    length_out = int(len(audio_f32) * YAMNET_SAMPLE_RATE / src_sr)
    if length_out < 1:
        return np.zeros(0, dtype=np.float32)
    indices = np.linspace(0, len(audio_f32) - 1, length_out)
    return np.interp(indices, np.arange(len(audio_f32)), audio_f32).astype(np.float32)

# ---------------------------------------------------------------------------
# WAV writer (manual, so we can finalize on signal)
# ---------------------------------------------------------------------------

class WavWriter:
    """Incrementally writes a WAV file, finalizing the RIFF header on close."""

    def __init__(self, path: str, rate: int = RECORD_SAMPLE_RATE, channels: int = 1):
        self.path = path
        self.rate = rate
        self.channels = channels
        self.sample_width = 2  # 16-bit
        self._f = open(path, "wb")
        self._data_bytes = 0
        self._write_header()

    def _write_header(self):
        self._f.write(b"RIFF")
        self._f.write(struct.pack("<I", 0))  # placeholder
        self._f.write(b"WAVE")
        self._f.write(b"fmt ")
        self._f.write(struct.pack("<I", 16))
        self._f.write(struct.pack("<HHIIHH", 1, self.channels, self.rate,
                                  self.rate * self.channels * self.sample_width,
                                  self.channels * self.sample_width,
                                  self.sample_width * 8))
        self._f.write(b"data")
        self._f.write(struct.pack("<I", 0))  # placeholder

    def write(self, pcm_int16: np.ndarray):
        raw = pcm_int16.astype(np.int16).tobytes()
        self._f.write(raw)
        self._data_bytes += len(raw)

    def close(self):
        if self._f.closed:
            return
        self._f.seek(4)
        self._f.write(struct.pack("<I", 36 + self._data_bytes))
        self._f.seek(40)
        self._f.write(struct.pack("<I", self._data_bytes))
        self._f.flush()
        self._f.close()

# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------

class FartDetector:
    def __init__(self, args):
        self.args = args
        self.event_id = args.event_id
        self.wav_path = args.wav_path
        self.running = True
        self._restart_wav = False

        model_dir = MODEL_DIR if MODEL_DIR.exists() else FALLBACK_MODEL_DIR
        model_path = str(model_dir / "yamnet.tflite")
        csv_path = str(model_dir / "yamnet_class_map.csv")

        log.info("Loading YAMNet from %s", model_path)
        self.interpreter = load_yamnet(model_path)
        self.class_names = load_class_names(csv_path)
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

        self.db = init_db(DB_PATH)
        self.threshold = float(get_setting(self.db, "threshold") or DEFAULT_THRESHOLD)

        self.wav_writer: WavWriter | None = None
        self.last_fart_time = 0.0
        self._last_threshold_refresh = 0.0
        self._lock = threading.Lock()

        FART_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGUSR1, self._handle_restart_wav)
        self._record_sr = RECORD_SAMPLE_RATE

    def _handle_stop(self, signum, frame):
        log.info("Received signal %d, shutting down", signum)
        self.running = False

    def _handle_restart_wav(self, signum, frame):
        log.info("SIGUSR1: will finalize WAV and start new segment")
        self._restart_wav = True

    def _grab_thumbnail(self) -> str:
        """Copy the most recent snapshot as a fart event thumbnail."""
        try:
            jpgs = sorted(SNAPSHOT_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            if not jpgs:
                return ""
            src = jpgs[-1]
            ts = time.strftime("%Y%m%d-%H%M%S")
            dest = FART_THUMBNAILS_DIR / f"fart_{ts}.jpg"
            shutil.copy2(str(src), str(dest))
            return str(dest)
        except Exception as e:
            log.warning("Failed to grab thumbnail: %s", e)
            return ""

    def _trigger_bt_announce(self):
        bt_enabled = get_setting(self.db, "bt_enabled")
        if bt_enabled != "true":
            log.info("Bluetooth announcement disabled in settings")
            return
        if not BT_SCRIPT.exists():
            log.warning("BT script not found at %s", BT_SCRIPT)
            return
        message = build_tts_message()
        log.info("BT announcement: %s", message)
        try:
            subprocess.Popen(
                [str(BT_SCRIPT), message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.warning("Failed to launch BT announce: %s", e)

    def _classify(self, audio_16k: np.ndarray) -> tuple[float, str]:
        """Run YAMNet on a ~1s chunk, return (fart_confidence, top_class_name)."""
        expected_len = self.input_details[0]["shape"][-1]
        if len(audio_16k) < expected_len:
            audio_16k = np.pad(audio_16k, (0, expected_len - len(audio_16k)))
        elif len(audio_16k) > expected_len:
            audio_16k = audio_16k[:expected_len]

        audio_16k = audio_16k.astype(np.float32)
        self.interpreter.set_tensor(self.input_details[0]["index"], audio_16k)
        self.interpreter.invoke()
        scores = self.interpreter.get_tensor(self.output_details[0]["index"])
        if scores.ndim > 1:
            scores = scores.mean(axis=0)
        top_idx = int(np.argmax(scores))
        top_name = self.class_names[top_idx] if top_idx < len(self.class_names) else "?"
        fart_score = float(scores[FART_CLASS_INDEX])
        return fart_score, top_name

    def run(self):
        chunk_seconds = 1.0
        alsa_dev = detect_alsa_plughw_mic()
        arecord_proc: subprocess.Popen | None = None
        stream = None

        if alsa_dev:
            self._record_sr = RECORD_SAMPLE_RATE
            chunk_samples = int(self._record_sr * chunk_seconds)
            self.wav_writer = WavWriter(self.wav_path, self._record_sr)
            log.info("Recording to %s (rate=%d)", self.wav_path, self._record_sr)
            log.info("Using ALSA capture via arecord: %s", alsa_dev)
            try:
                arecord_proc = subprocess.Popen(
                    [
                        "arecord",
                        "-D",
                        alsa_dev,
                        "-f",
                        "S16_LE",
                        "-r",
                        str(self._record_sr),
                        "-c",
                        "1",
                        "-t",
                        "raw",
                        "-q",
                        "-",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                )
                time.sleep(0.1)
                if arecord_proc.poll() is not None:
                    err = ""
                    if arecord_proc.stderr:
                        err = arecord_proc.stderr.read().decode(errors="replace")[:800]
                    log.error("arecord failed to start: %s", err or "(no stderr)")
                    if self.wav_writer:
                        self.wav_writer.close()
                    return
            except Exception as e:
                log.error("Failed to spawn arecord: %s", e)
                if self.wav_writer:
                    self.wav_writer.close()
                return
        else:
            log.warning("arecord -l found no mic; falling back to sounddevice")
            try:
                dev_idx = find_input_device()
                if dev_idx is None:
                    log.error("No input device for sounddevice")
                    return
                dev_info = sd.query_devices(dev_idx)
                self._record_sr = int(float(dev_info.get("default_samplerate") or RECORD_SAMPLE_RATE))
                chunk_samples = int(self._record_sr * chunk_seconds)
                self.wav_writer = WavWriter(self.wav_path, self._record_sr)
                log.info("Recording to %s (rate=%d)", self.wav_path, self._record_sr)
                log.info("Using input device %d: %s", dev_idx, dev_info["name"])
                stream = sd.InputStream(
                    samplerate=self._record_sr,
                    channels=1,
                    dtype="int16",
                    blocksize=chunk_samples,
                    device=dev_idx,
                )
                stream.start()
            except Exception as e:
                log.error("Failed to open audio stream: %s", e)
                if self.wav_writer:
                    self.wav_writer.close()
                return

        classify_buffer = np.zeros(0, dtype=np.float32)
        window_samples = int(YAMNET_SAMPLE_RATE * CLASSIFY_WINDOW_SEC)
        hop_samples = int(YAMNET_SAMPLE_RATE * CLASSIFY_HOP_SEC)
        frame_bytes = chunk_samples * 2

        log.info("Fart detector running (threshold=%.2f, event=%s)", self.threshold, self.event_id)

        try:
            while self.running:
                if self._restart_wav:
                    self._do_restart_wav()
                    self._restart_wav = False

                if arecord_proc is not None:
                    assert arecord_proc.stdout is not None
                    raw = b""
                    while len(raw) < frame_bytes and self.running:
                        piece = arecord_proc.stdout.read(frame_bytes - len(raw))
                        if not piece:
                            break
                        raw += piece
                    if len(raw) < frame_bytes:
                        if arecord_proc.poll() is not None:
                            err = ""
                            if arecord_proc.stderr:
                                err = arecord_proc.stderr.read().decode(errors="replace")[:800]
                            log.error("arecord ended (rc=%s): %s", arecord_proc.returncode, err)
                            break
                        time.sleep(0.02)
                        continue
                    pcm = np.frombuffer(raw, dtype=np.int16).copy()
                else:
                    assert stream is not None
                    try:
                        data, overflowed = stream.read(chunk_samples)
                    except Exception as e:
                        log.warning("Audio read error: %s", e)
                        time.sleep(0.1)
                        continue

                    if overflowed:
                        log.debug("Audio buffer overflow (non-fatal)")

                    pcm = data[:, 0]

                with self._lock:
                    if self.wav_writer:
                        self.wav_writer.write(pcm)

                audio_f32 = pcm.astype(np.float32) / 32768.0
                audio_16k = resample_to_yamnet(audio_f32, self._record_sr)
                classify_buffer = np.concatenate([classify_buffer, audio_16k])

                now = time.time()
                if now - self._last_threshold_refresh >= THRESHOLD_REFRESH_SEC:
                    self._last_threshold_refresh = now
                    new_thr = float(get_setting(self.db, "threshold") or DEFAULT_THRESHOLD)
                    if abs(new_thr - self.threshold) > 1e-6:
                        self.threshold = new_thr
                        log.info("Threshold updated from settings: %.2f", new_thr)

                while len(classify_buffer) >= window_samples:
                    chunk_16k = classify_buffer[:window_samples]
                    classify_buffer = classify_buffer[hop_samples:]

                    fart_conf, top_class = self._classify(chunk_16k)
                    log.debug("top=%s fart=%.4f", top_class, fart_conf)

                    now_ts = time.time()
                    if fart_conf >= self.threshold and (now_ts - self.last_fart_time) > COOLDOWN_SECONDS:
                        self.last_fart_time = now_ts
                        log.info(
                            "FART DETECTED! confidence=%.4f (top_class=%s)",
                            fart_conf, top_class,
                        )
                        thumb = self._grab_thumbnail()
                        log_fart_event(self.db, self.event_id, fart_conf, thumb)
                        self._trigger_bt_announce()

        finally:
            if stream is not None:
                stream.stop()
                stream.close()
            if arecord_proc is not None:
                arecord_proc.terminate()
                try:
                    arecord_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    arecord_proc.kill()
            with self._lock:
                if self.wav_writer:
                    self.wav_writer.close()
                    log.info("WAV finalized: %s", self.wav_path)

    def _do_restart_wav(self):
        """Finalize current WAV and open a new one (for movie segment splits).

        The finalized WAV is renamed to <path>.mux so on_movie_end.sh can pick
        it up for muxing without racing against the new segment that continues
        recording at the original path.
        """
        with self._lock:
            old_path = self.wav_path
            if self.wav_writer:
                self.wav_writer.close()
                log.info("WAV segment finalized: %s", old_path)
            mux_path = old_path + ".mux"
            try:
                os.rename(old_path, mux_path)
                log.info("Renamed for mux: %s -> %s", old_path, mux_path)
            except OSError as e:
                log.warning("Failed to rename WAV for mux: %s", e)
            self.wav_writer = WavWriter(old_path, self._record_sr)
            log.info("New WAV segment: %s", old_path)


def main():
    parser = argparse.ArgumentParser(description="Fart detection + audio recording daemon")
    parser.add_argument("--wav-path", required=True, help="Output WAV file path")
    parser.add_argument("--event-id", default="unknown", help="Motion event ID")
    parser.add_argument("--pid-file", default="/dev/shm/motion_audio.pid", help="PID file path")
    args = parser.parse_args()

    pid_path = Path(args.pid_file)
    pid_path.write_text(str(os.getpid()))

    try:
        detector = FartDetector(args)
        detector.run()
    finally:
        pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
