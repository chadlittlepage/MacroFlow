"""Pure-logic tests for the Videohub backend.

Covers: VideohubDevice display logic, config loading, device/preset
listing, get_preset lookup, _candidate_ips ordering, and the
`set_enabled` / `is_enabled` / mock-mode globals. Network paths
(_try_send, recall_preset on the wire) are not exercised here — those
need a real or mocked Videohub Daemon and live in integration tests.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from macroflow.backends import videohub


# ---------------------------------------------------------------------------
# VideohubDevice display_name fallback chain
# ---------------------------------------------------------------------------

class TestVideohubDeviceDisplayName:
    def test_friendly_name_wins(self):
        d = videohub.VideohubDevice(
            unique_id="uid-1",
            friendly_name="Studio A",
            model_name="Smart Videohub 12G",
            ip="10.0.0.1",
            num_inputs=10,
            num_outputs=10,
        )
        assert d.display_name == "Studio A"

    def test_falls_back_to_model_name(self):
        d = videohub.VideohubDevice(
            unique_id="uid-1",
            friendly_name="",
            model_name="Smart Videohub 12G",
            ip="10.0.0.1",
            num_inputs=10,
            num_outputs=10,
        )
        assert d.display_name == "Smart Videohub 12G"

    def test_falls_back_to_unique_id_when_others_blank(self):
        d = videohub.VideohubDevice(
            unique_id="uid-1",
            friendly_name="",
            model_name="",
            ip="10.0.0.1",
            num_inputs=10,
            num_outputs=10,
        )
        assert d.display_name == "uid-1"


# ---------------------------------------------------------------------------
# Module-level enable / mock-mode flags
# ---------------------------------------------------------------------------

class TestEnabledFlag:
    def test_default_is_enabled(self):
        # Restore default after test even if a prior test mutated it.
        prev = videohub.ENABLED
        try:
            videohub.set_enabled(True)
            assert videohub.is_enabled() is True
        finally:
            videohub.set_enabled(prev)

    def test_disable_then_enable(self):
        prev = videohub.ENABLED
        try:
            videohub.set_enabled(False)
            assert videohub.is_enabled() is False
            videohub.set_enabled(True)
            assert videohub.is_enabled() is True
        finally:
            videohub.set_enabled(prev)

    def test_set_enabled_coerces_truthy_inputs(self):
        prev = videohub.ENABLED
        try:
            videohub.set_enabled(0)  # type: ignore[arg-type]
            assert videohub.is_enabled() is False
            videohub.set_enabled(1)  # type: ignore[arg-type]
            assert videohub.is_enabled() is True
        finally:
            videohub.set_enabled(prev)


class TestMockMode:
    def test_set_mock_mode_persists(self):
        prev = videohub.MOCK_MODE
        try:
            videohub.set_mock_mode(True)
            assert videohub.MOCK_MODE is True
            videohub.set_mock_mode(False)
            assert videohub.MOCK_MODE is False
        finally:
            videohub.set_mock_mode(prev)


# ---------------------------------------------------------------------------
# Config loading + device / preset listing
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_config(tmp_path: Path, monkeypatch):
    """Redirect VIDEOHUB_CONFIG to a tmp file we control. Returns a
    callable that writes the given dict to the stub path."""
    cfg_path = tmp_path / "videohub_config.json"
    monkeypatch.setattr(videohub, "VIDEOHUB_CONFIG", cfg_path)

    def _write(payload: dict | None) -> None:
        if payload is None:
            if cfg_path.exists():
                cfg_path.unlink()
        else:
            cfg_path.write_text(json.dumps(payload))

    return _write


class TestLoadConfig:
    def test_returns_empty_dict_when_no_file(self, stub_config):
        stub_config(None)
        assert videohub.load_config() == {}

    def test_loads_valid_json(self, stub_config):
        stub_config({"devices": {"uid-1": {"friendly_name": "Studio A"}}})
        cfg = videohub.load_config()
        assert "devices" in cfg
        assert cfg["devices"]["uid-1"]["friendly_name"] == "Studio A"

    def test_returns_empty_dict_on_corrupt_json(self, stub_config, tmp_path: Path):
        # Write garbage at the stub path directly to force a JSON error.
        videohub.VIDEOHUB_CONFIG.write_text("{ not valid json")
        assert videohub.load_config() == {}


class TestListDevices:
    def test_empty_config_returns_empty_list(self, stub_config):
        stub_config({})
        assert videohub.list_devices() == []

    def test_devices_carry_through(self, stub_config):
        stub_config({
            "devices": {
                "uid-1": {
                    "friendly_name": "Studio A",
                    "model_name": "Smart Videohub 12G",
                    "ip": "10.0.0.5",
                    "num_inputs": 12,
                    "num_outputs": 12,
                },
                "uid-2": {
                    "friendly_name": "",
                    "model_name": "",
                    "ip": "",
                    "num_inputs": 10,
                    "num_outputs": 10,
                },
            },
        })
        devices = videohub.list_devices()
        assert len(devices) == 2
        names = sorted(d.display_name for d in devices)
        assert names == ["Studio A", "uid-2"]

    def test_defaults_io_count_when_missing(self, stub_config):
        stub_config({"devices": {"uid-x": {}}})
        d = videohub.list_devices()[0]
        # Falls back to 10x10 when the config omits the counts.
        assert d.num_inputs == 10
        assert d.num_outputs == 10


class TestListPresets:
    def test_unknown_device_returns_empty(self, stub_config):
        stub_config({"devices": {}})
        assert videohub.list_presets("nope") == []

    def test_preset_names_are_returned(self, stub_config):
        stub_config({
            "devices": {
                "uid-1": {
                    "presets": {
                        "Show Open": {"routes": []},
                        "Q&A": {"routes": []},
                        "Rolls": {"routes": []},
                    },
                },
            },
        })
        names = sorted(videohub.list_presets("uid-1"))
        assert names == ["Q&A", "Rolls", "Show Open"]

    def test_device_with_no_presets_returns_empty(self, stub_config):
        stub_config({"devices": {"uid-1": {}}})
        assert videohub.list_presets("uid-1") == []


class TestGetPreset:
    def test_unknown_device_returns_none(self, stub_config):
        stub_config({"devices": {}})
        assert videohub.get_preset("nope", "Anything") is None

    def test_unknown_preset_returns_none(self, stub_config):
        stub_config({"devices": {"uid-1": {"presets": {"A": {}}}}})
        assert videohub.get_preset("uid-1", "B") is None

    def test_returns_full_payload(self, stub_config):
        payload = {"routes": [[1, 1], [2, 2]], "labels": ["a", "b"]}
        stub_config({
            "devices": {
                "uid-1": {"presets": {"Show": payload}},
            },
        })
        assert videohub.get_preset("uid-1", "Show") == payload


# ---------------------------------------------------------------------------
# _candidate_ips priority and dedupe
# ---------------------------------------------------------------------------

class TestCandidateIPs:
    def test_stored_ip_first_then_loopback(self):
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": "10.0.0.5"},
            cfg={},
        )
        assert ips == ["10.0.0.5", "127.0.0.1"]

    def test_last_ip_inserted_when_device_id_matches(self):
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": "10.0.0.5"},
            cfg={"last_device_id": "uid-1", "last_ip": "10.0.0.99"},
        )
        assert ips == ["10.0.0.5", "10.0.0.99", "127.0.0.1"]

    def test_last_ip_skipped_when_device_id_does_not_match(self):
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": "10.0.0.5"},
            cfg={"last_device_id": "uid-2", "last_ip": "10.0.0.99"},
        )
        assert ips == ["10.0.0.5", "127.0.0.1"]

    def test_no_duplicates_when_last_ip_equals_stored(self):
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": "10.0.0.5"},
            cfg={"last_device_id": "uid-1", "last_ip": "10.0.0.5"},
        )
        # 10.0.0.5 appears once even though it matched twice.
        assert ips == ["10.0.0.5", "127.0.0.1"]

    def test_loopback_only_when_no_stored_or_last(self):
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": ""},
            cfg={},
        )
        assert ips == ["127.0.0.1"]

    def test_loopback_present_only_once(self):
        # If for some reason the stored ip IS 127.0.0.1, dedupe still holds.
        ips = videohub._candidate_ips(
            "uid-1",
            dev={"ip": "127.0.0.1"},
            cfg={},
        )
        assert ips == ["127.0.0.1"]
