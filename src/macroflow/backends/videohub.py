"""Videohub backend.

Reads presets authored in the existing Videohub Controller app
(/Users/Shared/Videohub Controller/videohub_controller.json) and recalls
them by opening a fresh TCP connection to the Videohub on port 9990.

We do NOT depend on Videohub Controller running. We read its config file
to discover devices + named presets, then drive the Videohub directly via
its documented Ethernet protocol.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import json
import socket
import threading as _threading
from dataclasses import dataclass
from pathlib import Path

VIDEOHUB_PORT = 9990
VIDEOHUB_CONFIG = Path("/Users/Shared/Videohub Controller/videohub_controller.json")

# Test mode: when True, recall_preset() does not touch the network — it logs
# what it would have sent and returns True. Useful for exercising the macro
# UI without a Videohub on the LAN.
MOCK_MODE: bool = False

# Master switch: when False the backend is fully disabled. Every public
# entry-point (recall_preset, list_devices, list_presets) returns an empty/
# success-no-op result without touching the LAN or VHC config. Toggled from
# the Settings window.
ENABLED: bool = True

# After every recall_preset() call, this is updated with a record of what
# was just applied so the GUI can display it. {device_id, preset_name,
# routes: [(out, in), ...], endpoint: "..." | "(mock)" | None}
LAST_RECALL: dict = {}


def set_mock_mode(enabled: bool) -> None:
    global MOCK_MODE
    MOCK_MODE = bool(enabled)
    print(f"[videohub] MOCK_MODE = {MOCK_MODE}")


def set_enabled(enabled: bool) -> None:
    global ENABLED
    ENABLED = bool(enabled)
    print(f"[videohub] ENABLED = {ENABLED}")


def is_enabled() -> bool:
    return ENABLED


VHC_BUNDLE_ID = "com.chadlittlepage.videohubcontroller"


def is_alive(timeout: float = 0.4) -> bool:
    """True if Videohub Controller is currently running.

    The router itself isn't a stable signal (no router on the LAN is
    common during preset authoring), and 127.0.0.1:9990 probes the
    Blackmagic Videohub Daemon (a system service, not VHC). Asking
    NSWorkspace whether VHC's bundle is running is the truthful answer
    the user expects — green when VHC is up, red when it's quit.
    """
    try:
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            try:
                if str(app.bundleIdentifier() or "") == VHC_BUNDLE_ID:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        # Fallback: try TCP-connect to the saved device IPs from VHC's config.
        cfg = load_config()
        devices = cfg.get("devices", {}) or {}
        candidate_ips: list[str] = []
        last_id = cfg.get("last_device_id", "")
        if last_id and last_id in devices:
            ip = (devices[last_id] or {}).get("ip", "")
            if ip:
                candidate_ips.append(ip)
        for uid, dev in devices.items():
            if uid == last_id:
                continue
            ip = (dev or {}).get("ip", "")
            if ip and ip not in candidate_ips:
                candidate_ips.append(ip)
        for ip in candidate_ips:
            try:
                with socket.create_connection((ip, VIDEOHUB_PORT), timeout=timeout):
                    return True
            except OSError:
                continue
        return False


@dataclass
class VideohubDevice:
    unique_id: str
    friendly_name: str
    model_name: str
    ip: str
    num_inputs: int
    num_outputs: int

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.model_name or self.unique_id


def load_config() -> dict:
    if not VIDEOHUB_CONFIG.exists():
        return {}
    try:
        return json.loads(VIDEOHUB_CONFIG.read_text())
    except Exception as e:
        print(f"[videohub] Failed to read {VIDEOHUB_CONFIG}: {e}")
        return {}


def list_devices() -> list[VideohubDevice]:
    """All devices Videohub Controller has ever seen.

    Always reads the config — does NOT honor ENABLED — so the macro editor
    can populate Device/Preset dropdowns even while the global Videohub
    backend is toggled off in Settings. The fire-time gate at
    recall_preset() keeps disabled macros inert.
    """
    cfg = load_config()
    devices: list[VideohubDevice] = []
    for uid, dev in cfg.get("devices", {}).items():
        devices.append(VideohubDevice(
            unique_id=uid,
            friendly_name=dev.get("friendly_name", ""),
            model_name=dev.get("model_name", ""),
            ip=dev.get("ip", ""),
            num_inputs=int(dev.get("num_inputs", 10)),
            num_outputs=int(dev.get("num_outputs", 10)),
        ))
    return devices


def list_presets(device_id: str) -> list[str]:
    """Preset names saved against a given device. Always reads — see
    list_devices() comment for why we don't gate this on ENABLED."""
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id, {})
    return list(dev.get("presets", {}).keys())


def get_preset(device_id: str, preset_name: str) -> dict | None:
    """Full preset payload (routing list + labels)."""
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id, {})
    return dev.get("presets", {}).get(preset_name)


def _candidate_ips(device_id: str, dev: dict, cfg: dict) -> list[str]:
    """Endpoints to try, in priority order:
       1. The device's stored IP.
       2. cfg['last_ip'] if it matches this device_id and differs from above
          (Videohub Controller updates this when it discovers a new IP).
       3. 127.0.0.1 — the local BlackmagicVideohubDaemon, which Videohub
          Controller talks to even when no real hardware is connected.
          Sending routing to the daemon updates the GUI in real time.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for ip in (
        dev.get("ip", ""),
        cfg.get("last_ip", "") if cfg.get("last_device_id") == device_id else "",
        "127.0.0.1",
    ):
        if ip and ip not in seen:
            seen.add(ip)
            ordered.append(ip)
    return ordered


def _try_send(ip: str, payload: bytes, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, VIDEOHUB_PORT), timeout=timeout) as sock:
            sock.sendall(payload)
            return True
    except OSError as e:
        print(f"[videohub]   {ip}: {e}")
        return False


# ---------------------------------------------------------------------------
# Cross-process API to Videohub Controller via NSDistributedNotificationCenter.
# When VHC is running, this is the preferred path: it drives VHC's own recall
# logic, so VHC's matrix/LCD update in real time and there's no protocol
# divergence. Falls back to raw TCP if VHC doesn't reply within a timeout.
# ---------------------------------------------------------------------------

NOTIF_RECALL = "MacroFlowRecallPreset"
NOTIF_RECALL_RESULT = "MacroFlowRecallPresetResult"

# A singleton bridge holds the NSDistributedNotificationCenter observer.
# It must be created on the main thread so its callback fires via the main
# runloop; PyObjC observers registered on a worker thread never receive
# callbacks (no runloop). All worker threads then signal/wait via the
# shared _PENDING dict.

_BRIDGE = None
_PENDING_LOCK = _threading.Lock()
_PENDING: dict = {}  # request_id -> {"event": Event, "result": (ok, msg)}


def _ensure_bridge_class():
    """Lazily define the bridge class so module import doesn't pull in AppKit
    when the videohub backend is imported in a non-GUI context."""
    global _BridgeClass
    if "_BridgeClass" in globals():
        return _BridgeClass
    from Foundation import NSObject

    class _BridgeClass(NSObject):  # noqa: F811
        def handleResult_(self, note):  # NOQA: N802
            info = note.userInfo() or {}
            rid = str(info.get("request_id") or "")
            with _PENDING_LOCK:
                slot = _PENDING.get(rid)
            if slot is None:
                return
            slot["result"] = (
                str(info.get("ok") or "") == "1",
                str(info.get("message") or ""),
            )
            slot["event"].set()

    globals()["_BridgeClass"] = _BridgeClass
    return _BridgeClass


def init_bridge() -> None:
    """Create the singleton observer. MUST be called on the main thread,
    before any worker thread tries to use _try_recall_via_vhc()."""
    global _BRIDGE
    if _BRIDGE is not None:
        return
    try:
        from AppKit import NSDistributedNotificationCenter
    except ImportError:
        print("[videohub] AppKit not available; VHC API disabled")
        return
    cls = _ensure_bridge_class()
    _BRIDGE = cls.alloc().init()
    center = NSDistributedNotificationCenter.defaultCenter()
    center.addObserver_selector_name_object_(
        _BRIDGE, "handleResult:", NOTIF_RECALL_RESULT, None,
    )
    print("[videohub] VHC notification bridge ready")


def _try_recall_via_vhc(device_id: str, preset_name: str,
                         timeout: float = 1.5) -> tuple[bool, str]:
    """Post a recall request to Videohub Controller and wait for its ack.

    Returns (succeeded, info). If VHC isn't running or doesn't reply within
    the timeout, returns (False, "no reply") so the caller can fall back.
    """
    if _BRIDGE is None:
        return False, "bridge not initialised"
    try:
        from AppKit import NSDistributedNotificationCenter
    except ImportError:
        return False, "AppKit unavailable"
    import uuid

    request_id = str(uuid.uuid4())
    event = _threading.Event()
    result: tuple[bool, str] = (False, "no reply")
    slot: dict = {"event": event, "result": result}
    with _PENDING_LOCK:
        _PENDING[request_id] = slot
    try:
        center = NSDistributedNotificationCenter.defaultCenter()
        center.postNotificationName_object_userInfo_deliverImmediately_(
            NOTIF_RECALL,
            None,
            {"device_id": device_id,
             "preset_name": preset_name,
             "request_id": request_id},
            True,
        )
        event.wait(timeout=timeout)
    finally:
        with _PENDING_LOCK:
            _PENDING.pop(request_id, None)
    final = slot["result"]
    if isinstance(final, tuple) and len(final) == 2:
        return bool(final[0]), str(final[1])
    return False, "invalid result"


def recall_preset(device_id: str, preset_name: str, timeout: float = 2.0) -> bool:
    """Open a TCP connection to a Videohub endpoint and apply the preset routing.

    Tries multiple endpoints in order — the device's saved IP, the top-level
    last_ip (if it matches), and finally the local Videohub Daemon at
    127.0.0.1. Returns True on the first successful send.
    """
    if not ENABLED:
        # Disabled in settings — treat as success no-op so a macro that has
        # both Videohub and Resolve actions still fires the Resolve side.
        return True
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id)
    if not dev:
        print(f"[videohub] Unknown device {device_id}")
        return False
    preset = dev.get("presets", {}).get(preset_name)
    if not preset:
        print(f"[videohub] Device {device_id} has no preset '{preset_name}'")
        return False
    routing = preset.get("routing", [])
    if not routing:
        print(f"[videohub] Preset '{preset_name}' has no routing")
        return False

    # Build the Videohub video output routing block. Indices are 0-based;
    # routing[output_idx] = input_idx, with -1 meaning "skip".
    lines = ["VIDEO OUTPUT ROUTING:"]
    for output_idx, input_idx in enumerate(routing):
        if input_idx is None or input_idx < 0:
            continue
        lines.append(f"{output_idx} {input_idx}")
    payload = ("\n".join(lines) + "\n\n").encode("utf-8")
    n_routes = len(lines) - 1
    label = dev.get("friendly_name") or dev.get("model_name") or device_id

    routes = [
        (out_idx, in_idx)
        for out_idx, in_idx in enumerate(routing)
        if in_idx is not None and in_idx >= 0
    ]

    def _record(endpoint: str | None, ok: bool) -> None:
        global LAST_RECALL
        LAST_RECALL = {
            "device_id": device_id,
            "device_label": label,
            "preset_name": preset_name,
            "routes": routes,
            "endpoint": endpoint,
            "ok": ok,
        }

    if MOCK_MODE:
        print(f"[videohub MOCK] would recall '{preset_name}' on {label}: "
              f"{n_routes} route(s)")
        _record("(mock)", True)
        return True

    # Preferred path: ask Videohub Controller via NSDistributedNotificationCenter.
    # VHC drives its own recall, so its matrix/LCD update in real time.
    ok, msg = _try_recall_via_vhc(device_id, preset_name)
    if ok:
        print(f"[videohub] recall OK via VHC API ({msg})")
        _record("vhc-api", True)
        return True
    if msg and msg != "no reply":
        # VHC was reachable but rejected the request (preset/device mismatch).
        print(f"[videohub] VHC API rejected: {msg}")

    # Fallback: raw TCP to known endpoints (works without VHC running).
    candidates = _candidate_ips(device_id, dev, cfg)
    print(f"[videohub] falling back to TCP for '{preset_name}' on {label} "
          f"({n_routes} routes); trying {candidates}")
    for ip in candidates:
        if _try_send(ip, payload, timeout):
            print(f"[videohub] recall OK via {ip}")
            _record(ip, True)
            return True
    print("[videohub] recall FAILED — no endpoint accepted the payload")
    _record(None, False)
    return False


