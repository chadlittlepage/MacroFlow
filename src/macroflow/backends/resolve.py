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

import importlib.util
import os
import sys
import threading
from pathlib import Path

_RESOLVE_MODULE_PATHS = [
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting/Modules/DaVinciResolveScript.py",
    str(Path.home() / "Library/Application Support/Blackmagic Design/"
        "DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py"),
]


def _load_dvr_script():
    if "DaVinciResolveScript" in sys.modules:
        return sys.modules["DaVinciResolveScript"]
    env_path = os.environ.get("RESOLVE_SCRIPT_API")
    candidates = []
    if env_path:
        candidates.append(str(Path(env_path) / "Modules" / "DaVinciResolveScript.py"))
    candidates.extend(_RESOLVE_MODULE_PATHS)
    for path in candidates:
        if path and os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("DaVinciResolveScript", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["DaVinciResolveScript"] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            return module
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
        return None
    project = pm.GetCurrentProject()
    if project is None:
        return None
    return project.GetCurrentTimeline()


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
    if not track_state:
        return True
    ok = True
    for idx, enabled in track_state.items():
        if not set_video_track_enabled(int(idx), bool(enabled)):
            ok = False
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
