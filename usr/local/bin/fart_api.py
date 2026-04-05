#!/usr/bin/env python3
"""
Flask API server for the Fart Detection dashboard.

Serves fart events from SQLite, thumbnails, videos, and
detection settings. Intended to run as a systemd service.
"""

import os
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory, abort

app = Flask(__name__)

DB_PATH = os.environ.get("FART_DB_PATH", "/var/lib/motion/fart_events.db")
THUMBNAIL_DIR = Path(os.environ.get("FART_THUMBNAIL_DIR", "/var/lib/motion/fart_thumbnails"))
VIDEO_DIR = Path(os.environ.get("FART_VIDEO_DIR", "/var/lib/motion/videos"))

DEFAULT_SETTINGS = {
    "threshold": "0.18",
    "bt_enabled": "true",
    "tts_message": "Fart Detected",
    "bt_mac": "10:94:97:30:44:66",
}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        _ensure_tables(g.db)
    return g.db


def _ensure_tables(conn: sqlite3.Connection):
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
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# ---------------------------------------------------------------------------
# CORS (allow React dev server on different port)
# ---------------------------------------------------------------------------

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@app.route("/api/events")
def list_events():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 100)
    offset = (page - 1) * per_page

    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM fart_events").fetchone()[0]
    rows = db.execute(
        "SELECT * FROM fart_events ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    ).fetchall()

    events = []
    for r in rows:
        thumb_file = Path(r["thumbnail_path"]).name if r["thumbnail_path"] else None
        video_file = _find_video_for_event(r["event_id"]) if r["event_id"] else None
        events.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "event_id": r["event_id"],
            "confidence": r["confidence"],
            "thumbnail": thumb_file,
            "video": video_file,
        })

    return jsonify({
        "events": events,
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@app.route("/api/events/<int:event_id>")
def get_event(event_id):
    db = get_db()
    row = db.execute("SELECT * FROM fart_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        abort(404)
    thumb_file = Path(row["thumbnail_path"]).name if row["thumbnail_path"] else None
    video_file = _find_video_for_event(row["event_id"]) if row["event_id"] else None
    return jsonify({
        "id": row["id"],
        "timestamp": row["timestamp"],
        "event_id": row["event_id"],
        "confidence": row["confidence"],
        "thumbnail": thumb_file,
        "video": video_file,
    })


def _find_video_for_event(event_id: str) -> str | None:
    """Find the video file whose name contains the event timestamp."""
    if not event_id or not VIDEO_DIR.exists():
        return None
    for f in sorted(VIDEO_DIR.glob("*.mp4"), reverse=True):
        if event_id in f.name:
            return f.name
    return None

# ---------------------------------------------------------------------------
# Static file serving (thumbnails / videos)
# ---------------------------------------------------------------------------

@app.route("/api/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    if not (THUMBNAIL_DIR / filename).is_file():
        abort(404)
    return send_from_directory(str(THUMBNAIL_DIR), filename)


@app.route("/api/videos/<path:filename>")
def serve_video(filename):
    if not (VIDEO_DIR / filename).is_file():
        abort(404)
    return send_from_directory(str(VIDEO_DIR), filename, mimetype="video/mp4")

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SETTINGS_KEYS = {"threshold", "bt_enabled", "tts_message", "bt_mac"}


@app.route("/api/settings", methods=["GET"])
def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in rows if r["key"] in SETTINGS_KEYS}
    for k, v in DEFAULT_SETTINGS.items():
        settings.setdefault(k, v)
    return jsonify(settings)


@app.route("/api/settings", methods=["POST", "OPTIONS"])
def update_settings():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(force=True)
    errors = _validate_settings(data)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    for k in SETTINGS_KEYS:
        if k in data:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(data[k])),
            )
    db.commit()
    return jsonify({"status": "ok"})


def _validate_settings(data: dict) -> dict:
    errors = {}
    if "threshold" in data:
        try:
            val = float(data["threshold"])
            if not (0.1 <= val <= 1.0):
                errors["threshold"] = "Must be between 0.1 and 1.0"
        except (ValueError, TypeError):
            errors["threshold"] = "Must be a number"
    if "tts_message" in data:
        msg = str(data["tts_message"]).strip()
        if not msg:
            errors["tts_message"] = "Message is required"
        elif len(msg) > 100:
            errors["tts_message"] = "Must be 100 characters or fewer"
    if "bt_mac" in data and data.get("bt_enabled") == "true":
        import re
        mac = str(data["bt_mac"]).strip()
        if not re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", mac):
            errors["bt_mac"] = "Invalid MAC address format (XX:XX:XX:XX:XX:XX)"
    return errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("FART_API_HOST", "0.0.0.0")
    port = int(os.environ.get("FART_API_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
