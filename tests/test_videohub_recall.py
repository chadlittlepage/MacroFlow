"""Tests for the videohub.recall_preset orchestration.

The real recall path opens a TCP socket to either VHC's notification
bridge or the local Videohub Daemon. We monkeypatch _try_send and
_try_recall_via_vhc to take the network out of the picture and assert
on the priority order, mock-mode short-circuit, the LAST_RECALL trail,
and the routing-payload format.

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
# Stub config + capture helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_config(tmp_path: Path, monkeypatch):
    """Redirect VIDEOHUB_CONFIG to a tmp file and return a writer."""
    cfg_path = tmp_path / "videohub_config.json"
    monkeypatch.setattr(videohub, "VIDEOHUB_CONFIG", cfg_path)

    def _write(payload: dict) -> None:
        cfg_path.write_text(json.dumps(payload))

    return _write


@pytest.fixture
def reset_recall_state(monkeypatch):
    """Each test starts with a clean LAST_RECALL + ENABLED + MOCK_MODE."""
    prev_last = dict(videohub.LAST_RECALL)
    prev_enabled = videohub.ENABLED
    prev_mock = videohub.MOCK_MODE
    videohub.LAST_RECALL = {}
    videohub.ENABLED = True
    videohub.MOCK_MODE = False
    yield
    videohub.LAST_RECALL = prev_last
    videohub.ENABLED = prev_enabled
    videohub.MOCK_MODE = prev_mock


@pytest.fixture
def trace_send(monkeypatch):
    """Capture (ip, payload, timeout) tuples instead of opening sockets."""
    calls: list[tuple[str, bytes, float]] = []

    def fake(ip, payload, timeout):
        calls.append((ip, payload, timeout))
        return False

    monkeypatch.setattr(videohub, "_try_send", fake)
    return calls


@pytest.fixture
def stub_vhc_unreachable(monkeypatch):
    """Pretend VHC bridge isn't running so the TCP fallback runs."""
    monkeypatch.setattr(
        videohub, "_try_recall_via_vhc",
        lambda dev, preset, timeout=1.5: (False, "no reply"),
    )


@pytest.fixture
def stub_vhc_success(monkeypatch):
    """Pretend VHC accepted the recall — TCP path should NOT run."""
    monkeypatch.setattr(
        videohub, "_try_recall_via_vhc",
        lambda dev, preset, timeout=1.5: (True, "vhc-recalled"),
    )


# ---------------------------------------------------------------------------
# Disabled / no-config / unknown-device / unknown-preset short-circuits
# ---------------------------------------------------------------------------

class TestRecallShortCircuits:
    def test_disabled_returns_true_no_op(
        self, reset_recall_state, stub_config, trace_send,
    ):
        # Settings → Videohub disabled. Macros that mix backends still need
        # the Videohub side to "succeed" so the others fire.
        videohub.ENABLED = False
        stub_config({"devices": {"d1": {"presets": {"p1": {"routing": [0]}}}}})
        assert videohub.recall_preset("d1", "p1") is True
        # Crucially: the network path must not have been touched.
        assert trace_send == []

    def test_unknown_device_returns_false(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, trace_send,
    ):
        stub_config({"devices": {}})
        assert videohub.recall_preset("ghost", "p") is False
        assert trace_send == []

    def test_unknown_preset_returns_false(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, trace_send,
    ):
        stub_config({"devices": {"d1": {"presets": {}}}})
        assert videohub.recall_preset("d1", "ghost") is False
        assert trace_send == []

    def test_empty_routing_returns_false(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, trace_send,
    ):
        stub_config({"devices": {"d1": {"presets": {"p": {"routing": []}}}}})
        assert videohub.recall_preset("d1", "p") is False
        assert trace_send == []


# ---------------------------------------------------------------------------
# MOCK_MODE recall — never touches the network.
# ---------------------------------------------------------------------------

class TestRecallMockMode:
    def test_mock_mode_records_and_returns_true(
        self, reset_recall_state, stub_config, trace_send,
    ):
        videohub.MOCK_MODE = True
        stub_config({
            "devices": {
                "d1": {
                    "friendly_name": "Studio A",
                    "presets": {"p1": {"routing": [0, 1, 2]}},
                },
            },
        })
        ok = videohub.recall_preset("d1", "p1")
        assert ok is True
        # Network never called.
        assert trace_send == []
        # LAST_RECALL trail uses the "(mock)" sentinel endpoint.
        assert videohub.LAST_RECALL["endpoint"] == "(mock)"
        assert videohub.LAST_RECALL["device_label"] == "Studio A"
        assert videohub.LAST_RECALL["preset_name"] == "p1"
        assert videohub.LAST_RECALL["ok"] is True


# ---------------------------------------------------------------------------
# VHC bridge succeeds → TCP fallback never runs.
# ---------------------------------------------------------------------------

class TestRecallVhcBridge:
    def test_vhc_success_skips_tcp(
        self, reset_recall_state, stub_config, stub_vhc_success, trace_send,
    ):
        stub_config({
            "devices": {"d1": {"presets": {"p1": {"routing": [0, 1]}}}},
        })
        assert videohub.recall_preset("d1", "p1") is True
        # TCP path was bypassed because VHC said yes.
        assert trace_send == []
        assert videohub.LAST_RECALL["endpoint"] == "vhc-api"


# ---------------------------------------------------------------------------
# VHC unreachable → TCP fallback in priority order.
# ---------------------------------------------------------------------------

class TestRecallTcpFallback:
    def test_tries_stored_ip_first(
        self,
        reset_recall_state,
        stub_config,
        stub_vhc_unreachable,
        monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {"p1": {"routing": [0, 1, 2]}},
                },
            },
        })
        # Make stored IP succeed → loopback should never be tried.
        called: list[str] = []

        def fake(ip, payload, timeout):
            called.append(ip)
            return ip == "10.0.0.5"

        monkeypatch.setattr(videohub, "_try_send", fake)
        assert videohub.recall_preset("d1", "p1") is True
        assert called == ["10.0.0.5"]
        assert videohub.LAST_RECALL["endpoint"] == "10.0.0.5"

    def test_falls_through_to_loopback_when_stored_fails(
        self,
        reset_recall_state,
        stub_config,
        stub_vhc_unreachable,
        monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {"p1": {"routing": [0, 1]}},
                },
            },
        })
        called: list[str] = []

        def fake(ip, payload, timeout):
            called.append(ip)
            return ip == "127.0.0.1"

        monkeypatch.setattr(videohub, "_try_send", fake)
        assert videohub.recall_preset("d1", "p1") is True
        assert called == ["10.0.0.5", "127.0.0.1"]
        assert videohub.LAST_RECALL["endpoint"] == "127.0.0.1"

    def test_all_endpoints_fail_returns_false(
        self,
        reset_recall_state,
        stub_config,
        stub_vhc_unreachable,
        monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {"p1": {"routing": [0, 1]}},
                },
            },
        })
        monkeypatch.setattr(
            videohub, "_try_send", lambda ip, payload, timeout: False,
        )
        assert videohub.recall_preset("d1", "p1") is False
        assert videohub.LAST_RECALL["endpoint"] is None
        assert videohub.LAST_RECALL["ok"] is False


# ---------------------------------------------------------------------------
# Routing payload format — VHC speaks the literal "VIDEO OUTPUT ROUTING:"
# block. We verify the formatter emits the right bytes.
# ---------------------------------------------------------------------------

class TestRoutingPayload:
    def test_payload_format_skips_negative_inputs(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {
                        # output 0 → input 3, output 1 = skip (-1), output 2 → input 7.
                        "p1": {"routing": [3, -1, 7]},
                    },
                },
            },
        })
        captured: dict = {}

        def fake(ip, payload, timeout):
            captured["payload"] = payload
            return True

        monkeypatch.setattr(videohub, "_try_send", fake)
        assert videohub.recall_preset("d1", "p1") is True
        text = captured["payload"].decode("utf-8")
        assert text.startswith("VIDEO OUTPUT ROUTING:\n")
        # Negative input was skipped.
        assert "1 " not in text  # "1 -1" / "1 X" lines should be absent
        # The two valid routes are present.
        assert "0 3" in text
        assert "2 7" in text
        # Trailing blank line marks end of block.
        assert text.endswith("\n\n")

    def test_payload_format_skips_none_inputs(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {"p1": {"routing": [None, 4, None, 9]}},
                },
            },
        })
        captured: dict = {}

        def fake(ip, payload, timeout):
            captured["payload"] = payload
            return True

        monkeypatch.setattr(videohub, "_try_send", fake)
        videohub.recall_preset("d1", "p1")
        text = captured["payload"].decode("utf-8")
        # outputs 0 and 2 (None inputs) absent; outputs 1 and 3 present.
        assert "1 4" in text
        assert "3 9" in text


# ---------------------------------------------------------------------------
# LAST_RECALL trail — the GUI reads this to display "what fired."
# ---------------------------------------------------------------------------

class TestLastRecallTrail:
    def test_routes_recorded_only_for_valid_inputs(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "model_name": "Smart Videohub 12G",
                    "presets": {"p1": {"routing": [3, -1, 7, None, 0]}},
                },
            },
        })
        monkeypatch.setattr(
            videohub, "_try_send", lambda ip, p, t: True,
        )
        videohub.recall_preset("d1", "p1")
        # Routes is [(out, in), ...] for valid entries only.
        assert sorted(videohub.LAST_RECALL["routes"]) == [(0, 3), (2, 7), (4, 0)]
        # Falls back to model_name when friendly_name is empty.
        assert videohub.LAST_RECALL["device_label"] == "Smart Videohub 12G"

    def test_label_falls_back_to_device_id(
        self, reset_recall_state, stub_config, stub_vhc_unreachable, monkeypatch,
    ):
        stub_config({
            "devices": {
                "d1": {
                    "ip": "10.0.0.5",
                    "presets": {"p1": {"routing": [0]}},
                },
            },
        })
        monkeypatch.setattr(
            videohub, "_try_send", lambda ip, p, t: True,
        )
        videohub.recall_preset("d1", "p1")
        assert videohub.LAST_RECALL["device_label"] == "d1"
