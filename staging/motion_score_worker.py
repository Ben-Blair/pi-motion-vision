#!/usr/bin/env python3

import json
import os
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Import the modular selector/scorer utilities.
import sys
sys.path.insert(0, os.path.dirname(__file__))
import select_best_snapshot as sbs  # noqa: E402


EVENT_DIR = "/var/lib/motion"
EVENT_ID_FILE = os.path.join(EVENT_DIR, "current_event_id")
SNAPSHOT_DIR = os.path.join(EVENT_DIR, "snapshots")
LOG_DIR = "/dev/shm/motion_logs"  # tmpfs log directory (reduces SD card writes)

QUEUE_ROOT = os.path.join(EVENT_DIR, "score_queue")
BEST_ROOT = os.path.join(EVENT_DIR, "best_snapshots")

# RAM bounding
KEEP_N = int(os.environ.get("MOTION_SNAPSHOT_KEEP_N", "500"))

# Live debug frames (served by the web viewer). Use tmpfs to avoid SD wear.
DEBUG_LIVE_ROOT = os.environ.get("MOTION_DEBUG_LIVE_ROOT", "/dev/shm/motion_debug_scoring")
DEBUG_LIVE_ENABLED = os.environ.get("MOTION_DEBUG_LIVE", "1") == "1"
DEBUG_LIVE_MAX_FRAMES = int(os.environ.get("MOTION_DEBUG_LIVE_MAX_FRAMES", "600"))
DEBUG_LIVE_EVERY_N = int(os.environ.get("MOTION_DEBUG_LIVE_EVERY_N", "1"))

# SD wear control
CHECKPOINT_SECS = int(os.environ.get("MOTION_BEST_CHECKPOINT_SECS", "60"))
CHECKPOINT_DELTA = float(os.environ.get("MOTION_BEST_CHECKPOINT_DELTA", "500.0"))

# Tier-2 stability gating (suppress single-frame outliers)
T2_STABILITY_WINDOW_SECS = float(os.environ.get("MOTION_T2_STABILITY_WINDOW_SECS", "3"))
T2_STABILITY_IOU = float(os.environ.get("MOTION_T2_STABILITY_IOU", "0.30"))
T2_STABILITY_MIN_MATCHES = int(os.environ.get("MOTION_T2_STABILITY_MIN_MATCHES", "2"))
T2_STABILITY_MIN_SPAN_SECS = float(os.environ.get("MOTION_T2_STABILITY_MIN_SPAN_SECS", "1.0"))

# EventState cleanup
EVENT_STATE_CLEANUP_GRACE_SECS = float(os.environ.get("MOTION_EVENT_STATE_CLEANUP_GRACE_SECS", "300"))  # 5 minutes

# Tier-1 stability gating (simpler than T2: 2 matches, no time spread)
T1_STABILITY_IOU = float(os.environ.get("MOTION_T1_STABILITY_IOU", "0.30"))
T1_STABILITY_MIN_MATCHES = int(os.environ.get("MOTION_T1_STABILITY_MIN_MATCHES", "2"))


def atomic_write_json(path: str, payload: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path) or ".", delete=False, mode="w") as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        os.replace(tmp.name, path)


def atomic_copy(src: str, dst: str):
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(dst) or ".", delete=False) as tmp:
        shutil.copy2(src, tmp.name)
        os.replace(tmp.name, dst)


def annotate_best(src_path: str, tier: int, score: float, scorer: sbs.Scorer, out_path: str) -> bool:
    """
    Write an annotated copy of the BEST frame (tier/score banner + face boxes).
    """
    try:
        import cv2  # lazy import to keep startup cheap
        img = cv2.imread(src_path)
        if img is None:
            return False

        label = f"T{int(tier)} score={float(score):.3f}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
        cv2.putText(img, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2, cv2.LINE_AA)

        try:
            det_img, _ = scorer.preprocess_for_face(img)
            faces = scorer.detect_faces(det_img)
            for (x, y, w, h, conf) in faces:
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                if conf > 0:
                    conf_n = max(0.0, min(1.0, float(conf)))
                    cv2.putText(img, f"{conf_n:.2f}", (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
        except Exception:
            pass

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp_dir = os.path.dirname(out_path) or "."
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False, suffix=".jpg") as tmp:
            tmp_name = tmp.name
        try:
            ok = cv2.imwrite(tmp_name, img)
            if not ok:
                return False
            os.replace(tmp_name, out_path)
            return True
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
    except Exception:
        return False


def prune_snapshots_keep_n(keep_n: int):
    if keep_n <= 0:
        return
    try:
        files = []
        for name in os.listdir(SNAPSHOT_DIR):
            if not name.lower().endswith(".jpg"):
                continue
            path = os.path.join(SNAPSHOT_DIR, name)
            try:
                st = os.stat(path)
                files.append((float(st.st_mtime), path))
            except Exception:
                continue
        if len(files) <= keep_n:
            return
        files.sort(key=lambda t: t[0])  # oldest first
        to_delete = files[: max(0, len(files) - keep_n)]
        for _, p in to_delete:
            try:
                os.unlink(p)
            except Exception:
                pass
    except Exception:
        return


@dataclass
class EventState:
    event_id: str
    cache_db: str
    conn: object
    scorer: sbs.Scorer
    best: Optional[Tuple[int, float]] = None
    best_source: Optional[str] = None
    last_checkpoint_ts: float = 0.0
    last_checkpoint_score: float = -1.0
    first_t2_checkpointed: bool = False
    last_prune_ts: float = 0.0
    debug_dir: str = ""
    debug_written: int = 0
    debug_seen: int = 0
    # recent Tier-2 detections: list of (ts, bbox)
    recent_t2: list = None
    # recent Tier-1 detections: list of timestamps (no bbox needed)
    recent_t1: list = None
    # Track last activity for cleanup
    last_activity_ts: float = 0.0
    flushed: bool = False


def make_scorer(logger):
    return sbs.Scorer(
        yunet_model=sbs.DEFAULT_YUNET_MODEL,
        yunet_score_threshold=0.5,
        yunet_nms_threshold=0.3,
        yunet_topk=5000,
        clahe_enabled=True,
        clahe_clip=2.0,
        clahe_grid=8,
        min_face_size_px=60,
        min_face_area_ratio=0.002,
        require_person_for_face_tier=False,
        logger=logger,
    )


def open_event_state(event_id: str, logger) -> EventState:
    cache_db = os.path.join(EVENT_DIR, f"score_cache_{event_id}.sqlite3")
    conn = sbs.open_cache(cache_db)
    scorer = make_scorer(logger)

    # Load current best from cache DB (if any).
    best = None
    best_source = None
    try:
        row = conn.execute(
            "SELECT basename, tier, score FROM scores ORDER BY tier DESC, score DESC LIMIT 1"
        ).fetchone()
        if row:
            best_source = str(row[0])
            best = (int(row[1]), float(row[2]))
    except Exception:
        pass

    now = time.time()
    st = EventState(
        event_id=event_id,
        cache_db=cache_db,
        conn=conn,
        scorer=scorer,
        best=best,
        best_source=best_source,
        last_checkpoint_ts=now,
        last_checkpoint_score=float(best[1]) if best else -1.0,
        first_t2_checkpointed=bool(best and best[0] >= 2),
        last_prune_ts=now,
    )
    st.recent_t2 = []
    st.recent_t1 = []
    st.last_activity_ts = now
    st.flushed = False
    # Live debug directory (tmpfs)
    if DEBUG_LIVE_ENABLED:
        try:
            os.makedirs(DEBUG_LIVE_ROOT, exist_ok=True)
            st.debug_dir = os.path.join(DEBUG_LIVE_ROOT, event_id)
            os.makedirs(st.debug_dir, exist_ok=True)
            # Update stable pointer for the viewer
            latest_link = os.path.join(DEBUG_LIVE_ROOT, "latest")
            try:
                if os.path.islink(latest_link) or os.path.exists(latest_link):
                    os.unlink(latest_link)
            except Exception:
                pass
            try:
                os.symlink(st.debug_dir, latest_link)
            except Exception:
                pass
        except Exception:
            st.debug_dir = ""
    return st


def close_event_state(st: EventState, logger):
    """Close SQLite connection and clean up resources for an event state."""
    try:
        if st.conn:
            st.conn.close()
    except Exception as e:
        logger.warning(f"Error closing connection for {st.event_id}: {e}")
    # Scorer cleanup is handled by Python GC


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = float(iw * ih)
    if inter <= 0:
        return 0.0
    union = float(aw * ah + bw * bh) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _t2_is_stable(st: EventState, bbox_xywh: Tuple[int, int, int, int]) -> Tuple[bool, int]:
    """
    Returns (stable, matches) where matches includes the current frame.
    Requires both enough matches AND sufficient time spread between oldest and newest match.
    """
    now = time.time()
    win = max(0.0, float(T2_STABILITY_WINDOW_SECS))
    # Prune old
    if st.recent_t2 is None:
        st.recent_t2 = []
    st.recent_t2 = [(ts, bb) for (ts, bb) in st.recent_t2 if (now - ts) <= win]

    matches = 1
    matching_times = [now]  # Include current frame timestamp
    thr = max(0.0, min(1.0, float(T2_STABILITY_IOU)))
    for ts, bb in st.recent_t2:
        try:
            if _iou(bbox_xywh, bb) >= thr:
                matches += 1
                matching_times.append(ts)
        except Exception:
            continue

    # Record this detection for future frames
    st.recent_t2.append((now, bbox_xywh))
    need = max(1, int(T2_STABILITY_MIN_MATCHES))
    
    # Check if we have enough matches
    if matches < need:
        return (False, matches)
    
    # Check time spread: matches must span at least MIN_SPAN_SECS
    min_span = max(0.0, float(T2_STABILITY_MIN_SPAN_SECS))
    if min_span > 0 and len(matching_times) >= 2:
        time_span = max(matching_times) - min(matching_times)
        if time_span < min_span:
            return (False, matches)
    
    return (True, matches)


def _t1_is_stable(st: EventState) -> Tuple[bool, int]:
    """
    Returns (stable, matches) for Tier-1 detections.
    Simpler than T2: requires 2 matches within window, no bbox matching needed.
    """
    now = time.time()
    # Use same window as T2 for consistency
    win = max(0.0, float(T2_STABILITY_WINDOW_SECS))
    # Prune old
    if st.recent_t1 is None:
        st.recent_t1 = []
    st.recent_t1 = [ts for ts in st.recent_t1 if (now - ts) <= win]

    matches = len(st.recent_t1) + 1  # Current frame counts as 1
    # Record this detection for future frames
    st.recent_t1.append(now)
    need = max(1, int(T1_STABILITY_MIN_MATCHES))
    return (matches >= need, matches)


def should_checkpoint(st: EventState, *, best_updated: bool) -> bool:
    if not best_updated or not st.best or not st.best_source or not st.best_source:
        return False
    tier, score = st.best
    now = time.time()
    if tier >= 2 and not st.first_t2_checkpointed:
        return True
    if CHECKPOINT_SECS > 0 and (now - st.last_checkpoint_ts) >= float(CHECKPOINT_SECS):
        return True
    if CHECKPOINT_DELTA > 0 and (score - st.last_checkpoint_score) >= float(CHECKPOINT_DELTA):
        return True
    return False


def maybe_write_live_debug_frame(st: EventState, snap_path: str, tier: int, score: float):
    if not DEBUG_LIVE_ENABLED:
        return
    if not st.debug_dir:
        return
    st.debug_seen += 1
    if DEBUG_LIVE_EVERY_N > 1 and (st.debug_seen % DEBUG_LIVE_EVERY_N) != 0:
        return
    if DEBUG_LIVE_MAX_FRAMES > 0 and st.debug_written >= DEBUG_LIVE_MAX_FRAMES:
        return
    # Write annotated copy into debug dir
    base = os.path.basename(snap_path)
    out_name = f"{st.debug_written:04d}_{base}"
    out_path = os.path.join(st.debug_dir, out_name)
    ok = annotate_best(snap_path, int(tier), float(score), st.scorer, out_path)
    if ok:
        st.debug_written += 1


def checkpoint_best(st: EventState, logger, *, final: bool = False):
    if not st.best or not st.best_source:
        return
    tier, score = st.best

    # best_source in DB is a basename; reconstruct the full path if possible.
    # Prefer the snapshot path if it exists, else just store basename in meta.
    best_basename = os.path.basename(st.best_source)
    best_path = os.path.join(SNAPSHOT_DIR, best_basename)
    if not os.path.exists(best_path):
        # In case snapshots were already pruned/deleted, we can't copy it; just write meta.
        best_path = ""

    ev_dir = os.path.join(BEST_ROOT, st.event_id)
    os.makedirs(ev_dir, exist_ok=True)

    current_best = os.path.join(ev_dir, "current_best.jpg")
    current_ann = current_best + ".annotated.jpg"
    current_meta = current_best + ".meta.json"

    payload = {
        "event_id": st.event_id,
        "best_source_basename": best_basename,
        "best_source_file": best_path if best_path else best_basename,
        "tier": int(tier),
        "score": float(score),
        "ts": float(time.time()),
        "final": bool(final),
    }
    atomic_write_json(current_meta, payload)

    if best_path:
        atomic_copy(best_path, current_best)
        annotate_best(best_path, int(tier), float(score), st.scorer, current_ann)

        if final:
            # Persist the final best with its original basename too.
            final_jpg = os.path.join(ev_dir, best_basename)
            final_ann = final_jpg + ".annotated.jpg"
            final_meta = final_jpg + ".meta.json"
            atomic_copy(best_path, final_jpg)
            annotate_best(best_path, int(tier), float(score), st.scorer, final_ann)
            atomic_write_json(final_meta, payload)

            # Also update legacy path (disk) for compatibility.
            try:
                atomic_copy(best_path, os.path.join(EVENT_DIR, "best_snapshot.jpg"))
            except Exception:
                pass

    st.last_checkpoint_ts = time.time()
    st.last_checkpoint_score = float(score)
    if int(tier) >= 2:
        st.first_t2_checkpointed = True
    logger.info(f"CHECKPOINT event={st.event_id} tier={tier} score={score} final={final}")


def read_queue_item(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            line = f.readline().strip()
            return line or None
    except Exception:
        return None


def process_snapshot(st: EventState, snap_path: str, logger) -> bool:
    # Update last activity timestamp
    st.last_activity_ts = time.time()
    
    base = os.path.basename(snap_path)
    if not sbs.SNAPSHOT_RE.match(base):
        return False

    # If already cached, avoid rescoring.
    bbox = None
    cached = None
    try:
        cached = sbs.cache_get(st.conn, base)
    except Exception:
        cached = None

    # For Tier-2 stability gating we need bbox; if cache says tier==2 we still rescore to get bbox.
    if cached is not None and int(cached[0]) != 2:
        tier, score = int(cached[0]), float(cached[1])
    else:
        tier, score, bbox = sbs.score_image_with_bbox(snap_path, st.scorer)
        if tier < 0:
            return False
        try:
            sbs.cache_put(st.conn, base, tier, score)
        except Exception:
            pass

    best_updated = False
    if st.best is None:
        # When initializing best, still apply the Tier-2 stability gate so a single-frame T2 outlier
        # can't become the initial "best snapshot".
        if int(tier) == 2 and bbox is not None:
            bb_xywh = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
            stable, matches = _t2_is_stable(st, bb_xywh)
            if not stable:
                logger.info(
                    f"T2_UNSTABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s iou={T2_STABILITY_IOU}"
                )
            else:
                logger.info(
                    f"T2_STABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s iou={T2_STABILITY_IOU}"
                )
                st.best = (int(tier), float(score))
                st.best_source = base
                best_updated = True
        elif int(tier) == 2:
            logger.info(f"T2_UNSTABLE event={st.event_id} {snap_path} score={score} matches=0 reason=no_bbox")
        elif int(tier) == 1:
            # Apply T1 stability gate
            stable, matches = _t1_is_stable(st)
            if not stable:
                logger.info(
                    f"T1_UNSTABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s"
                )
            else:
                logger.info(
                    f"T1_STABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s"
                )
                st.best = (int(tier), float(score))
                st.best_source = base
                best_updated = True
        else:
            st.best = (int(tier), float(score))
            st.best_source = base
            best_updated = True
    else:
        cur = (int(st.best[0]), float(st.best[1]))
        cand = (int(tier), float(score))

        if cand > cur:
            # Stability gate: suppress single-frame Tier-2 outliers from replacing best.
            if int(tier) == 2 and bbox is not None:
                bb_xywh = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
                stable, matches = _t2_is_stable(st, bb_xywh)
                if not stable:
                    logger.info(
                        f"T2_UNSTABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s iou={T2_STABILITY_IOU}"
                    )
                else:
                    logger.info(
                        f"T2_STABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s iou={T2_STABILITY_IOU}"
                    )
                    st.best = cand
                    st.best_source = base
                    best_updated = True
            elif int(tier) == 2:
                # Tier-2 but no bbox (should be rare); treat as unstable.
                logger.info(f"T2_UNSTABLE event={st.event_id} {snap_path} score={score} matches=0 reason=no_bbox")
            elif int(tier) == 1:
                # Apply T1 stability gate
                stable, matches = _t1_is_stable(st)
                if not stable:
                    logger.info(
                        f"T1_UNSTABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s"
                    )
                else:
                    logger.info(
                        f"T1_STABLE event={st.event_id} {snap_path} score={score} matches={matches} window={T2_STABILITY_WINDOW_SECS}s"
                    )
                    st.best = cand
                    st.best_source = base
                    best_updated = True
            else:
                st.best = cand
                st.best_source = base
                best_updated = True

    # Log in worker log
    logger.info(f"SCORED event={st.event_id} {snap_path} T{tier} score={score}")
    # Also append a simple line to the legacy live scoring log for easy tailing.
    try:
        with open(os.path.join(LOG_DIR, "live_snapshot_scoring.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} SCORED {snap_path} T{tier} score={score}\n")
    except Exception:
        pass

    # Live debug output for the web viewer
    maybe_write_live_debug_frame(st, snap_path, int(tier), float(score))

    # Periodic checkpointing disabled - best snapshot kept in memory until event end
    # Final checkpoint will be done on flush request to minimize SD writes

    # Periodic pruning (cheap enough)
    now = time.time()
    if now - st.last_prune_ts >= 5.0:
        prune_snapshots_keep_n(KEEP_N)
        st.last_prune_ts = now

    return True


_stop = False


def _handle_stop(signum, frame):
    global _stop
    _stop = True


def main():
    import logging

    os.makedirs(QUEUE_ROOT, exist_ok=True)
    os.makedirs(BEST_ROOT, exist_ok=True)
    if DEBUG_LIVE_ENABLED:
        try:
            os.makedirs(DEBUG_LIVE_ROOT, exist_ok=True)
        except Exception:
            pass

    # Ensure log directory exists
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass  # Fallback to EVENT_DIR if tmpfs unavailable
    
    logging.basicConfig(
        filename=os.path.join(LOG_DIR, "motion_score_worker.log"),
        level=logging.INFO,
        format="%(asctime)s %(message)s",
    )
    log = logging.getLogger("motion_score_worker")
    log.info("WORKER START")

    # Ensure the legacy live scoring log exists so tools that tail it keep working.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        open(os.path.join(LOG_DIR, "live_snapshot_scoring.log"), "a").close()
    except Exception:
        pass

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGHUP, lambda *_: None)

    states: Dict[str, EventState] = {}

    while not _stop:
        # Process queue dirs
        try:
            event_dirs = [
                d for d in os.listdir(QUEUE_ROOT)
                if os.path.isdir(os.path.join(QUEUE_ROOT, d))
            ]
        except Exception:
            event_dirs = []

        did_work = False
        for eid in sorted(event_dirs):
            qdir = os.path.join(QUEUE_ROOT, eid)
            flush_path = os.path.join(qdir, "flush")
            flushed_path = os.path.join(qdir, "flushed")

            if eid not in states:
                try:
                    states[eid] = open_event_state(eid, log)
                except Exception as e:
                    log.warning(f"Failed to init state for {eid}: {e}")
                    continue
            st = states[eid]

            try:
                items = sorted(
                    f for f in os.listdir(qdir)
                    if f.endswith(".q")
                )
            except Exception:
                items = []

            for name in items[:200]:  # bound per loop to avoid starving others
                qpath = os.path.join(qdir, name)
                snap = read_queue_item(qpath)
                try:
                    os.unlink(qpath)
                except Exception:
                    pass
                if not snap:
                    continue
                # Snapshot might have been pruned; handle gracefully.
                if not os.path.exists(snap):
                    continue
                process_snapshot(st, snap, log)
                did_work = True

            # If requested, flush final best after draining queue.
            if os.path.exists(flush_path) and not os.path.exists(flushed_path):
                # Drain remaining quickly
                try:
                    items2 = sorted(f for f in os.listdir(qdir) if f.endswith(".q"))
                except Exception:
                    items2 = []
                for name in items2:
                    qpath = os.path.join(qdir, name)
                    snap = read_queue_item(qpath)
                    try:
                        os.unlink(qpath)
                    except Exception:
                        pass
                    if snap and os.path.exists(snap):
                        process_snapshot(st, snap, log)
                        did_work = True

                checkpoint_best(st, log, final=True)
                st.flushed = True
                st.last_activity_ts = time.time()
                try:
                    with open(flushed_path, "w", encoding="utf-8") as f:
                        f.write(str(time.time()))
                except Exception:
                    pass
                try:
                    os.unlink(flush_path)
                except Exception:
                    pass
                
                # Clean up queue directory after flush
                try:
                    # Remove any remaining .q files
                    remaining_q = [f for f in os.listdir(qdir) if f.endswith(".q")]
                    for qf in remaining_q:
                        try:
                            os.unlink(os.path.join(qdir, qf))
                        except Exception:
                            pass
                    # Remove queue directory if empty or only contains flush markers
                    remaining = [f for f in os.listdir(qdir) if f not in ("flush", "flushed")]
                    if not remaining:
                        try:
                            os.rmdir(qdir)
                        except Exception:
                            pass
                except Exception:
                    pass

        # Clean up stale event states (flushed + grace period expired)
        now = time.time()
        stale_events = []
        for eid, st in list(states.items()):
            if st.flushed and (now - st.last_activity_ts) > EVENT_STATE_CLEANUP_GRACE_SECS:
                stale_events.append(eid)
        
        for eid in stale_events:
            log.info(f"CLEANUP_STATE event={eid} age={now - states[eid].last_activity_ts:.1f}s")
            close_event_state(states[eid], log)
            del states[eid]

        # Touch health indicator file
        try:
            health_file = os.path.join(EVENT_DIR, ".worker_health")
            with open(health_file, "w") as f:
                f.write(str(now))
        except Exception:
            pass

        if not did_work:
            time.sleep(0.25)

    # Shutdown: checkpoint current event if requested via flush markers
    log.info("WORKER STOP")


if __name__ == "__main__":
    raise SystemExit(main())


