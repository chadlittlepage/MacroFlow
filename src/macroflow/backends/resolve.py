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

import os
import sys
import threading

# Updated after every apply_track_state() so the GUI can show which tracks
# actually flipped. {flipped: [(idx, enabled), ...], unchanged: [idx, ...],
# failed: [idx, ...]}
LAST_APPLY: dict = {}

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
    if not connect():
        return None
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
    """Return [{index, name, enabled}, ...] for every video track. 1-based."""
    tl = _current_timeline()
    if tl is None:
        return []
    info: list[dict] = []
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


def set_video_track_enabled(track_index: int, enabled: bool) -> bool:
    tl = _current_timeline()
    if tl is None:
        return False
    try:
        return bool(tl.SetTrackEnable("video", int(track_index), bool(enabled)))
    except Exception as e:
        print(f"[resolve] SetTrackEnable({track_index}, {enabled}) failed: {e}")
        return False


def apply_track_state(track_state: dict[int, bool]) -> bool:
    """Bulk apply: {1: True, 2: False, 3: True, ...}. Returns True if all set."""
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
    try:
        n = int(tl.GetTrackCount("video"))
    except Exception:
        return {}
    out: dict[int, dict] = {}
    for idx in range(1, n + 1):
        try:
            items = tl.GetItemListInTrack("video", idx) or []
        except Exception:
            items = []
        if not items:
            continue
        clip = items[0]

        def _f(prop: str, default: float) -> float:
            try:
                v = clip.GetProperty(prop)
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
    return out


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
    for idx, xform in transforms.items():
        try:
            items = tl.GetItemListInTrack("video", int(idx)) or []
        except Exception:
            items = []
        if not items:
            continue
        clip = items[0]
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
                else:
                    target_f = float(val)
                    try:
                        cur_f = float(cur_raw) if cur_raw is not None else None
                    except (TypeError, ValueError):
                        cur_f = None
                    if cur_f is not None and abs(cur_f - target_f) <= EPSILON:
                        continue
                    clip.SetProperty(resolve_key, target_f)
            except Exception as e:
                print(f"[resolve] SetProperty V{idx} {resolve_key}={val}: {e}")
                ok = False
    return ok


def get_timeline_resolution() -> tuple[int, int]:
    """Return (width, height) of the current timeline. Falls back to 1920×1080
    if no timeline / setting is queryable."""
    tl = _current_timeline()
    if tl is None:
        return (1920, 1080)
    try:
        w = int(tl.GetSetting("timelineResolutionWidth") or 1920)
        h = int(tl.GetSetting("timelineResolutionHeight") or 1080)
        return (w, h)
    except Exception:
        return (1920, 1080)


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

def _run_off_main(fn, *, timeout: float = 5.0, default=None):
    """Run a callable on a worker thread and join."""
    box: list = [default]
    err: list = [None]

    def _worker():
        try:
            box[0] = fn()
        except Exception as e:  # pragma: no cover
            err[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    name = getattr(fn, "__name__", None) or getattr(fn, "func", fn).__name__
    if t.is_alive():
        print(f"[resolve] {name} timed out after {timeout}s")
        return default
    if err[0] is not None:
        print(f"[resolve] {name} raised {err[0]}")
        return default
    return box[0]


def safe_get_video_track_info() -> list[dict]:
    return _run_off_main(get_video_track_info, default=[]) or []


def safe_get_video_track_transforms() -> dict[int, dict]:
    return _run_off_main(get_video_track_transforms, default={}) or {}


def safe_get_current_timecode() -> str | None:
    return _run_off_main(get_current_timecode, default=None)


def safe_apply_track_state(track_state: dict[int, bool]) -> bool:
    from functools import partial
    return bool(_run_off_main(partial(apply_track_state, track_state), default=False))


def safe_apply_video_track_transforms(transforms: dict[int, dict]) -> bool:
    from functools import partial
    return bool(_run_off_main(
        partial(apply_video_track_transforms, transforms), default=False,
    ))


def safe_get_timeline_resolution() -> tuple[int, int]:
    return _run_off_main(get_timeline_resolution, default=(1920, 1080)) or (1920, 1080)
