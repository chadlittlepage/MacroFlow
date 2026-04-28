"""Macro = a named bundle of actions across multiple backends.

A macro fires its actions in parallel threads. One backend failing does
not abort the others — that's the whole point of a macro grid.

Storage: /Users/Shared/MacroFlow/macroflow.json (shared by all users on
the Mac, same approach as Videohub Controller).

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from macroflow.backends import resolve, videohub

_SHARED_DIR = Path("/Users/Shared/MacroFlow")
CONFIG_PATH = _SHARED_DIR / "macroflow.json"


@dataclass
class VideohubAction:
    device_id: str = ""
    preset_name: str = ""

    def is_set(self) -> bool:
        return bool(self.device_id and self.preset_name)

    def fire(self) -> bool:
        if not self.is_set():
            return True  # nothing to do counts as success
        return videohub.recall_preset(self.device_id, self.preset_name)


@dataclass
class ResolveAction:
    # Per-track enable/disable: {1: True, 3: False, ...}
    # Tracks not in the dict are left untouched.
    tracks: dict[int, bool] = field(default_factory=dict)

    def is_set(self) -> bool:
        return bool(self.tracks)

    def fire(self) -> bool:
        if not self.is_set():
            return True
        # Coerce JSON-loaded keys (str) back to int.
        normalized = {int(k): bool(v) for k, v in self.tracks.items()}
        # safe_* wrapper runs on a worker thread to keep the Fusion library
        # from corrupting the Cocoa main thread's NSAutoreleasePool.
        return resolve.safe_apply_track_state(normalized)


@dataclass
class Macro:
    id: str
    label: str = ""
    color: str = "#4a556c"  # primary color (CLAUDE.md global rule 001)
    hotkey: str = ""        # single character (e.g. "a", "1") or "F1"-"F12"; empty = none
    videohub: VideohubAction = field(default_factory=VideohubAction)
    resolve: ResolveAction = field(default_factory=ResolveAction)

    def fire(self) -> dict[str, bool]:
        """Run every backend action in parallel. Returns {backend: success}."""
        results: dict[str, bool] = {}
        threads: list[tuple[str, threading.Thread]] = []

        def _run(name: str, fn) -> None:
            try:
                results[name] = bool(fn())
            except Exception as e:
                print(f"[macro] {self.id}: {name} raised {e}")
                results[name] = False

        for name, action in (("videohub", self.videohub), ("resolve", self.resolve)):
            if action.is_set():
                t = threading.Thread(target=_run, args=(name, action.fire), daemon=True)
                t.start()
                threads.append((name, t))

        for _name, t in threads:
            t.join(timeout=5.0)
        return results

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "color": self.color,
            "hotkey": self.hotkey,
            "videohub": asdict(self.videohub),
            "resolve": {"tracks": {str(k): bool(v) for k, v in self.resolve.tracks.items()}},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Macro":
        vh_data = data.get("videohub", {}) or {}
        rv_data = data.get("resolve", {}) or {}
        tracks_raw = rv_data.get("tracks", {}) or {}
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            color=str(data.get("color", "#4a556c")),
            hotkey=str(data.get("hotkey", "")),
            videohub=VideohubAction(
                device_id=str(vh_data.get("device_id", "")),
                preset_name=str(vh_data.get("preset_name", "")),
            ),
            resolve=ResolveAction(
                tracks={int(k): bool(v) for k, v in tracks_raw.items()},
            ),
        )


@dataclass
class MacroGrid:
    rows: int = 4
    cols: int = 4
    macros: dict[str, Macro] = field(default_factory=dict)  # keyed by "r,c"
    mock_videohub: bool = False
    # Font sizes (saved + restored). Defaults match the values used in the
    # initial UI build.
    display_font_size: float = 12.0   # LCD strip
    title_font_size: float = 13.0     # cell title
    hotkey_font_size: float = 26.0    # big hotkey glyph

    @staticmethod
    def cell_id(row: int, col: int) -> str:
        return f"{row},{col}"

    def get(self, row: int, col: int) -> Macro | None:
        return self.macros.get(self.cell_id(row, col))

    def set(self, row: int, col: int, macro: Macro) -> None:
        macro.id = self.cell_id(row, col)
        self.macros[macro.id] = macro

    def clear(self, row: int, col: int) -> None:
        self.macros.pop(self.cell_id(row, col), None)

    def fire(self, row: int, col: int) -> dict[str, bool] | None:
        m = self.get(row, col)
        if m is None:
            return None
        return m.fire()


class MacroStore:
    """Persists a single MacroGrid to disk."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.grid = MacroGrid()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            print(f"[macro] No config at {self.path}; starting fresh")
            return
        # Refuse to follow a symlink at the shared config path. Without this,
        # any local user could plant a symlink at /Users/Shared/MacroFlow/
        # macroflow.json and have MacroFlow read whatever it points to under
        # the running user's privileges.
        if self.path.is_symlink():
            print(f"[macro] Refusing to load {self.path}: path is a symlink")
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception as e:
            print(f"[macro] Failed to load {self.path}: {e}")
            return
        self.grid.rows = int(data.get("rows", 4))
        self.grid.cols = int(data.get("cols", 4))
        self.grid.mock_videohub = bool(data.get("mock_videohub", False))
        fs = data.get("font_sizes") or {}
        self.grid.display_font_size = float(fs.get("display", 12.0))
        self.grid.title_font_size = float(fs.get("title", 13.0))
        self.grid.hotkey_font_size = float(fs.get("hotkey", 26.0))
        self.grid.macros = {
            mid: Macro.from_dict(m) for mid, m in (data.get("macros") or {}).items()
        }
        print(f"[macro] Loaded {len(self.grid.macros)} macro(s) "
              f"({self.grid.rows}x{self.grid.cols})")

    def save(self) -> None:
        data = {
            "rows": self.grid.rows,
            "cols": self.grid.cols,
            "mock_videohub": self.grid.mock_videohub,
            "font_sizes": {
                "display": self.grid.display_font_size,
                "title": self.grid.title_font_size,
                "hotkey": self.grid.hotkey_font_size,
            },
            "macros": {mid: m.to_dict() for mid, m in self.grid.macros.items()},
        }
        try:
            atomic_write_shared_json(self.path, data)
        except Exception as e:
            print(f"[macro] Failed to write {self.path}: {e}")


def atomic_write_shared_json(path: Path, data: dict) -> None:
    """Atomic, multi-user-safe JSON write to a shared `/Users/Shared/...` path.

    Why atomic: two users hitting save simultaneously must not interleave
    bytes. We write to a temp file in the same directory then os.replace
    it onto the target (rename is atomic within a filesystem).

    Why os.replace and not write_text: write_text follows symlinks. If a
    malicious user planted a symlink at the target, write_text would
    truncate whatever it points to. os.replace swaps the target inode
    atomically — symlink at the path is replaced, never followed.

    Multi-user perms: parent dir set to 0o777 (any user can create / replace
    files inside), the new file is chmod 0o666 BEFORE the rename so the
    final file is world-readable+writable from the moment it appears.
    """
    import json as _json
    import os
    import tempfile

    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        # Best-effort — only the dir's owner can chmod it. After the first
        # creator sets 0o777, subsequent users can write without needing it.
        os.chmod(parent, 0o777)
    except Exception:
        pass

    payload = _json.dumps(data, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=path.suffix, dir=str(parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # Set perms BEFORE the rename so the final file is world-rw the
        # instant it lands. After rename, only the (new) owner can chmod.
        os.chmod(tmp_name, 0o666)
        os.replace(tmp_name, str(path))
    except Exception:
        # Clean up the orphan temp file on any failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
