"""Tests for the pure-logic surface of the Resolve backend.

The DaVinci Resolve scripting API is not available in CI, so we don't
exercise live-Resolve paths. What's testable in pure Python:

- _infer_quadrant: pan/tilt sign mapping (the bit that's most likely to
  regress when someone "fixes" a Tilt convention).
- apply_track_state / apply_video_track_transforms early-return paths.
- _run_off_main: worker-thread join, timeout, and exception handling.
- safe_* wrappers' default fallbacks when the underlying function fails.

Live functions are exercised by faking the timeline / clip / property
surfaces just enough to satisfy the call shape.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import time

import pytest

from macroflow.backends import resolve


# ---------------------------------------------------------------------------
# _infer_quadrant: the Tilt convention is positive = UP, so top row has
# positive Tilt. This is the source of truth tested by the live project.
# ---------------------------------------------------------------------------

class TestInferQuadrant:
    def test_top_left_negative_pan_positive_tilt(self):
        assert resolve._infer_quadrant(-1920, 1080) == "Q1"

    def test_top_right_positive_pan_positive_tilt(self):
        assert resolve._infer_quadrant(1920, 1080) == "Q2"

    def test_bottom_left_negative_pan_negative_tilt(self):
        assert resolve._infer_quadrant(-1920, -1080) == "Q3"

    def test_bottom_right_positive_pan_negative_tilt(self):
        assert resolve._infer_quadrant(1920, -1080) == "Q4"

    @pytest.mark.parametrize(
        ("pan", "tilt", "quadrant"),
        [
            (0.0, 0.0, "Q2"),       # exact center → Q2 (pan>=0, tilt>=0)
            (0.0, 1.0, "Q2"),       # right of axis
            (-0.001, 0.0, "Q1"),    # just left of axis
            (-0.001, -0.001, "Q3"), # just left + below axis
        ],
    )
    def test_axis_boundaries(self, pan: float, tilt: float, quadrant: str):
        assert resolve._infer_quadrant(pan, tilt) == quadrant


# ---------------------------------------------------------------------------
# apply_track_state early-return / no-timeline / no-op paths
# ---------------------------------------------------------------------------

class TestApplyTrackStateEarlyReturns:
    def test_empty_state_returns_true(self, monkeypatch):
        # No tracks to flip → success without touching Resolve.
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.apply_track_state({}) is True

    def test_no_timeline_returns_false(self, monkeypatch):
        # State to apply but no timeline → fail.
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.apply_track_state({1: True}) is False
        assert resolve.LAST_APPLY["failed"] == [1]

    def test_no_op_when_track_already_in_target_state(self, monkeypatch):
        class FakeTL:
            def __init__(self):
                self.set_calls = 0

            def GetIsTrackEnabled(self, kind, idx):  # NOQA: N802
                return True

            def SetTrackEnable(self, kind, idx, enabled):  # NOQA: N802
                self.set_calls += 1
                return True

        tl = FakeTL()
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        result = resolve.apply_track_state({1: True})
        assert result is True
        assert tl.set_calls == 0  # no flip — already at desired state
        assert resolve.LAST_APPLY["unchanged"] == [1]

    def test_flip_succeeds_when_state_differs(self, monkeypatch):
        class FakeTL:
            def GetIsTrackEnabled(self, kind, idx):  # NOQA: N802
                return False

            def SetTrackEnable(self, kind, idx, enabled):  # NOQA: N802
                return True

        monkeypatch.setattr(resolve, "_current_timeline", lambda: FakeTL())
        result = resolve.apply_track_state({1: True, 2: True})
        assert result is True
        flipped_ids = sorted(i for i, _ in resolve.LAST_APPLY["flipped"])
        assert flipped_ids == [1, 2]

    def test_one_failed_set_marks_overall_failure(self, monkeypatch):
        class FakeTL:
            def GetIsTrackEnabled(self, kind, idx):  # NOQA: N802
                return False

            def SetTrackEnable(self, kind, idx, enabled):  # NOQA: N802
                return idx != 2  # track 2 fails

        monkeypatch.setattr(resolve, "_current_timeline", lambda: FakeTL())
        result = resolve.apply_track_state({1: True, 2: True})
        assert result is False
        assert resolve.LAST_APPLY["failed"] == [2]


# ---------------------------------------------------------------------------
# apply_video_track_transforms early-return / no-op paths
# ---------------------------------------------------------------------------

class TestApplyVideoTrackTransformsEarlyReturns:
    def test_empty_transforms_returns_true(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.apply_video_track_transforms({}) is True

    def test_no_timeline_returns_false(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.apply_video_track_transforms({1: {"zoom_x": 0.5}}) is False


# ---------------------------------------------------------------------------
# _run_off_main: worker-thread plumbing
# ---------------------------------------------------------------------------

class TestRunOffMain:
    def test_returns_callable_result(self):
        def add():
            return 42
        assert resolve._run_off_main(add) == 42

    def test_returns_default_when_callable_raises(self):
        def boom():
            raise RuntimeError("nope")
        # Default propagates back; the worker swallows the exception
        # and prints to stderr instead of taking down the main thread.
        assert resolve._run_off_main(boom, default="fallback") == "fallback"

    def test_returns_default_on_timeout(self):
        def slow():
            time.sleep(0.5)
            return "done"
        # 50ms timeout — the worker is still running, so we get the default.
        assert resolve._run_off_main(slow, timeout=0.05, default="t/o") == "t/o"

    def test_partial_callable_resolves_name_via_func_attr(self):
        # _run_off_main reaches into fn.__name__ for logging; partials don't
        # have one. The implementation falls back to fn.func.__name__.
        from functools import partial

        def named_target(x):
            return x * 2
        result = resolve._run_off_main(partial(named_target, 21))
        assert result == 42


# ---------------------------------------------------------------------------
# safe_* wrappers' default-on-failure contract
# ---------------------------------------------------------------------------

class TestSafeWrappersDefaults:
    def test_safe_get_video_track_info_default_is_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert resolve.safe_get_video_track_info() == []

    def test_safe_get_video_track_transforms_default_is_empty_dict(
        self, monkeypatch,
    ):
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert resolve.safe_get_video_track_transforms() == {}

    def test_safe_get_timeline_resolution_default_is_1920x1080(
        self, monkeypatch,
    ):
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert resolve.safe_get_timeline_resolution() == (1920, 1080)

    def test_safe_get_current_timecode_default_is_none(self, monkeypatch):
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None: default,
        )
        assert resolve.safe_get_current_timecode() is None

    def test_safe_apply_track_state_returns_false_on_failure(
        self, monkeypatch,
    ):
        # Simulate the worker returning the default (False) on timeout.
        # The signature has to accept coalesce_key now that the safe_*
        # wrappers pass one for queue-coalescing back-pressure.
        monkeypatch.setattr(
            resolve, "_run_off_main",
            lambda fn, *, timeout=5.0, default=None, coalesce_key=None: default,
        )
        assert resolve.safe_apply_track_state({1: True}) is False
