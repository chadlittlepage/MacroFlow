"""Tests for the Resolve backend's timeline read/write paths.

We don't have a live Resolve in CI, so we feed the functions a fake
timeline and a fake clip with just the methods they call. This pins
the property-name map, default fallback, and per-track behavior
without ever touching DaVinciResolveScript.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import pytest

from macroflow.backends import resolve


# ---------------------------------------------------------------------------
# Fakes that mimic the slice of the Resolve API we actually call.
# ---------------------------------------------------------------------------

class FakeClip:
    """Stand-in for a Resolve TimelineItem.

    `props` maps Resolve property names → values. Callers that test the
    `_f(prop, default)` fallback can also pass `raise_on=("Pan",)` to
    force GetProperty to raise for specific properties.
    """

    def __init__(
        self,
        props: dict | None = None,
        raise_on: tuple[str, ...] = (),
        start: int = 0,
        end: int = 1_000_000,
    ) -> None:
        self.props = props or {}
        self.raise_on = set(raise_on)
        # Captures every SetProperty call so tests can assert on it.
        self.set_calls: list[tuple[str, object]] = []
        # Whether SetProperty should fail; setting it to a property name
        # makes only that property fail.
        self.fail_set: set[str] = set()
        # GetStart / GetEnd cover an implausibly wide default range so the
        # playhead-aware lookup picks any FakeClip by default. Tests that
        # care about playhead targeting set explicit start / end.
        self._start = start
        self._end = end

    def GetProperty(self, prop: str):  # NOQA: N802
        if prop in self.raise_on:
            raise RuntimeError(f"GetProperty({prop}) blew up")
        return self.props.get(prop)

    def SetProperty(self, prop: str, value):  # NOQA: N802
        if prop in self.fail_set:
            raise RuntimeError(f"SetProperty({prop}) blew up")
        self.set_calls.append((prop, value))
        return True

    def GetStart(self) -> int:  # NOQA: N802
        return self._start

    def GetEnd(self) -> int:  # NOQA: N802
        return self._end


class FakeTimeline:
    """Stand-in for a Resolve Timeline.

    `tracks` is a 1-keyed dict of track_index → list[FakeClip].
    `track_names` is 1-keyed dict for GetTrackName.
    `enabled_state` is 1-keyed dict for GetIsTrackEnabled / SetTrackEnable.
    """

    def __init__(
        self,
        tracks: dict[int, list[FakeClip]] | None = None,
        track_names: dict[int, str] | None = None,
        enabled_state: dict[int, bool] | None = None,
        timeline_resolution: tuple[int, int] | None = (1920, 1080),
        timecode: str | None = "01:00:01:00",
        framerate: float | None = 24.0,
    ) -> None:
        self.tracks = tracks or {}
        self.track_names = track_names or {}
        self.enabled_state = dict(enabled_state) if enabled_state else {}
        self._resolution = timeline_resolution
        # Default timecode 01:00:01:00 falls inside FakeClip's default
        # [0, 1_000_000] frame range, so the playhead-aware clip lookup
        # picks the first clip on each track without test ceremony.
        self._timecode = timecode
        self._framerate = framerate
        # Capture for assertions
        self.set_track_calls: list[tuple[int, bool]] = []
        # Knobs for forcing failures on specific tracks
        self.fail_set_track: set[int] = set()
        self.raise_on_get_track_count = False
        self.raise_on_set_track: set[int] = set()
        self.raise_on_get_enabled: set[int] = set()
        self.raise_on_get_items: set[int] = set()
        self.raise_on_get_track_name: set[int] = set()

    def GetTrackCount(self, kind: str) -> int:  # NOQA: N802
        if self.raise_on_get_track_count:
            raise RuntimeError("GetTrackCount blew up")
        if kind != "video":
            return 0
        return max(self.tracks.keys(), default=0)

    def GetTrackName(self, kind: str, idx: int):  # NOQA: N802
        if idx in self.raise_on_get_track_name:
            raise RuntimeError("GetTrackName blew up")
        return self.track_names.get(idx)

    def GetItemListInTrack(self, kind: str, idx: int):  # NOQA: N802
        if idx in self.raise_on_get_items:
            raise RuntimeError("GetItemListInTrack blew up")
        return self.tracks.get(idx, [])

    def GetIsTrackEnabled(self, kind: str, idx: int) -> bool:  # NOQA: N802
        if idx in self.raise_on_get_enabled:
            raise RuntimeError("GetIsTrackEnabled blew up")
        return self.enabled_state.get(idx, True)

    def SetTrackEnable(self, kind: str, idx: int, enabled: bool) -> bool:  # NOQA: N802
        if idx in self.raise_on_set_track:
            raise RuntimeError("SetTrackEnable blew up")
        self.set_track_calls.append((idx, enabled))
        if idx in self.fail_set_track:
            return False
        self.enabled_state[idx] = enabled
        return True

    def GetSetting(self, key: str):  # NOQA: N802
        if key == "timelineFrameRate":
            return str(self._framerate) if self._framerate else None
        if self._resolution is None:
            return None
        if key == "timelineResolutionWidth":
            return str(self._resolution[0])
        if key == "timelineResolutionHeight":
            return str(self._resolution[1])
        return None

    def GetCurrentTimecode(self) -> str | None:  # NOQA: N802
        return self._timecode


# ---------------------------------------------------------------------------
# get_video_track_info
# ---------------------------------------------------------------------------

class TestGetVideoTrackInfo:
    def test_no_timeline_returns_empty(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.get_video_track_info() == []

    def test_lists_each_track_with_default_naming_when_none(self, monkeypatch):
        tl = FakeTimeline(
            tracks={1: [], 2: []},
            track_names={},  # both fall back to V1 / V2
            enabled_state={1: True, 2: False},
        )
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        info = resolve.get_video_track_info()
        assert info == [
            {"index": 1, "name": "V1", "enabled": True},
            {"index": 2, "name": "V2", "enabled": False},
        ]

    def test_preserves_track_names_when_supplied(self, monkeypatch):
        tl = FakeTimeline(
            tracks={1: [], 2: []},
            track_names={1: "Bug", 2: "Lower-3rd"},
        )
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        info = resolve.get_video_track_info()
        assert info[0]["name"] == "Bug"
        assert info[1]["name"] == "Lower-3rd"

    def test_get_track_count_exception_returns_empty(self, monkeypatch):
        tl = FakeTimeline(tracks={1: []})
        tl.raise_on_get_track_count = True
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.get_video_track_info() == []

    def test_track_name_exception_falls_back_to_v_prefix(self, monkeypatch):
        tl = FakeTimeline(tracks={1: []}, track_names={1: "Bug"})
        tl.raise_on_get_track_name = {1}
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        info = resolve.get_video_track_info()
        assert info[0]["name"] == "V1"

    def test_get_enabled_exception_defaults_to_true(self, monkeypatch):
        tl = FakeTimeline(tracks={1: []}, enabled_state={1: False})
        tl.raise_on_get_enabled = {1}
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        info = resolve.get_video_track_info()
        # When the API call raises we conservatively report True
        # (saves the user from a "looks disabled" false alarm).
        assert info[0]["enabled"] is True


# ---------------------------------------------------------------------------
# set_video_track_enabled
# ---------------------------------------------------------------------------

class TestSetVideoTrackEnabled:
    def test_no_timeline_returns_false(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.set_video_track_enabled(1, True) is False

    def test_calls_through_to_timeline(self, monkeypatch):
        tl = FakeTimeline(tracks={1: []})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.set_video_track_enabled(1, False) is True
        assert tl.set_track_calls == [(1, False)]

    def test_raises_returns_false_with_log(self, monkeypatch, capsys):
        tl = FakeTimeline(tracks={1: []})
        tl.raise_on_set_track = {1}
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.set_video_track_enabled(1, True) is False
        out = capsys.readouterr().out
        assert "SetTrackEnable" in out


# ---------------------------------------------------------------------------
# get_video_track_transforms — full read path with fake clips
# ---------------------------------------------------------------------------

class TestGetVideoTrackTransforms:
    def test_no_timeline_returns_empty(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.get_video_track_transforms() == {}

    def test_returns_empty_when_track_count_raises(self, monkeypatch):
        tl = FakeTimeline(tracks={1: [FakeClip()]})
        tl.raise_on_get_track_count = True
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.get_video_track_transforms() == {}

    def test_track_with_no_clips_is_omitted(self, monkeypatch):
        tl = FakeTimeline(tracks={1: [FakeClip()], 2: []})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        result = resolve.get_video_track_transforms()
        assert 1 in result
        assert 2 not in result

    def test_get_items_exception_skips_track(self, monkeypatch):
        tl = FakeTimeline(tracks={1: [FakeClip()], 2: [FakeClip()]})
        tl.raise_on_get_items = {1}
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        result = resolve.get_video_track_transforms()
        assert 1 not in result
        assert 2 in result

    def test_full_property_map_is_read(self, monkeypatch):
        clip = FakeClip(props={
            "Pan": 1920.0, "Tilt": 1080.0,
            "ZoomX": 0.5, "ZoomY": 0.6,
            "RotationAngle": 12.5,
            "AnchorPointX": 100.0, "AnchorPointY": 200.0,
            "Pitch": 5.0, "Yaw": -3.0,
            "FlipX": 1.0, "FlipY": 0.0,
        })
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        result = resolve.get_video_track_transforms()
        x = result[1]
        # Quadrant inferred from sign(Pan, Tilt) — Q2 here.
        assert x["quadrant"] == "Q2"
        assert x["zoom_x"] == 0.5
        assert x["zoom_y"] == 0.6
        assert x["position_x"] == 1920.0
        assert x["position_y"] == 1080.0
        assert x["rotation_angle"] == 12.5
        assert x["anchor_point_x"] == 100.0
        assert x["anchor_point_y"] == 200.0
        assert x["pitch"] == 5.0
        assert x["yaw"] == -3.0
        assert x["flip_h"] is True
        assert x["flip_v"] is False

    def test_missing_property_falls_back_to_default(self, monkeypatch):
        # Empty props dict: every read should use the default.
        clip = FakeClip(props={})
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        x = resolve.get_video_track_transforms()[1]
        assert x["zoom_x"] == 1.0       # default 1.0
        assert x["zoom_y"] == 1.0
        assert x["position_x"] == 0.0
        assert x["rotation_angle"] == 0.0
        assert x["pitch"] == 0.0
        assert x["flip_h"] is False
        # Q2 — center of axis with our convention (pan>=0, tilt>=0).
        assert x["quadrant"] == "Q2"

    def test_property_raise_falls_back_to_default(self, monkeypatch):
        clip = FakeClip(props={"Pan": 1.0}, raise_on=("ZoomX",))
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        x = resolve.get_video_track_transforms()[1]
        # ZoomX raises → default 1.0; Pan is still read.
        assert x["zoom_x"] == 1.0
        assert x["position_x"] == 1.0

    def test_non_numeric_property_falls_back_to_default(self, monkeypatch):
        # If the property comes back as a non-numeric string, _f swallows
        # the ValueError and uses the default.
        clip = FakeClip(props={"ZoomX": "lol", "Tilt": -540.0})
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        x = resolve.get_video_track_transforms()[1]
        assert x["zoom_x"] == 1.0
        # Negative tilt → bottom row, pan=0 → right side → Q4.
        assert x["quadrant"] == "Q4"


# ---------------------------------------------------------------------------
# apply_video_track_transforms — full write path
# ---------------------------------------------------------------------------

class TestApplyVideoTrackTransforms:
    def test_writes_property_map_correctly(self, monkeypatch):
        # Clip starts with all properties unset (GetProperty returns None);
        # every non-zero target should produce a write. Targets that match
        # the implicit "0" default (FlipY=False, AnchorPointX=0.0, etc.)
        # are skipped by the read-then-write guard.
        clip = FakeClip()
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)

        ok = resolve.apply_video_track_transforms({
            1: {
                "zoom_x": 0.5, "zoom_y": 0.5,
                "position_x": -1920.0, "position_y": 1080.0,
                "rotation_angle": 90.0,
                "anchor_point_x": 0.0, "anchor_point_y": 0.0,
                "pitch": 0.0, "yaw": 0.0,
                "flip_h": True, "flip_v": False,
            },
        })
        assert ok is True
        seen = dict(clip.set_calls)
        # Float properties: writes happen when cur_raw is None (no current
        # value), so all numeric targets land — even the 0.0s.
        assert seen["ZoomX"] == 0.5
        assert seen["ZoomY"] == 0.5
        assert seen["Pan"] == -1920.0
        assert seen["Tilt"] == 1080.0
        assert seen["RotationAngle"] == 90.0
        assert seen["AnchorPointX"] == 0.0
        assert seen["AnchorPointY"] == 0.0
        assert seen["Pitch"] == 0.0
        assert seen["Yaw"] == 0.0
        # flip_* uses bool-coercion: 1 if cur_raw else 0 == 0 when cur_raw
        # is None. Target FlipX=True → 1 differs from 0 → write happens.
        assert seen["FlipX"] == 1
        # Target FlipY=False → 0, current resolves to 0 → no-op skip.
        assert "FlipY" not in seen

    def test_no_op_writes_are_skipped(self, monkeypatch):
        # Clip already at the target values; apply should be a complete
        # no-op. This is the macOS 15 + Resolve crash mitigation: don't
        # tickle the transform-layer init when nothing actually changes.
        clip = FakeClip(props={
            "ZoomX": 0.5, "ZoomY": 0.5,
            "Pan": -1920.0, "Tilt": 1080.0,
            "RotationAngle": 90.0,
            "AnchorPointX": 0.0, "AnchorPointY": 0.0,
            "Pitch": 0.0, "Yaw": 0.0,
            "FlipX": 1.0, "FlipY": 0.0,
        })
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        ok = resolve.apply_video_track_transforms({
            1: {
                "zoom_x": 0.5, "zoom_y": 0.5,
                "position_x": -1920.0, "position_y": 1080.0,
                "rotation_angle": 90.0,
                "anchor_point_x": 0.0, "anchor_point_y": 0.0,
                "pitch": 0.0, "yaw": 0.0,
                "flip_h": True, "flip_v": False,
            },
        })
        assert ok is True
        assert clip.set_calls == [], (
            "no SetProperty calls should fire when current values match"
        )

    def test_only_changed_values_are_written(self, monkeypatch):
        # Quadrant change: only Pan + Tilt should be written when the rest
        # of the transform already matches. This is the typical macOS 15
        # repro — quadrant flip from Q1 → Q2 used to issue 11 writes.
        clip = FakeClip(props={
            "ZoomX": 1.0, "ZoomY": 1.0,
            "Pan": -1920.0, "Tilt": 1080.0,   # currently Q1
            "RotationAngle": 0.0,
            "AnchorPointX": 0.0, "AnchorPointY": 0.0,
            "Pitch": 0.0, "Yaw": 0.0,
            "FlipX": 0.0, "FlipY": 0.0,
        })
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        # Push the full xform dict but with new Pan/Tilt for Q2.
        resolve.apply_video_track_transforms({
            1: {
                "zoom_x": 1.0, "zoom_y": 1.0,
                "position_x": 1920.0, "position_y": 1080.0,  # Q2
                "rotation_angle": 0.0,
                "anchor_point_x": 0.0, "anchor_point_y": 0.0,
                "pitch": 0.0, "yaw": 0.0,
                "flip_h": False, "flip_v": False,
            },
        })
        keys_written = {k for k, _ in clip.set_calls}
        # Q1 → Q2 keeps Tilt the same (top row stays top row), only Pan
        # flips sign. So only Pan should be written.
        assert keys_written == {"Pan"}

    def test_float_epsilon_skips_near_identical_values(self, monkeypatch):
        # Tiny float drift (e.g. JSON serialization roundtrip) should NOT
        # trigger a redundant write — that's what the EPSILON guard is for.
        clip = FakeClip(props={"Pan": 1920.0})
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        resolve.apply_video_track_transforms({
            1: {"position_x": 1920.0 + 1e-9},  # under EPSILON (1e-6)
        })
        assert clip.set_calls == []

    def test_writes_target_clip_at_playhead_not_leftmost(self, monkeypatch):
        # Three clips on V1, playhead inside the middle one. Only the
        # middle clip should receive the SetProperty calls.
        # Frame math: timecode "01:00:05:00" at 24fps = 86400 + 120 = 86520.
        leftmost = FakeClip(start=86400, end=86460)        # 01:00:00 - 01:00:02:12
        middle = FakeClip(start=86460, end=86600)          # 01:00:02:12 - 01:00:08:08
        rightmost = FakeClip(start=86600, end=86700)       # 01:00:08:08 - 01:00:12:12
        tl = FakeTimeline(
            tracks={1: [leftmost, middle, rightmost]},
            timecode="01:00:05:00",
            framerate=24.0,
        )
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        resolve.apply_video_track_transforms({1: {"position_x": 1920.0}})
        assert leftmost.set_calls == []
        assert rightmost.set_calls == []
        assert middle.set_calls == [("Pan", 1920.0)]

    def test_skips_track_with_no_clip_at_playhead(self, monkeypatch):
        # Playhead is at 01:00:05:00 (frame 86520) but the only clip on V1
        # ends at frame 86420. Macro should silently skip the track —
        # NOT corrupt the leftmost clip.
        clip = FakeClip(start=86400, end=86420)
        tl = FakeTimeline(
            tracks={1: [clip]},
            timecode="01:00:05:00",
            framerate=24.0,
        )
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        ok = resolve.apply_video_track_transforms({1: {"position_x": 1920.0}})
        assert ok is True
        assert clip.set_calls == []

    def test_skips_track_with_no_clips(self, monkeypatch):
        tl = FakeTimeline(tracks={1: [], 2: [FakeClip()]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        # Track 1 has no clips; we must not raise, and track 2 still writes.
        ok = resolve.apply_video_track_transforms({
            1: {"zoom_x": 0.5},
            2: {"zoom_x": 0.7},
        })
        assert ok is True
        track2_clip = tl.tracks[2][0]
        assert ("ZoomX", 0.7) in track2_clip.set_calls

    def test_get_items_exception_treats_as_no_clips(self, monkeypatch):
        tl = FakeTimeline(tracks={1: [FakeClip()]})
        tl.raise_on_get_items = {1}
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        # GetItemListInTrack raises → treated as empty → no SetProperty calls.
        ok = resolve.apply_video_track_transforms({1: {"zoom_x": 0.5}})
        assert ok is True
        assert tl.tracks[1][0].set_calls == []

    def test_none_value_in_xform_is_skipped(self, monkeypatch):
        clip = FakeClip()
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        # `None` means "leave alone" — must not be written.
        resolve.apply_video_track_transforms({1: {"zoom_x": 0.5, "zoom_y": None}})
        seen = dict(clip.set_calls)
        assert "ZoomX" in seen
        assert "ZoomY" not in seen

    def test_non_dict_xform_is_skipped(self, monkeypatch):
        clip = FakeClip()
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        # Hand-edited config could pass garbage; we must not crash.
        ok = resolve.apply_video_track_transforms({1: "not a dict"})  # type: ignore[arg-type]
        assert ok is True
        assert clip.set_calls == []

    def test_setproperty_failure_marks_overall_false(self, monkeypatch):
        clip = FakeClip()
        clip.fail_set = {"Pan"}  # Pan write blows up
        tl = FakeTimeline(tracks={1: [clip]})
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        ok = resolve.apply_video_track_transforms({
            1: {"position_x": -1920.0, "zoom_x": 0.5},
        })
        assert ok is False
        # The non-failing write still went through.
        assert ("ZoomX", 0.5) in clip.set_calls


# ---------------------------------------------------------------------------
# get_timeline_resolution
# ---------------------------------------------------------------------------

class TestGetTimelineResolution:
    def test_returns_settings_when_present(self, monkeypatch):
        tl = FakeTimeline(timeline_resolution=(3840, 2160))
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.get_timeline_resolution() == (3840, 2160)

    def test_falls_back_to_1920x1080_when_no_timeline(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.get_timeline_resolution() == (1920, 1080)

    def test_falls_back_when_settings_return_none(self, monkeypatch):
        # `timeline_resolution=None` causes GetSetting to return None →
        # the int(None or 1920) fallback kicks in.
        tl = FakeTimeline(timeline_resolution=None)
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.get_timeline_resolution() == (1920, 1080)


# ---------------------------------------------------------------------------
# get_current_timecode
# ---------------------------------------------------------------------------

class TestGetCurrentTimecode:
    def test_no_timeline_returns_none(self, monkeypatch):
        monkeypatch.setattr(resolve, "_current_timeline", lambda: None)
        assert resolve.get_current_timecode() is None

    def test_returns_timeline_value(self, monkeypatch):
        tl = FakeTimeline(timecode="01:00:00:00")
        monkeypatch.setattr(resolve, "_current_timeline", lambda: tl)
        assert resolve.get_current_timecode() == "01:00:00:00"

    def test_exception_returns_none(self, monkeypatch):
        class Broken:
            def GetCurrentTimecode(self):  # NOQA: N802
                raise RuntimeError("nope")
        monkeypatch.setattr(resolve, "_current_timeline", lambda: Broken())
        assert resolve.get_current_timecode() is None
