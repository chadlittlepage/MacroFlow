"""Tests for the Macro / MacroGrid / MacroStore data model.

These hit the pure-logic surface of `macroflow.macro` — no Cocoa, no
network, no DaVinci Resolve. Backend `fire()` calls are exercised only
to the extent that no-op (unset) actions short-circuit cleanly; we do
not stand up real backends here.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from macroflow.macro import (
    LocalDimmingAction,
    Macro,
    MacroGrid,
    MacroStore,
    ResolveAction,
    VideohubAction,
    atomic_write_shared_json,
)


# ---------------------------------------------------------------------------
# Action.is_set semantics
# ---------------------------------------------------------------------------

class TestActionIsSet:
    def test_videohub_unset_when_blank(self):
        assert not VideohubAction().is_set()

    def test_videohub_unset_when_only_device(self):
        assert not VideohubAction(device_id="dev1").is_set()

    def test_videohub_unset_when_only_preset(self):
        assert not VideohubAction(preset_name="A").is_set()

    def test_videohub_set_when_both_filled(self):
        assert VideohubAction(device_id="dev1", preset_name="A").is_set()

    def test_resolve_unset_when_no_tracks(self):
        assert not ResolveAction().is_set()

    def test_resolve_unset_with_only_transforms(self):
        # Transforms without an enable/disable map don't fire.
        assert not ResolveAction(track_transforms={1: {"Quadrant": "Q1"}}).is_set()

    def test_resolve_set_with_tracks(self):
        assert ResolveAction(tracks={1: True}).is_set()

    def test_local_dimming_unset_by_default(self):
        assert not LocalDimmingAction().is_set()

    def test_local_dimming_set_via_enabled_false(self):
        # `enabled=False` is explicit "turn off", not "leave alone."
        assert LocalDimmingAction(enabled=False).is_set()

    def test_local_dimming_set_via_quadrant(self):
        assert LocalDimmingAction(quadrant="TL").is_set()

    def test_local_dimming_set_via_preset(self):
        assert LocalDimmingAction(preset="X11L").is_set()

    def test_local_dimming_set_via_bloom(self):
        assert LocalDimmingAction(bloom_sigma=2.5).is_set()


# ---------------------------------------------------------------------------
# Action.fire short-circuits on unset
# ---------------------------------------------------------------------------

class TestActionFireShortCircuit:
    def test_videohub_unset_fire_returns_true(self):
        # Hitting the network when the action is unset would be a bug.
        assert VideohubAction().fire() is True

    def test_resolve_unset_fire_returns_true(self):
        assert ResolveAction().fire() is True

    def test_local_dimming_unset_fire_returns_true(self):
        assert LocalDimmingAction().fire() is True


# ---------------------------------------------------------------------------
# Macro.to_dict / from_dict round-trip
# ---------------------------------------------------------------------------

class TestMacroRoundtrip:
    def test_minimal_roundtrip(self):
        m = Macro(id="0,0")
        restored = Macro.from_dict(m.to_dict())
        assert restored.id == m.id
        assert restored.label == ""
        assert restored.color == "#4a556c"

    def test_full_roundtrip_preserves_every_field(self):
        m = Macro(
            id="2,3",
            label="Test 1",
            color="#aabbcc",
            hotkey="F1",
            hotkey_modifier="Cmd",
            videohub_enabled=True,
            videohub=VideohubAction(device_id="vh1", preset_name="P1"),
            resolve=ResolveAction(
                tracks={1: True, 2: False, 5: True},
                track_transforms={
                    1: {"Quadrant": "Q1", "ZoomX": 0.5},
                    2: {"Pan": 1920, "Tilt": -1080},
                },
                track_names={1: "V1", 2: "V2"},
            ),
            local_dimming=LocalDimmingAction(
                enabled=True, quadrant="BR", preset="QM9K", bloom_sigma=4.0,
            ),
        )
        restored = Macro.from_dict(m.to_dict())
        assert restored.id == "2,3"
        assert restored.label == "Test 1"
        assert restored.color == "#aabbcc"
        assert restored.hotkey == "F1"
        assert restored.hotkey_modifier == "Cmd"
        assert restored.videohub_enabled is True
        assert restored.videohub.device_id == "vh1"
        assert restored.videohub.preset_name == "P1"
        assert restored.resolve.tracks == {1: True, 2: False, 5: True}
        assert restored.resolve.track_transforms[1]["Quadrant"] == "Q1"
        assert restored.resolve.track_transforms[2]["Pan"] == 1920
        assert restored.resolve.track_names == {1: "V1", 2: "V2"}
        assert restored.local_dimming.enabled is True
        assert restored.local_dimming.quadrant == "BR"
        assert restored.local_dimming.preset == "QM9K"
        assert restored.local_dimming.bloom_sigma == 4.0

    def test_track_keys_coerced_to_int_after_json_trip(self):
        # JSON serializes int dict keys as strings — from_dict must coerce
        # back so callers don't end up with mixed-type dicts.
        m = Macro(
            id="0,0",
            resolve=ResolveAction(tracks={3: True, 7: False}),
        )
        as_json = json.dumps(m.to_dict())
        restored = Macro.from_dict(json.loads(as_json))
        assert restored.resolve.tracks == {3: True, 7: False}
        assert all(isinstance(k, int) for k in restored.resolve.tracks)

    def test_from_dict_handles_missing_subfields(self):
        # Old configs may have only id + label + color; everything else
        # defaults cleanly without raising.
        restored = Macro.from_dict({"id": "1,1", "label": "X"})
        assert restored.id == "1,1"
        assert restored.label == "X"
        assert restored.videohub.device_id == ""
        assert restored.resolve.tracks == {}
        assert restored.local_dimming.enabled is None

    def test_from_dict_local_dimming_enabled_none_is_preserved(self):
        # `None` means "leave alone" — must round-trip through JSON intact.
        m = Macro(id="0,0", local_dimming=LocalDimmingAction(enabled=None))
        restored = Macro.from_dict(json.loads(json.dumps(m.to_dict())))
        assert restored.local_dimming.enabled is None

    def test_from_dict_drops_non_dict_track_transforms(self):
        # If a config got hand-edited and a transform value is malformed
        # (string instead of dict), from_dict should skip it, not crash.
        data = {
            "id": "0,0",
            "resolve": {
                "tracks": {"1": True},
                "track_transforms": {"1": "garbage", "2": {"Quadrant": "Q2"}},
            },
        }
        restored = Macro.from_dict(data)
        assert 1 not in restored.resolve.track_transforms
        assert restored.resolve.track_transforms[2]["Quadrant"] == "Q2"


# ---------------------------------------------------------------------------
# MacroGrid cell-keyed map
# ---------------------------------------------------------------------------

class TestMacroGrid:
    def test_cell_id_format(self):
        assert MacroGrid.cell_id(0, 0) == "0,0"
        assert MacroGrid.cell_id(3, 7) == "3,7"

    def test_get_returns_none_for_empty_cell(self):
        g = MacroGrid()
        assert g.get(0, 0) is None

    def test_set_normalizes_macro_id_to_cell_coordinates(self):
        # Macro.id should always reflect its actual cell, even if the
        # caller passed in an id that doesn't match.
        g = MacroGrid()
        m = Macro(id="wrong_id", label="X")
        g.set(2, 3, m)
        retrieved = g.get(2, 3)
        assert retrieved is not None
        assert retrieved.id == "2,3"

    def test_clear_removes_macro(self):
        g = MacroGrid()
        g.set(1, 1, Macro(id="placeholder", label="Y"))
        assert g.get(1, 1) is not None
        g.clear(1, 1)
        assert g.get(1, 1) is None

    def test_clear_is_idempotent_on_empty_cell(self):
        # Clearing a cell that's already empty must not raise.
        g = MacroGrid()
        g.clear(5, 5)

    def test_fire_returns_none_for_empty_cell(self):
        g = MacroGrid()
        assert g.fire(0, 0) is None

    def test_fire_unset_macro_returns_empty_results(self):
        # A macro with no actions set fires nothing → results dict empty.
        g = MacroGrid()
        g.set(0, 0, Macro(id="0,0", label="Empty"))
        results = g.fire(0, 0)
        assert results == {}


# ---------------------------------------------------------------------------
# MacroStore persistence
# ---------------------------------------------------------------------------

class TestMacroStore:
    def test_load_missing_file_starts_with_default_grid(self, tmp_path: Path):
        store = MacroStore(path=tmp_path / "nope.json")
        assert store.grid.rows == 4
        assert store.grid.cols == 4
        assert store.grid.macros == {}

    def test_save_then_load_round_trip(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        store = MacroStore(path=cfg)
        store.grid.rows = 6
        store.grid.cols = 8
        store.grid.macros["0,0"] = Macro(id="0,0", label="Hello")
        store.grid.display_font_size = 18.0
        store.grid.title_font_size = 16.0
        store.grid.hotkey_font_size = 32.0
        store.grid.keep_on_top = True
        store.grid.global_hotkeys = True
        store.save()

        store2 = MacroStore(path=cfg)
        assert store2.grid.rows == 6
        assert store2.grid.cols == 8
        assert "0,0" in store2.grid.macros
        assert store2.grid.macros["0,0"].label == "Hello"
        assert store2.grid.display_font_size == 18.0
        assert store2.grid.title_font_size == 16.0
        assert store2.grid.hotkey_font_size == 32.0
        assert store2.grid.keep_on_top is True
        assert store2.grid.global_hotkeys is True

    def test_load_refuses_to_follow_symlink(self, tmp_path: Path):
        # A malicious user planting a symlink at the shared config path
        # must not redirect MacroFlow into reading another file.
        target = tmp_path / "evil.json"
        target.write_text(json.dumps({"rows": 99, "cols": 99}))
        cfg = tmp_path / "macroflow.json"
        os.symlink(target, cfg)

        store = MacroStore(path=cfg)
        # Loading was refused → defaults preserved, evil values not picked up.
        assert store.grid.rows == 4
        assert store.grid.cols == 4

    def test_load_with_corrupt_json_falls_back_to_defaults(
        self, tmp_path: Path, capsys,
    ):
        cfg = tmp_path / "macroflow.json"
        cfg.write_text("{ not json")
        store = MacroStore(path=cfg)
        assert store.grid.rows == 4
        assert "Failed to load" in capsys.readouterr().out

    def test_snapshot_current_captures_grid_shape(self, tmp_path: Path):
        store = MacroStore(path=tmp_path / "macroflow.json")
        store.grid.rows = 5
        store.grid.cols = 3
        store.grid.macros["1,2"] = Macro(id="1,2", label="snap")
        snap = store.snapshot_current()
        assert snap["rows"] == 5
        assert snap["cols"] == 3
        assert "1,2" in snap["macros"]
        assert snap["macros"]["1,2"]["label"] == "snap"

    def test_save_persists_presets(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        store = MacroStore(path=cfg)
        store.grid.presets["MyPreset"] = {
            "rows": 4, "cols": 4, "macros": {"0,0": {"id": "0,0", "label": "P"}},
        }
        store.save()

        store2 = MacroStore(path=cfg)
        assert "MyPreset" in store2.grid.presets
        assert store2.grid.presets["MyPreset"]["macros"]["0,0"]["label"] == "P"


# ---------------------------------------------------------------------------
# atomic_write_shared_json
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_creates_file_with_world_rw_perms(self, tmp_path: Path):
        path = tmp_path / "subdir" / "out.json"
        atomic_write_shared_json(path, {"hello": "world"})
        assert path.exists()
        # Must be world-readable + writable so a second user can save.
        mode = path.stat().st_mode & 0o777
        assert mode == 0o666

    def test_replaces_existing_file_atomically(self, tmp_path: Path):
        path = tmp_path / "out.json"
        path.write_text(json.dumps({"old": True}))
        atomic_write_shared_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"new": True}

    def test_replaces_symlink_at_target(self, tmp_path: Path):
        # The whole point: writing should swap the file at `path`, never
        # follow a symlink that's sitting there.
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text(json.dumps({"untouched": True}))
        path = tmp_path / "macroflow.json"
        os.symlink(elsewhere, path)

        atomic_write_shared_json(path, {"new": "data"})
        # `path` is no longer a symlink — it's a regular file.
        assert not path.is_symlink()
        # The original target wasn't touched.
        assert json.loads(elsewhere.read_text()) == {"untouched": True}

    def test_writes_valid_json(self, tmp_path: Path):
        path = tmp_path / "out.json"
        payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        atomic_write_shared_json(path, payload)
        assert json.loads(path.read_text()) == payload

    def test_no_orphan_temp_file_on_success(self, tmp_path: Path):
        path = tmp_path / "out.json"
        atomic_write_shared_json(path, {"x": 1})
        # Only the final file should exist — no .tmp.* siblings.
        siblings = list(tmp_path.iterdir())
        assert siblings == [path]


# ---------------------------------------------------------------------------
# Defaults sanity check (regressions on dataclass field changes)
# ---------------------------------------------------------------------------

def test_macro_grid_defaults():
    g = MacroGrid()
    assert g.rows == 4
    assert g.cols == 4
    assert g.macros == {}
    assert g.videohub_enabled is True
    assert g.keep_on_top is False
    assert g.global_hotkeys is False
    assert g.display_font_size == 12.0
    assert g.title_font_size == 13.0
    assert g.hotkey_font_size == 26.0


def test_macro_defaults_match_color_rule():
    # CLAUDE.md global rule 001: primary color is #4a556c.
    assert Macro(id="0,0").color == "#4a556c"


@pytest.mark.parametrize(
    "modifier",
    ["", "Cmd", "Ctrl", "Opt", "Shift"],
)
def test_hotkey_modifier_round_trip(modifier: str):
    m = Macro(id="0,0", hotkey="a", hotkey_modifier=modifier)
    restored = Macro.from_dict(m.to_dict())
    assert restored.hotkey_modifier == modifier
