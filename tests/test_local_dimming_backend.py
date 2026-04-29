"""Tests for the LocalDimmingSim backend's pure logic.

The Fusion comp / tool API is not available in CI. We use lightweight
stubs that mimic the .Name / .GetAttrs / .SetInput surface to exercise
_find_macro_tool and _apply.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import pytest

from macroflow.backends import local_dimming


# ---------------------------------------------------------------------------
# Module-level constants. These are referenced from app.py and macro.py;
# regressing them silently would break the editor's quadrant dropdown
# and the preset list rendering.
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_macro_name_is_localdimmingsim(self):
        assert local_dimming.MACRO_NAME == "LocalDimmingSim"

    def test_quadrant_index_covers_four_corners_plus_full(self):
        assert set(local_dimming.QUADRANT_INDEX.keys()) == {
            "TL", "TR", "BL", "BR", "Full",
        }
        # Indices must be 0..4 with Full=4 (matches the Fusion macro's enum).
        assert local_dimming.QUADRANT_INDEX["TL"] == 0
        assert local_dimming.QUADRANT_INDEX["TR"] == 1
        assert local_dimming.QUADRANT_INDEX["BL"] == 2
        assert local_dimming.QUADRANT_INDEX["BR"] == 3
        assert local_dimming.QUADRANT_INDEX["Full"] == 4

    def test_presets_have_four_floats_each(self):
        for name, vals in local_dimming.PRESETS.items():
            assert len(vals) == 4, f"{name} should have 4 values"
            zx, zy, gain, black = vals
            assert isinstance(zx, int), f"{name} zones_x should be int"
            assert isinstance(zy, int), f"{name} zones_y should be int"
            assert isinstance(gain, (int, float)), f"{name} peak_gain should be number"
            assert isinstance(black, (int, float)), f"{name} black_nits should be number"

    def test_oled_presets_use_full_resolution_zones(self):
        # OLED presets simulate per-pixel control — zones should be 1920x1080.
        for name in ("LG C6 OLED", "Samsung S95F OLED", "Sony BVM-HX3110"):
            zx, zy, _, _ = local_dimming.PRESETS[name]
            assert (zx, zy) == (1920, 1080), f"{name} should use OLED zone grid"

    def test_oled_presets_have_zero_black_floor(self):
        for name in ("LG C6 OLED", "Samsung S95F OLED", "Sony BVM-HX3110"):
            _, _, _, black = local_dimming.PRESETS[name]
            assert black == 0.0


# ---------------------------------------------------------------------------
# _find_macro_tool: traverse a fake comp.GetToolList()
# ---------------------------------------------------------------------------

class _FakeTool:
    def __init__(self, name: str, regid: str = "MacroOperator"):
        self.Name = name
        self._regid = regid
        self.inputs: dict = {}

    def GetAttrs(self, key):  # NOQA: N802
        return self._regid if key == "TOOLS_RegID" else None

    def SetInput(self, key, value):  # NOQA: N802
        self.inputs[key] = value


class _FakeComp:
    def __init__(self, tools: list[_FakeTool]):
        self._tools = tools

    def GetToolList(self, ordered):  # NOQA: N802
        # Resolve returns a 1-keyed dict, not a list.
        return {i + 1: t for i, t in enumerate(self._tools)}


class TestFindMacroTool:
    def test_returns_none_for_none_comp(self):
        assert local_dimming._find_macro_tool(None) is None

    def test_returns_none_when_no_tools_match(self):
        comp = _FakeComp([_FakeTool("OtherMacro"), _FakeTool("YetAnother")])
        assert local_dimming._find_macro_tool(comp) is None

    def test_returns_macro_when_present(self):
        target = _FakeTool("LocalDimmingSim")
        comp = _FakeComp([_FakeTool("OtherMacro"), target])
        assert local_dimming._find_macro_tool(comp) is target

    def test_skips_non_macrooperator_tools_with_matching_name(self):
        # A non-macro tool that happens to be named LocalDimmingSim should
        # NOT match — RegID guards the type.
        non_macro = _FakeTool("LocalDimmingSim", regid="Background")
        comp = _FakeComp([non_macro])
        assert local_dimming._find_macro_tool(comp) is None

    def test_returns_none_when_gettoollist_raises(self):
        class Broken:
            def GetToolList(self, ordered):  # NOQA: N802
                raise RuntimeError("Fusion API hiccup")
        assert local_dimming._find_macro_tool(Broken()) is None

    def test_skips_tools_whose_getattrs_raises(self):
        # One tool's GetAttrs blows up; iteration must continue and find
        # the legitimate match further on.
        class BrokenTool:
            Name = "LocalDimmingSim"

            def GetAttrs(self, key):  # NOQA: N802
                raise RuntimeError("attr read failed")
        good = _FakeTool("LocalDimmingSim")
        comp = _FakeComp([BrokenTool(), good])
        assert local_dimming._find_macro_tool(comp) is good


# ---------------------------------------------------------------------------
# _apply: poke values onto the macro's inputs.
# ---------------------------------------------------------------------------

class TestApply:
    def _stub_resolve_with_tool(self, monkeypatch, tool):
        comp = _FakeComp([tool])
        monkeypatch.setattr(local_dimming, "_comp_for_current_clip", lambda: comp)

    def test_returns_false_when_macro_not_found(self, monkeypatch):
        # No matching macro tool on the current clip.
        monkeypatch.setattr(
            local_dimming, "_comp_for_current_clip",
            lambda: _FakeComp([]),
        )
        assert local_dimming._apply({"enabled": True}) is False

    def test_returns_false_when_no_comp(self, monkeypatch):
        monkeypatch.setattr(local_dimming, "_comp_for_current_clip", lambda: None)
        assert local_dimming._apply({"enabled": True}) is False

    def test_enabled_true_writes_one(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        assert local_dimming._apply({"enabled": True}) is True
        assert tool.inputs["Enabled"] == 1

    def test_enabled_false_writes_zero(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        assert local_dimming._apply({"enabled": False}) is True
        assert tool.inputs["Enabled"] == 0

    def test_quadrant_writes_index_value(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        local_dimming._apply({"quadrant": "BR"})
        assert tool.inputs["Quadrant"] == 3  # BR = 3

    def test_unknown_quadrant_is_silently_ignored(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        result = local_dimming._apply({"quadrant": "INVALID"})
        assert result is True
        assert "Quadrant" not in tool.inputs

    def test_preset_writes_zone_grid_and_levels(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        local_dimming._apply({"preset": "TCL QM9K"})
        assert tool.inputs["ZoneGridX"] == 104
        assert tool.inputs["ZoneGridY"] == 58
        assert tool.inputs["PeakNits"] == pytest.approx(0.95)
        assert tool.inputs["BlackLevelNits"] == pytest.approx(0.002)

    def test_unknown_preset_silently_ignored(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        result = local_dimming._apply({"preset": "Bogus TV"})
        assert result is True
        assert "ZoneGridX" not in tool.inputs

    def test_bloom_sigma_writes_float(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        local_dimming._apply({"bloom_sigma": 4.0})
        assert tool.inputs["BloomSigma"] == 4.0

    def test_bloom_sigma_none_is_skipped(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        local_dimming._apply({"bloom_sigma": None})
        assert "BloomSigma" not in tool.inputs

    def test_combined_state_writes_every_field(self, monkeypatch):
        tool = _FakeTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, tool)
        local_dimming._apply({
            "enabled": True,
            "quadrant": "TL",
            "preset": "TCL X11L",
            "bloom_sigma": 2.5,
        })
        assert tool.inputs["Enabled"] == 1
        assert tool.inputs["Quadrant"] == 0  # TL
        assert tool.inputs["ZoneGridX"] == 192
        assert tool.inputs["ZoneGridY"] == 108
        assert tool.inputs["BloomSigma"] == 2.5

    def test_setinput_exception_returns_false(self, monkeypatch):
        class BrokenTool(_FakeTool):
            def SetInput(self, key, value):  # NOQA: N802
                raise RuntimeError("Fusion crashed")
        broken = BrokenTool("LocalDimmingSim")
        self._stub_resolve_with_tool(monkeypatch, broken)
        assert local_dimming._apply({"enabled": True}) is False
