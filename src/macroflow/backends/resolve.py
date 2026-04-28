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


def safe_get_current_timecode() -> str | None:
    return _run_off_main(get_current_timecode, default=None)


def safe_apply_track_state(track_state: dict[int, bool]) -> bool:
    from functools import partial
    return bool(_run_off_main(partial(apply_track_state, track_state), default=False))
