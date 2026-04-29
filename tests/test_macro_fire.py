"""Cover the Macro.fire parallel-thread path + per-action fire forwarding.

Action.fire calls poke real backends (videohub TCP, Resolve API,
LocalDimmingSim). We replace those with monkeypatched stubs so the
tests stay hermetic.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import pytest

from macroflow.backends import local_dimming as ld_backend
from macroflow.backends import resolve as resolve_backend
from macroflow.backends import videohub as videohub_backend
from macroflow.macro import (
    LocalDimmingAction,
    Macro,
    ResolveAction,
    VideohubAction,
)


# ---------------------------------------------------------------------------
# VideohubAction.fire forwards to videohub.recall_preset
# ---------------------------------------------------------------------------

class TestVideohubActionFireForwarding:
    def test_calls_recall_preset_with_device_and_name(self, monkeypatch):
        seen: list[tuple[str, str]] = []

        def fake_recall(device_id, preset_name, timeout=2.0):
            seen.append((device_id, preset_name))
            return True

        monkeypatch.setattr(videohub_backend, "recall_preset", fake_recall)
        action = VideohubAction(device_id="d1", preset_name="p1")
        assert action.fire() is True
        assert seen == [("d1", "p1")]

    def test_failure_propagates_false(self, monkeypatch):
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: False,
        )
        action = VideohubAction(device_id="d1", preset_name="p1")
        assert action.fire() is False


# ---------------------------------------------------------------------------
# ResolveAction.fire normalizes JSON-loaded keys (str → int)
# ---------------------------------------------------------------------------

class TestResolveActionFireForwarding:
    def test_int_key_coercion_before_forwarding(self, monkeypatch):
        seen: list[dict] = []

        def fake_apply(track_state):
            seen.append(track_state)
            return True

        monkeypatch.setattr(
            resolve_backend, "safe_apply_track_state", fake_apply,
        )
        # Simulate a config that came back from JSON with str keys.
        action = ResolveAction(tracks={"1": True, "2": False})  # type: ignore[arg-type]
        assert action.fire() is True
        # The forwarded dict has int keys + bool values.
        assert seen == [{1: True, 2: False}]

    def test_failure_propagates(self, monkeypatch):
        monkeypatch.setattr(
            resolve_backend, "safe_apply_track_state", lambda ts: False,
        )
        action = ResolveAction(tracks={1: True})
        assert action.fire() is False


# ---------------------------------------------------------------------------
# LocalDimmingAction.fire builds the right state dict
# ---------------------------------------------------------------------------

class TestLocalDimmingActionFireForwarding:
    def test_only_set_fields_appear_in_state(self, monkeypatch):
        seen: list[dict] = []

        def fake_apply(state):
            seen.append(state)
            return True

        monkeypatch.setattr(ld_backend, "safe_apply", fake_apply)
        # Only `enabled` is set — the others should be absent from the
        # forwarded state dict so the backend leaves them alone.
        action = LocalDimmingAction(enabled=True)
        assert action.fire() is True
        assert seen == [{"enabled": True}]

    def test_full_state_forwarded(self, monkeypatch):
        seen: list[dict] = []
        monkeypatch.setattr(
            ld_backend, "safe_apply", lambda state: seen.append(state) or True,
        )
        action = LocalDimmingAction(
            enabled=True, quadrant="TL", preset="TCL X11L", bloom_sigma=2.5,
        )
        action.fire()
        assert seen == [{
            "enabled": True,
            "quadrant": "TL",
            "preset": "TCL X11L",
            "bloom_sigma": 2.5,
        }]

    def test_enabled_false_explicitly_forwarded(self, monkeypatch):
        seen: list[dict] = []
        monkeypatch.setattr(
            ld_backend, "safe_apply", lambda state: seen.append(state) or True,
        )
        # `False` means "explicitly turn it off" — must appear in state.
        action = LocalDimmingAction(enabled=False)
        action.fire()
        assert seen == [{"enabled": False}]


# ---------------------------------------------------------------------------
# Macro.fire parallel-thread orchestration
# ---------------------------------------------------------------------------

class TestMacroFire:
    def test_skips_videohub_when_per_macro_disabled(self, monkeypatch):
        # videohub.recall_preset MUST NOT be called when the macro's
        # per-macro Videohub-enable flag is False, even if the action is set.
        seen: list = []
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: seen.append((d, p)) or True,
        )
        m = Macro(
            id="0,0",
            videohub_enabled=False,
            videohub=VideohubAction(device_id="d1", preset_name="p1"),
        )
        results = m.fire()
        assert seen == []
        # `videohub` key absent from results because the action was skipped.
        assert "videohub" not in results

    def test_runs_videohub_when_per_macro_enabled(self, monkeypatch):
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: True,
        )
        m = Macro(
            id="0,0",
            videohub_enabled=True,
            videohub=VideohubAction(device_id="d1", preset_name="p1"),
        )
        results = m.fire()
        assert results == {"videohub": True}

    def test_fires_multiple_backends_in_parallel(self, monkeypatch):
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: True,
        )
        monkeypatch.setattr(
            resolve_backend, "safe_apply_track_state", lambda ts: True,
        )
        monkeypatch.setattr(
            ld_backend, "safe_apply", lambda state: True,
        )
        m = Macro(
            id="0,0",
            videohub_enabled=True,
            videohub=VideohubAction(device_id="d1", preset_name="p1"),
            resolve=ResolveAction(tracks={1: True}),
            local_dimming=LocalDimmingAction(enabled=True),
        )
        results = m.fire()
        assert results == {
            "videohub": True,
            "resolve": True,
            "local_dimming": True,
        }

    def test_one_backend_failing_does_not_block_others(self, monkeypatch):
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: False,
        )
        monkeypatch.setattr(
            resolve_backend, "safe_apply_track_state", lambda ts: True,
        )
        m = Macro(
            id="0,0",
            videohub_enabled=True,
            videohub=VideohubAction(device_id="d1", preset_name="p1"),
            resolve=ResolveAction(tracks={1: True}),
        )
        results = m.fire()
        assert results == {"videohub": False, "resolve": True}

    def test_action_raising_is_caught_and_marked_false(self, monkeypatch):
        # An unexpected exception inside an action's fire() must not bring
        # down the worker thread — the macro reports False for that backend.
        def boom(d, p, timeout=2.0):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(videohub_backend, "recall_preset", boom)
        m = Macro(
            id="0,0",
            videohub_enabled=True,
            videohub=VideohubAction(device_id="d1", preset_name="p1"),
        )
        results = m.fire()
        assert results == {"videohub": False}

    def test_unset_action_skipped_entirely(self, monkeypatch):
        # Action with is_set()==False is skipped — no thread spawned, no
        # entry in results.
        seen: list = []
        monkeypatch.setattr(
            videohub_backend, "recall_preset",
            lambda d, p, timeout=2.0: seen.append("called") or True,
        )
        m = Macro(id="0,0", videohub_enabled=True)  # default empty action
        results = m.fire()
        assert seen == []
        assert results == {}


# ---------------------------------------------------------------------------
# local_dimming._comp_for_current_clip — chain through the resolve backend.
# ---------------------------------------------------------------------------

class _LDFakeProject:
    def __init__(self, timeline):
        self._tl = timeline

    def GetCurrentTimeline(self):  # NOQA: N802
        return self._tl


class _LDFakePM:
    def __init__(self, project):
        self._proj = project

    def GetCurrentProject(self):  # NOQA: N802
        return self._proj


class _LDFakeResolve:
    def __init__(self, pm):
        self._pm = pm

    def GetProjectManager(self):  # NOQA: N802
        return self._pm


class _LDFakeTimeline:
    def __init__(self, item, comp_count=1, comp=None):
        self._item = item
        self._cc = comp_count
        self._comp = comp

    def GetCurrentVideoItem(self):  # NOQA: N802
        return self._item


class _LDFakeItem:
    def __init__(self, comp_count, comp):
        self._cc = comp_count
        self._comp = comp

    def GetFusionCompCount(self):  # NOQA: N802
        return self._cc

    def GetFusionCompByIndex(self, idx):  # NOQA: N802
        return self._comp


class TestCompForCurrentClip:
    def _wire(self, monkeypatch, *, pm=None, project=None, timeline=None,
              item=None, comp_count=1, comp=None, connected=True):
        """Build the chain Resolve → PM → Project → Timeline → Item → Comp."""
        if item is None and comp is not None:
            item = _LDFakeItem(comp_count, comp)
        if timeline is None and item is not None:
            timeline = _LDFakeTimeline(item)
        if project is None and timeline is not None:
            project = _LDFakeProject(timeline)
        if pm is None and project is not None:
            pm = _LDFakePM(project)
        fake_resolve = _LDFakeResolve(pm) if pm is not None else None
        monkeypatch.setattr(resolve_backend, "connect", lambda: connected)
        monkeypatch.setattr(resolve_backend, "_resolve", fake_resolve)

    def test_returns_none_when_connect_fails(self, monkeypatch):
        self._wire(monkeypatch, connected=False)
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_none_when_pm_is_none(self, monkeypatch):
        self._wire(monkeypatch, pm=None, project=None, timeline=None, item=None)
        # Only resolve handle present — no PM.
        monkeypatch.setattr(
            resolve_backend, "_resolve", _LDFakeResolve(None),
        )
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_none_when_project_is_none(self, monkeypatch):
        self._wire(monkeypatch, pm=_LDFakePM(None))
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_none_when_timeline_is_none(self, monkeypatch):
        self._wire(monkeypatch, project=_LDFakeProject(None))
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_none_when_no_video_item(self, monkeypatch):
        self._wire(monkeypatch, timeline=_LDFakeTimeline(item=None))
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_none_when_comp_count_is_zero(self, monkeypatch):
        marker = object()
        item = _LDFakeItem(comp_count=0, comp=marker)
        self._wire(monkeypatch, item=item)
        # Even though `comp` is set, the count guard returns None.
        assert ld_backend._comp_for_current_clip() is None

    def test_returns_comp_when_count_positive(self, monkeypatch):
        marker = object()
        item = _LDFakeItem(comp_count=1, comp=marker)
        self._wire(monkeypatch, item=item)
        assert ld_backend._comp_for_current_clip() is marker

    def test_returns_none_on_chain_exception(self, monkeypatch, capsys):
        class Broken:
            def GetCurrentProject(self):  # NOQA: N802
                raise RuntimeError("PM blew up")
        monkeypatch.setattr(resolve_backend, "connect", lambda: True)
        monkeypatch.setattr(
            resolve_backend, "_resolve",
            type("R", (), {"GetProjectManager": lambda self: Broken()})(),
        )
        assert ld_backend._comp_for_current_clip() is None
        assert "resolve traversal failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# local_dimming.safe_apply forwards through resolve._run_off_main.
# ---------------------------------------------------------------------------

class TestSafeApplyForwarding:
    def test_returns_true_when_apply_returns_true(self, monkeypatch):
        # Stub _run_off_main so we don't actually spin up a worker.
        monkeypatch.setattr(
            resolve_backend, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: fn(),
        )
        # _apply needs a comp; fake the chain to return None so apply
        # returns False, then test the True case via direct stub.
        monkeypatch.setattr(ld_backend, "_apply", lambda state: True)
        assert ld_backend.safe_apply({"enabled": True}) is True

    def test_default_false_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            resolve_backend, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert ld_backend.safe_apply({"enabled": True}) is False
