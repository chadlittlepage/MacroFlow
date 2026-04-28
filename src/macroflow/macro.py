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
        return resolve.apply_track_state(normalized)


@dataclass
class Macro:
    id: str
    label: str = ""
    color: str = "#4a556c"  # primary color (CLAUDE.md global rule 001)
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
        try:
            data = json.loads(self.path.read_text())
        except Exception as e:
            print(f"[macro] Failed to load {self.path}: {e}")
            return
        self.grid.rows = int(data.get("rows", 4))
        self.grid.cols = int(data.get("cols", 4))
        self.grid.macros = {
            mid: Macro.from_dict(m) for mid, m in (data.get("macros") or {}).items()
        }
        print(f"[macro] Loaded {len(self.grid.macros)} macro(s) "
              f"({self.grid.rows}x{self.grid.cols})")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        data = {
            "rows": self.grid.rows,
            "cols": self.grid.cols,
            "macros": {mid: m.to_dict() for mid, m in self.grid.macros.items()},
        }
        try:
            self.path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[macro] Failed to write {self.path}: {e}")
