"""Smoke tests for the LocalDimming MacroFlow integration.

These don't require Resolve to be running — `safe_apply` returns False
when no comp is reachable, but the action layer (is_set/fire dispatch,
serialization round-trip) is still exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macroflow.backends import local_dimming
from macroflow.macro import LocalDimmingAction, Macro


def test_empty_action_is_noop():
    a = LocalDimmingAction()
    assert a.is_set() is False
    # Empty action should fire successfully without touching Resolve.
    assert a.fire() is True


def test_action_is_set_when_any_field_populated():
    assert LocalDimmingAction(enabled=True).is_set()
    assert LocalDimmingAction(enabled=False).is_set()
    assert LocalDimmingAction(quadrant="TL").is_set()
    assert LocalDimmingAction(preset="TCL QM9K").is_set()
    assert LocalDimmingAction(bloom_sigma=4.0).is_set()


def test_macro_roundtrip_preserves_local_dimming():
    m = Macro(
        id="2,3",
        label="Dim ON",
        local_dimming=LocalDimmingAction(
            enabled=True, quadrant="BR", preset="TCL X11L", bloom_sigma=18.0
        ),
    )
    d = m.to_dict()
    assert d["local_dimming"]["preset"] == "TCL X11L"
    m2 = Macro.from_dict(d)
    assert m2.local_dimming.enabled is True
    assert m2.local_dimming.quadrant == "BR"
    assert m2.local_dimming.preset == "TCL X11L"
    assert m2.local_dimming.bloom_sigma == 18.0


def test_macro_roundtrip_with_no_local_dimming_block():
    """Old configs without a local_dimming key should still load."""
    m = Macro.from_dict({"id": "0,0", "label": "legacy"})
    assert m.local_dimming.is_set() is False


def test_presets_table_matches_known_models():
    expected = {
        "Budget Edge-Lit", "Samsung QN90F", "Sony BRAVIA 9", "Hisense U8QG",
        "TCL QM8K", "Hisense U9QG", "TCL QM9K", "TCL X11L",
        "LG C6 OLED", "Samsung S95F OLED", "Sony BVM-HX3110",
    }
    assert set(local_dimming.PRESETS.keys()) == expected
    # Each preset is a 4-tuple of (zones_x, zones_y, peak_gain, black_nits)
    for name, spec in local_dimming.PRESETS.items():
        assert len(spec) == 4, f"{name} has wrong shape: {spec}"
        zx, zy, gain, black = spec
        assert zx > 0 and zy > 0
        assert 0 < gain <= 2.5
        assert black >= 0
