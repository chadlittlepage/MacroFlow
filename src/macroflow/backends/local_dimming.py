"""LocalDimmingSim Fusion macro toggle for MacroFlow.

The macro itself ships with the Chad's DaVinci Script project. This backend
reaches into Resolve's current-clip Fusion comp and pokes the macro's
Inputs (Enabled / Quadrant / ZoneGridX|Y / PeakNits / BloomSigma /
BlackLevelNits). Bool-y action: nothing happens unless `is_set()` says so.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

from macroflow.backends import resolve as resolve_backend

MACRO_NAME = "LocalDimmingSim"
QUADRANT_INDEX = {"TL": 0, "TR": 1, "BL": 2, "BR": 3, "Full": 4}

# Display class presets — mirror chads_davinci.local_dimming.PRESETS so
# MacroFlow doesn't need that package as a dependency. (zones_x, zones_y,
# peak_gain @ 4000-nit ref, black_nits)
PRESETS: dict[str, tuple[int, int, float, float]] = {
    "Budget Edge-Lit":   (8, 4,        0.100, 0.05),
    "Samsung QN90F":     (40, 23,      0.625, 0.005),
    "Sony BRAVIA 9":     (60, 34,      0.700, 0.002),
    "Hisense U8QG":      (61, 34,      0.750, 0.002),
    "TCL QM8K":          (83, 46,      0.800, 0.002),
    "Hisense U9QG":      (95, 53,      1.125, 0.002),
    "TCL QM9K":          (104, 58,     0.950, 0.002),
    "TCL X11L":          (192, 108,    1.350, 0.001),
    "LG C6 OLED":        (1920, 1080,  0.250, 0.0),
    "Samsung S95F OLED": (1920, 1080,  0.500, 0.0),
    "Sony BVM-HX3110":   (1920, 1080,  1.000, 0.0),
}


def _find_macro_tool(comp):
    """Locate the LocalDimmingSim macro on the comp.

    Holds the iterating tool list locally and drops it before return so
    no Fusion ScriptVal escapes via a closure or stack frame longer than
    necessary. Caller is expected to also drop the returned `tool` ref
    on the worker thread when done.
    """
    if comp is None:
        return None
    try:
        tools = comp.GetToolList(False) or {}
    except Exception:
        return None
    found = None
    try:
        for tool in tools.values():
            try:
                if (
                    tool.GetAttrs("TOOLS_RegID") == "MacroOperator"
                    and tool.Name == MACRO_NAME
                ):
                    found = tool
                    break
            except Exception:
                continue
    finally:
        # `tools` (a Fusion-managed dict of ScriptVals) goes out of scope
        # here; let it die on this thread, not the caller's.
        tools = None
    return found


def _comp_for_current_clip():
    """Get the Fusion comp on the current clip via the resolve backend.

    Walks the Resolve→PM→Project→Timeline→Item chain and grabs the
    Fusion comp. Every intermediate ScriptVal is dropped before return
    so only the comp itself is held by the caller.
    """
    if not resolve_backend.connect():
        return None
    r = resolve_backend._resolve  # noqa: SLF001 — package-internal handle
    pm = proj = tl = item = None
    comp = None
    try:
        pm = r.GetProjectManager()
        if pm is None:
            return None
        proj = pm.GetCurrentProject()
        if proj is None:
            return None
        tl = proj.GetCurrentTimeline()
        if tl is None:
            return None
        item = tl.GetCurrentVideoItem()
        if item is None:
            return None
        try:
            count = item.GetFusionCompCount()
        except Exception:
            count = 0
        if count and count > 0:
            comp = item.GetFusionCompByIndex(1)
        return comp
    except Exception as e:
        print(f"[local_dimming] resolve traversal failed: {e}")
        return None
    finally:
        # Drop intermediate ScriptVal refs on this thread.
        del pm, proj, tl, item


def _apply(state: dict) -> bool:
    """Worker: push state onto the macro's inputs.

    Drops every Fusion ScriptVal reference (comp, tool) before returning
    so they get freed deterministically on the same worker thread that
    created them. macOS 15 + Resolve 20.1 segfaults if a Fusion ScriptVal
    is destroyed on a different thread than it was created on.
    """
    import gc

    comp = _comp_for_current_clip()
    tool = _find_macro_tool(comp)
    if tool is None:
        print("[local_dimming] LocalDimmingSim not found on current clip")
        del comp, tool
        gc.collect()
        return False

    ok = True
    try:
        if "enabled" in state:
            tool.SetInput("Enabled", 1 if state["enabled"] else 0)
        if "quadrant" in state and state["quadrant"] in QUADRANT_INDEX:
            tool.SetInput("Quadrant", QUADRANT_INDEX[state["quadrant"]])
        if "preset" in state and state["preset"] in PRESETS:
            zx, zy, gain, black = PRESETS[state["preset"]]
            tool.SetInput("ZoneGridX", zx)
            tool.SetInput("ZoneGridY", zy)
            tool.SetInput("PeakNits", gain)
            tool.SetInput("BlackLevelNits", black)
        if "bloom_sigma" in state and state["bloom_sigma"] is not None:
            tool.SetInput("BloomSigma", float(state["bloom_sigma"]))
    except Exception as e:
        print(f"[local_dimming] SetInput failed: {e}")
        ok = False
    finally:
        # Drop Fusion ScriptVal refs on this thread, not the caller's.
        del tool, comp
        gc.collect()
    return ok


def safe_apply(state: dict) -> bool:
    """Run _apply on a worker thread (Fusion + Cocoa main thread = bad)."""
    from functools import partial
    return bool(resolve_backend._run_off_main(  # noqa: SLF001
        partial(_apply, state), default=False))
