"""DaVinci Resolve backend.

Wraps DaVinciResolveScript. Locates the bundled Python module via the
standard Resolve install path, then exposes a small surface area:

    connect()                          -> bool
    get_video_track_count()            -> int
    get_video_track_info()             -> list[dict]
    set_video_track_enabled(idx, bool) -> bool
    get_current_timecode()             -> str | None

Resolve must be running on the same machine. The scripting API lives at:

    /Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/
        Scripting/Modules/DaVinciResolveScript.py

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import gc
import os
import queue
import sys
import threading

# Updated after every apply_track_state() so the GUI can show which tracks
# actually flipped. {flipped: [(idx, enabled), ...], unchanged: [idx, ...],
# failed: [idx, ...]}
LAST_APPLY: dict = {}

# EXPERIMENTAL — default OFF.
# When True, apply_video_track_transforms briefly toggles the track
# enable flag off-then-on after a successful write so Resolve flushes
# its playback frame cache and the new transform takes effect on the
# very next rendered frame — even mid-playback.
# Known to occasionally hang Resolve's playback engine on some
# projects, so the user has to opt in via Settings.
FORCE_REFRESH_DURING_PLAYBACK: bool = False


def set_force_refresh_during_playback(enabled: bool) -> None:
    global FORCE_REFRESH_DURING_PLAYBACK
    FORCE_REFRESH_DURING_PLAYBACK = bool(enabled)

_MODULE_DIRS = [
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting/Modules",
    os.path.expanduser(
        "~/Library/Application Support/Blackmagic Design/"
        "DaVinci Resolve/Developer/Scripting/Modules"
    ),
    "/opt/resolve/Developer/Scripting/Modules",
]


def _load_dvr_script():
    if "DaVinciResolveScript" in sys.modules:
        return sys.modules["DaVinciResolveScript"]
    env_path = os.environ.get("RESOLVE_SCRIPT_API")
    if env_path:
        candidate = os.path.join(env_path, "Modules")
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)
    for d in _MODULE_DIRS:
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    try:
        import DaVinciResolveScript as dvr  # noqa: N813
        return dvr
    except (ImportError, AttributeError) as e:
        print(f"[resolve] DaVinciResolveScript import failed: {e}")
        return None


_lock = threading.Lock()
_resolve = None


def connect() -> bool:
    """Acquire a Resolve handle. Idempotent."""
    global _resolve
    with _lock:
        if _resolve is not None:
            return True
        dvr = _load_dvr_script()
        if dvr is None:
            print("[resolve] DaVinciResolveScript module not found. Is Resolve installed?")
            return False
        try:
            _resolve = dvr.scriptapp("Resolve")
        except Exception as e:
            print(f"[resolve] scriptapp() failed: {e}")
            return False
        if _resolve is None:
            print("[resolve] Could not connect. Is DaVinci Resolve running?")
            return False
        return True


def is_alive() -> bool:
    """Round-trip a call so a STALE cached handle (Resolve was running but
    quit) is detected. Used by the GUI's status indicator."""
    global _resolve
    with _lock:
        if _resolve is None:
            # Try a fresh connect — Resolve may have just started.
            pass
    if not connect():
        return False
    try:
        pm = _resolve.GetProjectManager()
        return pm is not None
    except Exception:
        # The cached handle is stale; drop it so a future connect() retries.
        with _lock:
            _resolve = None
        return False


def safe_is_alive() -> bool:
    return bool(_run_off_main(is_alive, default=False))


def _current_timeline():
    """Walk Resolve → PM → Project → Timeline.

    Drops the intermediate ScriptVal refs (pm, project) before returning
    so they don't escape on the caller's stack frame. macOS 15 + Resolve
    20.1 segfaults if these get destroyed on a different thread.
    """
    if not connect():
        return None
    pm = project = None
    try:
        pm = _resolve.GetProjectManager()
        if pm is None:
            print("[resolve] GetProjectManager returned None")
            return None
        project = pm.GetCurrentProject()
        if project is None:
            print("[resolve] No current project (open a project in Resolve first)")
            return None
        tl = project.GetCurrentTimeline()
        if tl is None:
            print("[resolve] Project has no current timeline")
        return tl
    finally:
        del pm, project


def get_video_track_count() -> int:
    tl = _current_timeline()
    if tl is None:
        return 0
    try:
        return int(tl.GetTrackCount("video"))
    except Exception as e:
        print(f"[resolve] GetTrackCount failed: {e}")
        return 0


def get_video_track_info() -> list[dict]:
    """Return [{index, name, enabled}, ...] for every video track. 1-based.

    Drops the timeline ScriptVal before returning so it gets freed on
    this (worker) thread, not whichever thread later GCs the caller's
    locals.
    """
    tl = _current_timeline()
    if tl is None:
        return []
    info: list[dict] = []
    try:
        try:
            n = int(tl.GetTrackCount("video"))
        except Exception:
            return []
        for idx in range(1, n + 1):
            try:
                name = tl.GetTrackName("video", idx) or f"V{idx}"
            except Exception:
                name = f"V{idx}"
            try:
                enabled = bool(tl.GetIsTrackEnabled("video", idx))
            except Exception:
                enabled = True
            info.append({"index": idx, "name": name, "enabled": enabled})
        return info
    finally:
        del tl


def set_video_track_enabled(track_index: int, enabled: bool) -> bool:
    tl = _current_timeline()
    if tl is None:
        return False
    try:
        return bool(tl.SetTrackEnable("video", int(track_index), bool(enabled)))
    except Exception as e:
        print(f"[resolve] SetTrackEnable({track_index}, {enabled}) failed: {e}")
        return False
    finally:
        del tl


def apply_track_state(track_state: dict[int, bool]) -> bool:
    """Bulk apply: {1: True, 2: False, 3: True, ...}. Returns True if all set.

    Drops the timeline ScriptVal before returning so it gets freed on
    this (worker) thread.
    """
    global LAST_APPLY
    if not track_state:
        LAST_APPLY = {"flipped": [], "unchanged": [], "failed": []}
        return True
    tl = _current_timeline()
    if tl is None:
        LAST_APPLY = {"flipped": [], "unchanged": [], "failed": list(track_state)}
        return False
    flipped: list[tuple[int, bool]] = []
    noop: list[int] = []
    failed: list[int] = []
    ok = True
    try:
        for idx, enabled in track_state.items():
            idx = int(idx)
            enabled = bool(enabled)
            try:
                current = bool(tl.GetIsTrackEnabled("video", idx))
            except Exception:
                current = None
            if current == enabled:
                noop.append(idx)
                continue
            try:
                result = bool(tl.SetTrackEnable("video", idx, enabled))
            except Exception as e:
                print(f"[resolve] SetTrackEnable({idx}, {enabled}) raised {e}")
                result = False
            if result:
                flipped.append((idx, enabled))
            else:
                failed.append(idx)
                ok = False
        LAST_APPLY = {"flipped": flipped, "unchanged": noop, "failed": failed}
        parts = []
        if flipped:
            parts.append("flipped " + " ".join(
                f"V{i}{'↑' if en else '↓'}" for i, en in flipped))
        if noop:
            parts.append(f"already-set {len(noop)}")
        if failed:
            parts.append("failed " + " ".join(f"V{i}" for i in failed))
        print("[resolve] apply_track_state: " + (", ".join(parts) or "no changes"))
        return ok
    finally:
        del tl


def get_video_track_transforms() -> dict[int, dict]:
    """Per-track current transform, read from the first clip on each track.

    Returns {1: {"quadrant", "zoom_x", "zoom_y", "position_x", "position_y",
                 "rotation_angle", "anchor_point_x", "anchor_point_y",
                 "pitch", "yaw", "flip_h", "flip_v"}, ...}

    Tracks with no clips are omitted. The quadrant is inferred from the
    sign of (Pan, Tilt). Resolve property names that don't exist on this
    Resolve build come back None and we substitute the default.
    """
    tl = _current_timeline()
    if tl is None:
        return {}
    out: dict[int, dict] = {}
    items = None  # rebound each track loop; declared here for the outer finally
    clip = None
    frame = _current_frame(tl)
    try:
        try:
            n = int(tl.GetTrackCount("video"))
        except Exception:
            return out
        for idx in range(1, n + 1):
            try:
                items = tl.GetItemListInTrack("video", idx) or []
            except Exception:
                items = []
            if not items:
                continue
            # Read transforms from the clip currently under the playhead so
            # the editor reflects what the user is parked on, not whatever
            # leftmost clip happens to live on the track.
            clip = _item_at_frame(items, frame)
            if clip is None:
                continue

            def _f(prop: str, default: float, _clip=clip) -> float:
                try:
                    v = _clip.GetProperty(prop)
                except Exception:
                    v = None
                try:
                    return float(v) if v is not None else float(default)
                except (ValueError, TypeError):
                    return float(default)

            try:
                pan = _f("Pan", 0.0)
                tilt = _f("Tilt", 0.0)
                xform = {
                    "quadrant":       _infer_quadrant(pan, tilt),
                    "zoom_x":         _f("ZoomX", 1.0),
                    "zoom_y":         _f("ZoomY", 1.0),
                    "position_x":     pan,
                    "position_y":     tilt,
                    "rotation_angle": _f("RotationAngle", 0.0),
                    "anchor_point_x": _f("AnchorPointX", 0.0),
                    "anchor_point_y": _f("AnchorPointY", 0.0),
                    "pitch":          _f("Pitch", 0.0),
                    "yaw":            _f("Yaw", 0.0),
                    "flip_h":         bool(_f("FlipX", 0.0)),
                    "flip_v":         bool(_f("FlipY", 0.0)),
                }
                out[idx] = xform
            except Exception as e:
                print(f"[resolve] read transform for V{idx}: {e}")
            finally:
                # Per-track ScriptVal cleanup before the next iteration.
                clip = None
                items = None
        return out
    finally:
        del tl, items, clip


def _current_frame(tl) -> int | None:
    """Convert the timeline's current timecode to an absolute frame number.

    Resolve's TimelineItem.GetStart() / GetEnd() are absolute frame numbers
    (relative to the start of the timeline's "infinite" track ruler, e.g.
    01:00:00:00 = 86400 at 24 fps). To find the clip at the playhead we
    need the playhead in the same coordinate system.
    """
    if tl is None:
        return None
    try:
        tc = tl.GetCurrentTimecode()
    except Exception:
        return None
    if not tc:
        return None
    try:
        fps_raw = tl.GetSetting("timelineFrameRate")
    except Exception:
        fps_raw = None
    try:
        fps = float(fps_raw) if fps_raw else 24.0
    except (TypeError, ValueError):
        fps = 24.0
    # "HH:MM:SS:FF" — drop-frame uses ';' between SS and FF; we treat it the
    # same as ':' since drop-frame compensation only affects the displayed
    # value, not the absolute frame index.
    sep = tc.replace(";", ":")
    parts = sep.split(":")
    if len(parts) != 4:
        return None
    try:
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    return int(round((h * 3600 + m * 60 + s) * fps)) + f


def _item_at_frame(items, frame):
    """Pick the TimelineItem whose [start, end) covers `frame`.

    Used to find the clip at the playhead on a given track. Returns None
    when no clip on the track covers the playhead — caller decides whether
    to skip or fall back.
    """
    if frame is None:
        return None
    for it in items:
        try:
            start = int(it.GetStart())
            end = int(it.GetEnd())
        except Exception:
            continue
        if start <= frame < end:
            return it
    return None


def _infer_quadrant(pan: float, tilt: float) -> str:
    """Map (Pan, Tilt) to one of Q1..Q4. Resolve uses math-style coords
    where positive Tilt = UP, so top row has POSITIVE Tilt and bottom
    row has NEGATIVE Tilt."""
    if pan < 0 and tilt >= 0:
        return "Q1"   # top-left
    if pan >= 0 and tilt >= 0:
        return "Q2"   # top-right
    if pan < 0 and tilt < 0:
        return "Q3"   # bottom-left
    return "Q4"       # bottom-right


def apply_video_track_transforms(transforms: dict[int, dict]) -> bool:
    """Push per-track transform values back to the first clip on each track.

    Mirrors the read shape of get_video_track_transforms() — keys are our
    internal names (zoom_x, position_x, flip_h, etc.) which we map to the
    Resolve scripting property names (ZoomX, Pan, FlipX, ...). Tracks not
    in the dict are left untouched. Returns True if every property write
    succeeded.

    **Read-then-write guard.** We read the clip's current value first and
    only call SetProperty when the value actually differs (within a small
    epsilon for floats). This avoids two failure modes that have been
    observed in the wild:

    1. **Resolve crash on macOS 15** when the clip has never had any
       transforms set and we issue 11 SetProperty calls in rapid
       succession. The first write seems to trigger Resolve's
       transform-layer initialization, and the cascade of writes during
       that init has crashed Resolve. Skipping no-op writes means a
       quadrant change typically issues 2 writes (Pan + Tilt) instead
       of all 11, which sidesteps the init storm.
    2. **Performance** during a slider drag: writes that don't change
       state still walk Resolve's undo/redo stack and rebuild caches.
    """
    if not transforms:
        return True
    tl = _current_timeline()
    if tl is None:
        return False
    property_map = {
        "zoom_x":         "ZoomX",
        "zoom_y":         "ZoomY",
        "position_x":     "Pan",
        "position_y":     "Tilt",
        "rotation_angle": "RotationAngle",
        "anchor_point_x": "AnchorPointX",
        "anchor_point_y": "AnchorPointY",
        "pitch":          "Pitch",
        "yaw":            "Yaw",
        "flip_h":         "FlipX",
        "flip_v":         "FlipY",
    }
    EPSILON = 1e-6
    ok = True
    items = None
    clip = None
    frame = _current_frame(tl)
    # Tracks that got at least one SetProperty write. We toggle their
    # enable flag at the end if FORCE_REFRESH_DURING_PLAYBACK is set —
    # forces Resolve to flush its playback frame cache so the new
    # transform takes effect on the very next rendered frame even
    # mid-playback. Cost: one black frame on the affected track.
    written_tracks: list[int] = []
    try:
        for idx, xform in transforms.items():
            try:
                items = tl.GetItemListInTrack("video", int(idx)) or []
            except Exception:
                items = []
            if not items:
                continue
            # Apply transforms to the clip under the playhead, NOT the
            # leftmost clip on the track. Without this, MacroFlow used to
            # write to whatever clip happened to be at items[0], which
            # explains "the editor said it moved but the parked clip didn't"
            # bugs on multi-clip V1 timelines.
            clip = _item_at_frame(items, frame)
            if clip is None:
                # No clip on this track covers the playhead — nothing to
                # write. Macro silently skips this track instead of
                # corrupting the leftmost clip's transform.
                print(
                    f"[resolve] V{idx}: no clip at playhead — skipping transform"
                )
                continue
            wrote_this_track = False
            for our_key, resolve_key in property_map.items():
                val = xform.get(our_key) if isinstance(xform, dict) else None
                if val is None:
                    continue
                # Read current value so we can skip no-op writes.
                try:
                    cur_raw = clip.GetProperty(resolve_key)
                except Exception:
                    cur_raw = None
                try:
                    if our_key in ("flip_h", "flip_v"):
                        target = 1 if val else 0
                        cur = 1 if cur_raw else 0
                        if cur == target:
                            continue
                        clip.SetProperty(resolve_key, target)
                        wrote_this_track = True
                    else:
                        target_f = float(val)
                        try:
                            cur_f = float(cur_raw) if cur_raw is not None else None
                        except (TypeError, ValueError):
                            cur_f = None
                        if cur_f is not None and abs(cur_f - target_f) <= EPSILON:
                            continue
                        clip.SetProperty(resolve_key, target_f)
                        wrote_this_track = True
                except Exception as e:
                    print(f"[resolve] SetProperty V{idx} {resolve_key}={val}: {e}")
                    ok = False
            if wrote_this_track:
                written_tracks.append(int(idx))
            # Drop per-track ScriptVal refs before the next iteration so
            # they're freed on this thread, not whichever thread later GCs
            # the loop locals.
            clip = None
            items = None

        # Force-refresh: toggle each written track's enable flag off→on
        # so Resolve flushes its playback frame cache and re-evaluates
        # with the new transform on the next rendered frame. This is what
        # makes mid-playback quadrant swaps actually take effect; without
        # it, Resolve happily reads the stale cached transform until a
        # clip boundary forces a reload. Cost: one black frame per track.
        if FORCE_REFRESH_DURING_PLAYBACK and written_tracks:
            for idx in written_tracks:
                try:
                    tl.SetTrackEnable("video", int(idx), False)
                    tl.SetTrackEnable("video", int(idx), True)
                except Exception as e:
                    print(
                        f"[resolve] V{idx}: force-refresh toggle failed: {e}"
                    )
        return ok
    finally:
        del tl, items, clip


def get_timeline_resolution() -> tuple[int, int]:
    """Return (width, height) of the current timeline. Falls back to 1920×1080
    if no timeline / setting is queryable.

    Tries multiple sources in priority order:
    1. Timeline-level GetSetting (most accurate when it works)
    2. Project-level GetSetting (sometimes the only one populated, esp. when
       the user is parked inside a compound clip whose nested timeline
       returns empty resolution settings)
    3. (1920, 1080) default — with a LOUD warning so the user knows the
       quadrant offsets in the editor are about to be wrong.
    """
    if not connect():
        print(
            "[resolve] WARNING: could not connect — using fallback "
            "(1920, 1080); quadrant offsets will be wrong if your "
            "timeline is not HD"
        )
        return (1920, 1080)
    pm = project = tl = None
    try:
        # Source 1: timeline.GetSetting
        tl = _current_timeline()
        if tl is not None:
            try:
                w_raw = tl.GetSetting("timelineResolutionWidth")
                h_raw = tl.GetSetting("timelineResolutionHeight")
                w = int(w_raw) if w_raw not in (None, "", "0") else 0
                h = int(h_raw) if h_raw not in (None, "", "0") else 0
                if w > 0 and h > 0:
                    return (w, h)
            except (TypeError, ValueError, Exception):
                pass

        # Source 2: project.GetSetting — falls through to here when the
        # timeline-level call returned empty, which happens with compound
        # clips and some nested-timeline edge cases.
        try:
            pm = _resolve.GetProjectManager()
            project = pm.GetCurrentProject() if pm else None
        except Exception:
            project = None
        if project is not None:
            try:
                w_raw = project.GetSetting("timelineResolutionWidth")
                h_raw = project.GetSetting("timelineResolutionHeight")
                w = int(w_raw) if w_raw not in (None, "", "0") else 0
                h = int(h_raw) if h_raw not in (None, "", "0") else 0
                if w > 0 and h > 0:
                    print(
                        f"[resolve] timeline resolution from project: "
                        f"{w}x{h} (timeline-level was empty)"
                    )
                    return (w, h)
            except (TypeError, ValueError, Exception):
                pass

        print(
            "[resolve] WARNING: timeline + project resolution settings "
            "both empty — falling back to (1920, 1080). Open Resolve → "
            "Project Settings → Master Settings to verify, or pin the "
            "value in MacroFlow → Settings → Timeline resolution."
        )
        return (1920, 1080)
    finally:
        del pm, project, tl


def get_current_timecode() -> str | None:
    tl = _current_timeline()
    if tl is None:
        return None
    try:
        return str(tl.GetCurrentTimecode())
    except Exception as e:
        print(f"[resolve] GetCurrentTimecode failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Threaded wrappers
# ---------------------------------------------------------------------------
# Calling the Fusion scripting library from the Cocoa main thread corrupts
# NSAutoreleasePool: Fusion's C extension pushes/pops autoreleased objects
# during PyRemoteObject teardown, and when Cocoa next commits a CA
# transaction it crashes in objc_release on the freed handle.
#
# Python threads do NOT share the main thread's autorelease pool, so running
# Resolve calls on a worker thread sidesteps the corruption entirely.

# ---------------------------------------------------------------------------
# Persistent worker thread for all Resolve / Fusion API calls.
#
# Why: macOS 15 + Resolve 20.1 segfaults inside Fusion's event queue when
# ScriptVal objects (clips, tools, comps) are destroyed on a thread other
# than the one that created them. The previous spawn-a-daemon-per-call
# pattern destroyed ScriptVals on a thread that died immediately after,
# letting Resolve's main UI thread later dereference dangling Qt-hash
# entries. See crash_archive.txt frame 5 (Fusion::ScriptSymbol::~ScriptSymbol)
# triggered from libfusionsystem's HandleUIEX.
#
# Fix: one long-lived worker thread for the lifetime of the app. Every
# Resolve/Fusion call runs on it, and a gc.collect() runs at the END of
# each call so any ScriptVals created during the call are freed on the
# same thread, deterministically, before control returns.
# ---------------------------------------------------------------------------

_WORKER_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()
# Tracks pending coalesce_keys so duplicate work fired in quick succession
# (e.g. user mashes a macro 10x while Resolve is busy playing) collapses
# down to AT MOST one queued + one in-flight invocation per key.
_PENDING_KEYS: set[str] = set()
# Hard cap on queue depth. When the queue fills (Resolve busy / playing,
# user spamming macros), additional submissions are dropped instead of
# piling up. Prevents the "stop Resolve → editor flashes 10× catching up"
# behavior. ~3 in-flight is plenty for our workload.
_MAX_QUEUE_DEPTH = 3


def _worker_loop() -> None:
    while True:
        item = _WORKER_QUEUE.get()
        if item is None:  # shutdown sentinel (unused today; here for safety)
            return
        fn, result_box, err_box, done, key = item
        try:
            result_box.append(fn())
        except Exception as e:
            err_box.append(e)
        finally:
            # CRITICAL: free any Resolve/Fusion ScriptVal references that
            # were created during fn() *here*, on the worker thread, before
            # we signal completion. Letting them outlive this call means
            # they get freed on whatever thread happens to GC them next —
            # often Resolve's UI thread, where it crashes on macOS 15.
            try:
                gc.collect()
            except Exception:
                pass
            if key is not None:
                with _WORKER_LOCK:
                    _PENDING_KEYS.discard(key)
            done.set()


def _ensure_worker() -> None:
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        # daemon=True so the thread doesn't block app exit. Within a call,
        # however, it is the *only* thread that ever touches Resolve API,
        # which is what fixes the cross-thread ScriptVal destruction bug.
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop, daemon=True, name="macroflow-resolve-worker",
        )
        _WORKER_THREAD.start()


def _run_off_main(
    fn,
    *,
    timeout: float = 5.0,
    default=None,
    coalesce_key: str | None = None,
):
    """Run a callable on the persistent Resolve worker thread.

    Every Resolve/Fusion call goes through here so ScriptVal lifetimes
    stay on a single thread. After fn() returns, gc.collect() runs on the
    worker thread to deterministically free any ScriptVal refs created
    inside fn before control returns to the caller.

    Back-pressure:
      • When ``coalesce_key`` is set and an identical-key call is already
        pending or in-flight, the new call is dropped (returns ``default``)
        — so mashing a macro 10× while Resolve is playing collapses to
        one queued + one in-flight invocation, not 10.
      • If the queue depth exceeds _MAX_QUEUE_DEPTH, the new call is
        also dropped. Prevents the "stop Resolve → 10 flashes catch up"
        symptom on slow Fusion bridges.
    """
    _ensure_worker()
    if coalesce_key is not None:
        with _WORKER_LOCK:
            if coalesce_key in _PENDING_KEYS:
                # Same op already pending — drop this one. Treated as a
                # success no-op so the macro's other backends still fire.
                return default
            _PENDING_KEYS.add(coalesce_key)
    elif _WORKER_QUEUE.qsize() >= _MAX_QUEUE_DEPTH:
        # Unkeyed call but the queue is saturated — drop rather than
        # let the producer outrun the consumer.
        name = getattr(fn, "__name__", None) or getattr(fn, "func", fn).__name__
        print(
            f"[resolve] {name} dropped — worker queue saturated "
            f"(>{_MAX_QUEUE_DEPTH} pending, Resolve likely busy/playing)"
        )
        return default

    result_box: list = []
    err_box: list = []
    done = threading.Event()
    _WORKER_QUEUE.put((fn, result_box, err_box, done, coalesce_key))
    if not done.wait(timeout=timeout):
        name = getattr(fn, "__name__", None) or getattr(fn, "func", fn).__name__
        print(f"[resolve] {name} timed out after {timeout}s")
        return default
    name = getattr(fn, "__name__", None) or getattr(fn, "func", fn).__name__
    if err_box:
        print(f"[resolve] {name} raised {err_box[0]}")
        return default
    return result_box[0] if result_box else default


def safe_get_video_track_info() -> list[dict]:
    return _run_off_main(get_video_track_info, default=[]) or []


def safe_get_video_track_transforms() -> dict[int, dict]:
    return _run_off_main(get_video_track_transforms, default={}) or {}


def safe_get_current_timecode() -> str | None:
    return _run_off_main(get_current_timecode, default=None)


def safe_apply_track_state(track_state: dict[int, bool]) -> bool:
    from functools import partial
    # Coalesce identical writes — if a previous apply_track_state with
    # the same payload is still pending, the new one is dropped. Keeps
    # mashing a macro 10× while Resolve plays from queueing 10 writes.
    key = "apply_track_state:" + ",".join(
        f"{k}={1 if v else 0}" for k, v in sorted(track_state.items())
    )
    return bool(_run_off_main(
        partial(apply_track_state, track_state),
        default=False, coalesce_key=key,
    ))


def safe_apply_video_track_transforms(transforms: dict[int, dict]) -> bool:
    from functools import partial
    # Coalesce by track-set + the values' rounded float signatures so
    # identical drag-scrub mouseups don't enqueue twice. Different
    # values produce different keys so a real change still goes through.
    parts = []
    for idx in sorted(transforms.keys()):
        x = transforms[idx]
        if not isinstance(x, dict):
            parts.append(f"{idx}=NA")
            continue
        sig = "|".join(
            f"{k}={round(float(v), 4)}" if isinstance(v, (int, float))
            else f"{k}={v}"
            for k, v in sorted(x.items()) if v is not None
        )
        parts.append(f"{idx}={sig}")
    key = "apply_video_track_transforms:" + ";".join(parts)
    return bool(_run_off_main(
        partial(apply_video_track_transforms, transforms),
        default=False, coalesce_key=key,
    ))


def safe_get_timeline_resolution() -> tuple[int, int]:
    return _run_off_main(get_timeline_resolution, default=(1920, 1080)) or (1920, 1080)
