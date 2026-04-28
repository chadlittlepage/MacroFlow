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
import threading
from dataclasses import dataclass
from pathlib import Path

VIDEOHUB_PORT = 9990
VIDEOHUB_CONFIG = Path("/Users/Shared/Videohub Controller/videohub_controller.json")


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
    """All devices Videohub Controller has ever seen."""
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
    """Preset names saved against a given device."""
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id, {})
    return list(dev.get("presets", {}).keys())


def get_preset(device_id: str, preset_name: str) -> dict | None:
    """Full preset payload (routing list + labels)."""
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id, {})
    return dev.get("presets", {}).get(preset_name)


def recall_preset(device_id: str, preset_name: str, timeout: float = 3.0) -> bool:
    """Open a TCP connection to the device's IP and apply the preset routing.

    Returns True on success, False on any failure (no device, no IP, no preset,
    socket error, etc.). Errors are logged to stdout, not raised — MacroFlow
    fires multiple actions in parallel and one backend failure should not stop
    the others.
    """
    cfg = load_config()
    dev = cfg.get("devices", {}).get(device_id)
    if not dev:
        print(f"[videohub] Unknown device {device_id}")
        return False
    ip = dev.get("ip", "")
    if not ip:
        print(f"[videohub] Device {device_id} has no IP")
        return False
    preset = dev.get("presets", {}).get(preset_name)
    if not preset:
        print(f"[videohub] Device {device_id} has no preset '{preset_name}'")
        return False
    routing = preset.get("routing", [])
    if not routing:
        print(f"[videohub] Preset '{preset_name}' has no routing")
        return False

    # Build the Videohub video output routing block. Indices are 0-based on
    # the wire; routing[output_idx] = input_idx, with -1 meaning "skip".
    lines = ["VIDEO OUTPUT ROUTING:"]
    for output_idx, input_idx in enumerate(routing):
        if input_idx is None or input_idx < 0:
            continue
        lines.append(f"{output_idx} {input_idx}")
    payload = ("\n".join(lines) + "\n\n").encode("utf-8")

    try:
        with socket.create_connection((ip, VIDEOHUB_PORT), timeout=timeout) as sock:
            sock.sendall(payload)
            return True
    except OSError as e:
        print(f"[videohub] Failed to recall '{preset_name}' on {ip}: {e}")
        return False


def recall_preset_async(device_id: str, preset_name: str) -> threading.Thread:
    """Fire-and-forget recall on a background thread."""
    t = threading.Thread(
        target=recall_preset, args=(device_id, preset_name), daemon=True,
    )
    t.start()
    return t
