"""Tests for the Resolve backend's connect / is_alive / _current_timeline
plumbing — the layer that wraps DaVinciResolveScript.

DaVinciResolveScript isn't on the CI Python path, so we plant a fake
module in `sys.modules` and exercise the connect chain end-to-end.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import sys
import types

import pytest

from macroflow.backends import resolve


# ---------------------------------------------------------------------------
# Fake DaVinciResolveScript chain: dvr.scriptapp() → resolve →
# GetProjectManager → GetCurrentProject → GetCurrentTimeline.
# ---------------------------------------------------------------------------

class FakeProjectManager:
    def __init__(self, project=None):
        self._project = project

    def GetCurrentProject(self):  # NOQA: N802
        return self._project


class FakeProject:
    def __init__(self, timeline=None):
        self._timeline = timeline

    def GetCurrentTimeline(self):  # NOQA: N802
        return self._timeline


class FakeResolve:
    def __init__(self, project_manager=None, raise_on_pm=False):
        self._pm = project_manager
        self._raise_on_pm = raise_on_pm

    def GetProjectManager(self):  # NOQA: N802
        if self._raise_on_pm:
            raise RuntimeError("Resolve handle is stale")
        return self._pm


def _install_fake_dvr(monkeypatch, fake_resolve_obj):
    """Plant a DaVinciResolveScript module that returns the given handle."""
    fake_dvr = types.ModuleType("DaVinciResolveScript")
    fake_dvr.scriptapp = lambda name: fake_resolve_obj  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", fake_dvr)
    return fake_dvr


@pytest.fixture(autouse=True)
def reset_resolve_state(monkeypatch):
    """Each test starts with the connect() handle cleared so the test
    drives the full chain. We restore on teardown."""
    prev = resolve._resolve
    monkeypatch.setattr(resolve, "_resolve", None)
    yield
    resolve._resolve = prev


# ---------------------------------------------------------------------------
# _load_dvr_script
# ---------------------------------------------------------------------------

class TestLoadDvrScript:
    def test_returns_already_imported_module_if_present(self, monkeypatch):
        fake = types.ModuleType("DaVinciResolveScript")
        fake.tag = "preloaded"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "DaVinciResolveScript", fake)
        result = resolve._load_dvr_script()
        assert result is fake

    def test_returns_none_when_module_missing(self, monkeypatch, capsys):
        # Force the module out of sys.modules so the import path runs.
        monkeypatch.delitem(sys.modules, "DaVinciResolveScript", raising=False)
        # Block import by leaving _MODULE_DIRS empty + RESOLVE_SCRIPT_API unset.
        monkeypatch.setattr(resolve, "_MODULE_DIRS", [])
        monkeypatch.delenv("RESOLVE_SCRIPT_API", raising=False)
        assert resolve._load_dvr_script() is None
        out = capsys.readouterr().out
        assert "DaVinciResolveScript import failed" in out

    def test_uses_resolve_script_api_env_var_when_dir_exists(
        self, monkeypatch, tmp_path,
    ):
        # Create a Modules dir under RESOLVE_SCRIPT_API; the loader should
        # add it to sys.path even if the import still fails.
        api_dir = tmp_path / "Resolve"
        modules_dir = api_dir / "Modules"
        modules_dir.mkdir(parents=True)
        monkeypatch.delitem(sys.modules, "DaVinciResolveScript", raising=False)
        monkeypatch.setenv("RESOLVE_SCRIPT_API", str(api_dir))
        monkeypatch.setattr(resolve, "_MODULE_DIRS", [])
        # Snapshot sys.path; module load fails (nothing in modules_dir), but
        # the path-prepend should have happened.
        before = list(sys.path)
        resolve._load_dvr_script()
        assert str(modules_dir) in sys.path
        # Don't leak the path between tests.
        sys.path[:] = before


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------

class TestConnect:
    def test_returns_true_when_resolve_handle_already_cached(self, monkeypatch):
        # Pre-set the cached handle; connect should short-circuit.
        monkeypatch.setattr(resolve, "_resolve", FakeResolve())
        assert resolve.connect() is True

    def test_returns_false_when_module_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(resolve, "_load_dvr_script", lambda: None)
        assert resolve.connect() is False
        assert "module not found" in capsys.readouterr().out

    def test_returns_false_when_scriptapp_raises(self, monkeypatch, capsys):
        class Boom:
            def scriptapp(self, name):
                raise RuntimeError("scriptapp blew up")
        monkeypatch.setattr(resolve, "_load_dvr_script", lambda: Boom())
        assert resolve.connect() is False
        assert "scriptapp() failed" in capsys.readouterr().out

    def test_returns_false_when_scriptapp_returns_none(self, monkeypatch, capsys):
        class GivesNone:
            def scriptapp(self, name):
                return None
        monkeypatch.setattr(resolve, "_load_dvr_script", lambda: GivesNone())
        assert resolve.connect() is False
        assert "Could not connect" in capsys.readouterr().out

    def test_caches_resolve_handle_on_success(self, monkeypatch):
        fake = FakeResolve()
        _install_fake_dvr(monkeypatch, fake)
        assert resolve.connect() is True
        # Second call uses the cached handle without re-running scriptapp.
        # We can confirm by reading the module-level singleton.
        assert resolve._resolve is fake


# ---------------------------------------------------------------------------
# is_alive: must round-trip a real call so a stale handle is caught.
# ---------------------------------------------------------------------------

class TestIsAlive:
    def test_false_when_connect_fails(self, monkeypatch):
        monkeypatch.setattr(resolve, "connect", lambda: False)
        assert resolve.is_alive() is False

    def test_true_when_get_project_manager_returns_truthy(self, monkeypatch):
        fake = FakeResolve(project_manager=FakeProjectManager())
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve.is_alive() is True

    def test_false_and_drops_handle_when_pm_returns_none(self, monkeypatch):
        # GetProjectManager returns None — Resolve is "running" but no
        # project state. is_alive should report False and not crash.
        fake = FakeResolve(project_manager=None)
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve.is_alive() is False

    def test_false_and_drops_handle_when_pm_raises(self, monkeypatch):
        # GetProjectManager raising is the "stale handle" signal — must
        # clear _resolve so the next connect retries.
        fake = FakeResolve(raise_on_pm=True)
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve.is_alive() is False
        assert resolve._resolve is None


# ---------------------------------------------------------------------------
# safe_is_alive
# ---------------------------------------------------------------------------

class TestSafeIsAlive:
    def test_default_is_false_on_timeout_or_failure(self, monkeypatch):
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert resolve.safe_is_alive() is False


# ---------------------------------------------------------------------------
# _current_timeline: connect → pm → project → timeline chain.
# ---------------------------------------------------------------------------

class TestCurrentTimeline:
    def test_returns_none_when_connect_fails(self, monkeypatch):
        monkeypatch.setattr(resolve, "connect", lambda: False)
        assert resolve._current_timeline() is None

    def test_returns_none_when_pm_is_none(self, monkeypatch, capsys):
        fake = FakeResolve(project_manager=None)
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve._current_timeline() is None
        assert "GetProjectManager returned None" in capsys.readouterr().out

    def test_returns_none_when_no_current_project(self, monkeypatch, capsys):
        fake = FakeResolve(project_manager=FakeProjectManager(project=None))
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve._current_timeline() is None
        assert "No current project" in capsys.readouterr().out

    def test_returns_none_when_project_has_no_timeline(
        self, monkeypatch, capsys,
    ):
        proj = FakeProject(timeline=None)
        fake = FakeResolve(project_manager=FakeProjectManager(project=proj))
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve._current_timeline() is None
        assert "no current timeline" in capsys.readouterr().out

    def test_returns_timeline_on_full_success(self, monkeypatch):
        marker = object()
        proj = FakeProject(timeline=marker)
        fake = FakeResolve(project_manager=FakeProjectManager(project=proj))
        monkeypatch.setattr(resolve, "_resolve", fake)
        monkeypatch.setattr(resolve, "connect", lambda: True)
        assert resolve._current_timeline() is marker


# ---------------------------------------------------------------------------
# get_video_track_count: thin wrapper but worth pinning.
# ---------------------------------------------------------------------------

class TestGetVideoTrackCount:
    def test_zero_when_no_timeline(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.get_video_track_count() == 0

    def test_returns_int_from_timeline(self, monkeypatch):
        class FakeTL:
            def GetTrackCount(self, kind):  # NOQA: N802
                return 7
        monkeypatch.setattr(resolve, "_current_timeline", lambda: FakeTL())
        assert resolve.get_video_track_count() == 7

    def test_zero_when_get_track_count_raises(self, monkeypatch, capsys):
        class FakeTL:
            def GetTrackCount(self, kind):  # NOQA: N802
                raise RuntimeError("blew up")
        monkeypatch.setattr(resolve, "_current_timeline", lambda: FakeTL())
        assert resolve.get_video_track_count() == 0
        assert "GetTrackCount failed" in capsys.readouterr().out
