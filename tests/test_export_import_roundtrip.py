"""End-to-end Export → Reset All → Import + v0.1.1 backwards-compat tests.

The first test simulates a real user who:
  1. Has a maximally-loaded config (every persisted field populated).
  2. Hits Export Settings… (file copy of macroflow.json).
  3. Hits Reset All (everything wiped).
  4. Hits Import Settings… (file rewritten + reloaded in place).

The second test simulates a v0.1.1 user updating to the current build —
their macroflow.json on disk lacks the timeline_resolution field; the
new MacroStore must read it cleanly with the field defaulted to "auto"
without dropping any other v0.1.1 setting, macro, or preset.

Run via:
    PYTHONPATH=src pytest tests/test_export_import_roundtrip.py -v

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import json
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


def _grid_to_persistent_dict(g: MacroGrid) -> dict:
    """Match the shape MacroStore.save writes — used to compare round-trip
    output against a freshly-built grid without depending on key ordering."""
    return {
        "rows": g.rows,
        "cols": g.cols,
        "mock_videohub": g.mock_videohub,
        "videohub_enabled": g.videohub_enabled,
        "keep_on_top": g.keep_on_top,
        "global_hotkeys": g.global_hotkeys,
        "font_sizes": {
            "display": g.display_font_size,
            "title": g.title_font_size,
            "hotkey": g.hotkey_font_size,
        },
        "timeline_resolution": g.timeline_resolution,
        "macros": {mid: m.to_dict() for mid, m in g.macros.items()},
        "presets": dict(g.presets),
    }


def _make_loaded_grid() -> MacroGrid:
    """A grid that exercises every persisted field at least once."""
    g = MacroGrid()
    g.rows = 6
    g.cols = 8
    g.mock_videohub = True
    g.videohub_enabled = False
    g.keep_on_top = True
    g.global_hotkeys = True
    g.display_font_size = 18.0
    g.title_font_size = 22.0
    g.hotkey_font_size = 40.0
    g.timeline_resolution = "7680x4320"

    m1 = Macro(
        id="0,0",
        label="Hero Studio",
        color="#aabbcc",
        hotkey="F1",
        hotkey_modifier="Cmd",
        videohub_enabled=True,
        videohub=VideohubAction(device_id="vh-studio-a", preset_name="Show Open"),
        resolve=ResolveAction(
            tracks={1: True, 2: False, 5: True, 8: True},
            track_transforms={
                1: {
                    "quadrant": "Q2",
                    "zoom_x": 0.5, "zoom_y": 0.5,
                    "position_x": 1920.0, "position_y": 1080.0,
                    "rotation_angle": 12.5,
                    "anchor_point_x": 100.0, "anchor_point_y": -50.0,
                    "pitch": 4.0, "yaw": -3.0,
                    "flip_h": True, "flip_v": False,
                },
                5: {
                    "quadrant": "Q4",
                    "position_x": 1920.0, "position_y": -1080.0,
                },
            },
            track_names={
                1: "V1 - cam-A", 2: "V2 - cam-B",
                5: "V5 - lower-3rd", 8: "V8 - bug",
            },
        ),
        local_dimming=LocalDimmingAction(
            enabled=True, quadrant="BR", preset="TCL X11L", bloom_sigma=4.5,
        ),
    )
    m2 = Macro(id="3,4", label="Q&A camera", color="#445566", hotkey="q")
    # Critical: enabled=False (NOT None) — the trickiest round-trip case.
    m3 = Macro(
        id="5,7",
        label="Dim off",
        local_dimming=LocalDimmingAction(enabled=False),
    )
    g.macros = {m1.id: m1, m2.id: m2, m3.id: m3}

    g.presets["Show A"] = {
        "rows": 6, "cols": 8,
        "macros": {m1.id: m1.to_dict(), m2.id: m2.to_dict()},
    }
    g.presets["Bumper"] = {
        "rows": 4, "cols": 4,
        "macros": {m1.id: m1.to_dict()},
    }
    return g


# ===========================================================================
# Full round-trip: Save → Export → Reset → Import → reload
# ===========================================================================

class TestExportImportRoundtrip:
    def test_save_writes_every_persisted_field(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        store = MacroStore(path=cfg)
        store.grid = _make_loaded_grid()
        store.save()

        on_disk = json.loads(cfg.read_text())
        expected = _grid_to_persistent_dict(store.grid)
        assert on_disk == expected

    def test_export_is_byte_equivalent_copy(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        export_dest = tmp_path / "exported.json"
        store = MacroStore(path=cfg)
        store.grid = _make_loaded_grid()
        store.save()

        # Export = file copy. (The live app uses NSSavePanel + json.dump,
        # both of which preserve content verbatim.)
        export_dest.write_text(cfg.read_text())
        assert json.loads(export_dest.read_text()) == json.loads(cfg.read_text())

    def test_reset_then_import_restores_everything(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        export_dest = tmp_path / "exported.json"

        # Save a loaded grid + take an export snapshot.
        store = MacroStore(path=cfg)
        original_grid = _make_loaded_grid()
        store.grid = original_grid
        store.save()
        export_dest.write_text(cfg.read_text())
        expected = _grid_to_persistent_dict(original_grid)

        # Reset All — replace with default grid + persist.
        store.grid = MacroGrid()
        store.save()
        after_reset = json.loads(cfg.read_text())
        assert after_reset["rows"] == 4
        assert after_reset["cols"] == 4
        assert after_reset["macros"] == {}
        assert after_reset["presets"] == {}

        # Import — atomic-write the export over CONFIG_PATH + reload.
        atomic_write_shared_json(cfg, json.loads(export_dest.read_text()))
        reloaded = MacroStore(path=cfg)
        actual = _grid_to_persistent_dict(reloaded.grid)
        assert actual == expected

    def test_local_dimming_enabled_false_survives_roundtrip(
        self, tmp_path: Path,
    ):
        # `enabled=False` must NOT be coerced to None on load — it means
        # "explicitly turn off the macro," whereas None means "leave alone."
        cfg = tmp_path / "macroflow.json"
        store = MacroStore(path=cfg)
        store.grid = _make_loaded_grid()
        store.save()
        reloaded = MacroStore(path=cfg)
        m = reloaded.grid.macros["5,7"]
        assert m.local_dimming.enabled is False

    def test_preset_inner_macros_preserved(self, tmp_path: Path):
        cfg = tmp_path / "macroflow.json"
        store = MacroStore(path=cfg)
        store.grid = _make_loaded_grid()
        store.save()
        reloaded = MacroStore(path=cfg)
        assert "Show A" in reloaded.grid.presets
        assert reloaded.grid.presets["Show A"]["rows"] == 6
        assert "0,0" in reloaded.grid.presets["Show A"]["macros"]
        # Recall reconstitutes — Macro.from_dict on each entry.
        recalled = Macro.from_dict(
            reloaded.grid.presets["Show A"]["macros"]["0,0"],
        )
        assert recalled.label == "Hero Studio"
        assert recalled.videohub.device_id == "vh-studio-a"
        assert recalled.resolve.tracks == {1: True, 2: False, 5: True, 8: True}


# ===========================================================================
# v0.1.1 → current backwards compatibility
# ===========================================================================

# Realistic v0.1.1 config — no `timeline_resolution` key.
_V011_CONFIG = {
    "rows": 6,
    "cols": 8,
    "mock_videohub": False,
    "videohub_enabled": True,
    "keep_on_top": True,
    "global_hotkeys": False,
    "font_sizes": {"display": 16, "title": 14, "hotkey": 32},
    # NOTE: no "timeline_resolution" key — this is the v0.1.1 schema.
    "presets": {
        "Show A": {
            "rows": 6, "cols": 8,
            "macros": {
                "0,0": {
                    "id": "0,0",
                    "label": "Hero Cam",
                    "color": "#aabbcc",
                    "hotkey": "F1",
                    "hotkey_modifier": "Cmd",
                    "videohub_enabled": True,
                    "videohub": {
                        "device_id": "studio-a",
                        "preset_name": "Open Show",
                    },
                    "resolve": {
                        "tracks": {"1": True, "3": False},
                        "track_transforms": {
                            "1": {
                                "quadrant": "Q2",
                                "zoom_x": 0.5, "zoom_y": 0.5,
                                "position_x": 1920.0, "position_y": 1080.0,
                            },
                        },
                        "track_names": {"1": "Cam-A", "3": "Lower-3rd"},
                    },
                },
            },
        },
        "Bumper": {
            "rows": 4, "cols": 4,
            "macros": {
                "0,0": {
                    "id": "0,0", "label": "Bug",
                    "color": "#445566", "hotkey": "b",
                },
            },
        },
    },
    "macros": {
        "0,0": {
            "id": "0,0", "label": "TEST 1",
            "color": "#a4a833",
            "hotkey": "1", "hotkey_modifier": "Cmd",
            "videohub_enabled": False,
            "videohub": {"device_id": "studio-a", "preset_name": "1 Preset"},
            "resolve": {
                "tracks": {"1": True},
                "track_transforms": {"1": {"quadrant": "Q1"}},
                "track_names": {"1": "Hero Cam"},
            },
        },
        "1,2": {
            "id": "1,2", "label": "Q&A",
            "color": "#33aa55", "hotkey": "q",
        },
    },
}


class TestV011BackwardsCompat:
    @pytest.fixture
    def v011_store(self, tmp_path: Path) -> MacroStore:
        cfg = tmp_path / "macroflow.json"
        cfg.write_text(json.dumps(_V011_CONFIG, indent=2))
        return MacroStore(path=cfg)

    def test_missing_timeline_resolution_defaults_to_auto(self, v011_store):
        assert v011_store.grid.timeline_resolution == "auto"

    def test_every_v011_top_level_setting_survives(self, v011_store):
        g = v011_store.grid
        assert g.rows == 6
        assert g.cols == 8
        assert g.mock_videohub is False
        assert g.videohub_enabled is True
        assert g.keep_on_top is True
        assert g.global_hotkeys is False
        assert g.display_font_size == 16.0
        assert g.title_font_size == 14.0
        assert g.hotkey_font_size == 32.0

    def test_top_level_macros_load_with_full_fidelity(self, v011_store):
        m = v011_store.grid.macros["0,0"]
        assert m.label == "TEST 1"
        assert m.color == "#a4a833"
        assert m.hotkey == "1"
        assert m.hotkey_modifier == "Cmd"
        assert m.resolve.tracks == {1: True}

    def test_presets_preserved_with_inner_snapshots(self, v011_store):
        g = v011_store.grid
        assert set(g.presets.keys()) == {"Show A", "Bumper"}
        assert g.presets["Show A"]["rows"] == 6
        assert g.presets["Show A"]["cols"] == 8
        assert "0,0" in g.presets["Show A"]["macros"]

    def test_preset_recall_reconstitutes_every_macro(self, v011_store):
        snap = v011_store.grid.presets["Show A"]
        recalled = {mid: Macro.from_dict(d) for mid, d in snap["macros"].items()}
        hero = recalled["0,0"]
        assert hero.label == "Hero Cam"
        assert hero.videohub.device_id == "studio-a"
        assert hero.videohub.preset_name == "Open Show"
        assert hero.resolve.tracks == {1: True, 3: False}
        x = hero.resolve.track_transforms.get(1, {})
        assert x.get("zoom_x") == 0.5
        assert x.get("position_x") == 1920.0

    def test_lossless_upgrade_on_resave(self, tmp_path: Path):
        # First load, then save — the new field should be added but no
        # old field should be lost.
        cfg = tmp_path / "macroflow.json"
        cfg.write_text(json.dumps(_V011_CONFIG, indent=2))
        store = MacroStore(path=cfg)
        store.save()
        upgraded = json.loads(cfg.read_text())
        assert upgraded["timeline_resolution"] == "auto"
        for key in (
            "rows", "cols", "mock_videohub", "videohub_enabled",
            "keep_on_top", "global_hotkeys", "font_sizes",
            "presets", "macros",
        ):
            assert key in upgraded, f"re-save dropped {key!r}"
        # Ensure user data wasn't corrupted.
        assert upgraded["macros"]["0,0"]["label"] == "TEST 1"
        assert "Show A" in upgraded["presets"]
