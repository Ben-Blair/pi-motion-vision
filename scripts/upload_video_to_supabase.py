# Manual one-off upload of a specific file (default: motion-videos/input.mp4).
#
#   source etc/supabase.env
#   source .venv/bin/activate
#   python scripts/upload_video_to_supabase.py
#
# Override file:  export UPLOAD_VIDEO=/path/to/clip.mp4
#
# Other scripts import upload_mp4_path() from this module.
#
# Whole-script picture (bottom → top in code): main() chooses a file;
# upload_mp4_path() optionally compresses then uploads via the Supabase client;
# _supabase() creates that client from env; compress_video() is the ffmpeg
# shrink step used only when the file is over the size limit.

# --- Standard library: env vars, temp files, filesystem paths ---
import os
import tempfile
from pathlib import Path

# --- Third party: ffmpeg wrapper (needs `ffmpeg` binary on PATH); Supabase Python client ---
import ffmpeg
from supabase import create_client, Client


# =============================================================================
# VIDEO COMPRESSION (optional preprocessing)
# Big picture: Motion clips can be huge. Supabase Free caps uploads at ~50 MB.
# This block is only the “shrink the file with ffmpeg” machinery. It does not
# touch the cloud; upload_mp4_path() calls it when the file is over budget.
# =============================================================================
def compress_video(video_full_path, output_file_name, target_size):
    """Re-encode video with libx264 so output size is near a target (two-pass).

    `target_size` is in the same units as common “target file size” formulas (see links).
    Pass 1 gathers stats; pass 2 writes `output_file_name`.
    """
    # Reference: https://en.wikipedia.org/wiki/Bit_rate#Encoding_bit_rate
    # Stack Overflow: https://stackoverflow.com/questions/64430805/how-to-compress-video-to-target-size-by-python
    min_audio_bitrate = 32000
    max_audio_bitrate = 256000

    # Read container duration and streams from the source file (no re-encode yet).
    probe = ffmpeg.probe(video_full_path)
    duration = float(probe["format"]["duration"])
    # Some clips have no audio track; avoid crashing on missing bit_rate.
    audio_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    if audio_stream and audio_stream.get("bit_rate"):
        audio_bitrate = float(audio_stream["bit_rate"])
    else:
        audio_bitrate = 0.0
    # Total bits budget for the whole file, spread across `duration` seconds (mux overhead factor).
    target_total_bitrate = (target_size * 1024 * 8) / (1.073741824 * duration)

    # If the source audio would eat too much of the budget, shrink (or clamp) audio bitrate.
    if audio_bitrate > 0 and 10 * audio_bitrate > target_total_bitrate:
        audio_bitrate = target_total_bitrate / 10
        if audio_bitrate < min_audio_bitrate < target_total_bitrate:
            audio_bitrate = min_audio_bitrate
        elif audio_bitrate > max_audio_bitrate:
            audio_bitrate = max_audio_bitrate
    # Whatever bitrate is left goes to video; pure video if there was no audio.
    video_bitrate = target_total_bitrate - audio_bitrate if audio_bitrate > 0 else target_total_bitrate
    video_bitrate = max(video_bitrate, 50_000)

    i = ffmpeg.input(video_full_path)
    # First pass: analysis only; output discarded (os.devnull).
    ffmpeg.output(
        i,
        os.devnull,
        **{"c:v": "libx264", "b:v": video_bitrate, "pass": 1, "f": "mp4"},
    ).overwrite_output().run()
    # Second pass: write real file; AAC audio only if we had an audio budget.
    out_kw = {"c:v": "libx264", "b:v": video_bitrate, "pass": 2}
    if audio_bitrate > 0:
        out_kw["c:a"] = "aac"
        out_kw["b:a"] = audio_bitrate
    else:
        out_kw["an"] = None
    ffmpeg.output(i, output_file_name, **out_kw).overwrite_output().run()


# =============================================================================
# SUPABASE CLIENT (connect to your project in the cloud)
# Big picture: Before you can upload to Storage, the program needs an “API
# connection” to *your* Supabase project. _supabase() builds that: it reads
# your project URL and a secret key from the environment (usually from
# etc/supabase.env), checks they’re set, then returns a client object. That
# client is what you call .storage...upload() on. The service role key is
# powerful—never commit it; only load it via env on trusted machines.
# =============================================================================
def _supabase() -> Client:
    """Build a Supabase client from env (service role key = full Storage access; keep secret)."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise ValueError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (source etc/supabase.env)")
    return create_client(url, key)


# =============================================================================
# UPLOAD ONE FILE (the main workflow)
# Big picture: Validate path → read bucket/settings from env → if file is too
# large, compress to a temp file → open the Supabase client → send bytes to the
# bucket under the original filename. Cleans up the temp encode when done.
# =============================================================================
def upload_mp4_path(source: Path) -> None:
    """Upload one MP4 to Storage using the original filename as the object key.

    If the file is larger than UPLOAD_TARGET_MB (default 48), transcode to a temp
    file first so Free-tier 50 MB limits are respected.
    """
    # Normalize ~ and symlinks so stat/open use the real file.
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".mp4":
        raise ValueError(f"expected .mp4, got {source}")

    # Bucket name from env (create bucket in Supabase dashboard first).
    bucket = os.environ.get("SUPABASE_BUCKET", "").strip()
    if not bucket:
        raise ValueError("Set SUPABASE_BUCKET (source etc/supabase.env)")

    # Max size to upload without transcoding; `target_size_k` feeds compress_video’s formula when we transcode.
    target_mb = float(os.environ.get("UPLOAD_TARGET_MB", "48").strip() or "48")
    max_raw_bytes = int(target_mb * 1024 * 1024)
    target_size_k = int(target_mb * 1000)

    supabase = _supabase()
    # Object key in Storage = basename (e.g. same name as on disk).
    key = source.name
    tmp: str | None = None
    try:
        if source.stat().st_size > max_raw_bytes:
            # Too big for Supabase Free: encode to a disposable temp file, then upload that.
            fd, tmp = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            compress_video(str(source), tmp, target_size_k)
            upload_path = Path(tmp)
        else:
            # Small enough: upload bytes from the original path.
            upload_path = source

        # Storage API accepts a file-like object; upsert replaces if key already exists.
        with open(upload_path, "rb") as f:
            supabase.storage.from_(bucket).upload(
                key,
                f,
                file_options={"content-type": "video/mp4", "upsert": "true"},
            )
    finally:
        # Always remove temp encode output so we don’t fill the disk.
        if tmp and os.path.isfile(tmp):
            os.unlink(tmp)


# =============================================================================
# RUN AS A SCRIPT (command-line entry)
# Big picture: When you `python upload_video_to_supabase.py`, this picks which
# file to upload (UPLOAD_VIDEO env or default motion-videos/input.mp4) and
# delegates to upload_mp4_path(). Importing this module skips this block.
# =============================================================================
def main() -> None:
    """CLI entry: upload a single file (UPLOAD_VIDEO env or default under repo motion-videos)."""
    repo = Path(__file__).resolve().parent.parent
    default = repo / "motion-videos" / "input.mp4"
    path = Path(os.environ.get("UPLOAD_VIDEO", str(default))).expanduser()
    try:
        upload_mp4_path(path)
    except ValueError as e:
        # Turn config errors into a non-zero exit and a short message for the shell.
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
