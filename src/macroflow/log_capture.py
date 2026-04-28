"""Stdout/stderr tee with a ring buffer and live observers.

Installed once at app launch (`install()`), it wraps `sys.stdout` and
`sys.stderr` so every print/traceback still goes to the terminal AND lands
in a bounded in-memory buffer. The Console window subscribes to receive
new lines as they're produced.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Callable

_MAX_LINES = 10_000

_BUFFER: deque[str] = deque(maxlen=_MAX_LINES)
_LOCK = threading.Lock()
_OBSERVERS: list[Callable[[str], None]] = []
_INSTALLED = False
_ORIG_STDOUT = None
_ORIG_STDERR = None


class _Tee:
    def __init__(self, stream, tag: str) -> None:
        self._stream = stream
        self._tag = tag
        self._partial = ""

    def write(self, data: str) -> int:
        try:
            self._stream.write(data)
        except Exception:
            pass
        # Buffer line-by-line so the console renders per-line entries
        # rather than fragmented partial writes.
        self._partial += data
        if "\n" in self._partial:
            parts = self._partial.split("\n")
            self._partial = parts[-1]
            ts = time.strftime("%H:%M:%S")
            for line in parts[:-1]:
                entry = f"[{ts}] [{self._tag}] {line}"
                with _LOCK:
                    _BUFFER.append(entry)
                    observers = list(_OBSERVERS)
                for obs in observers:
                    try:
                        obs(entry)
                    except Exception:
                        pass
        return len(data)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False


def install() -> None:
    """Replace sys.stdout / sys.stderr with tee'd writers (idempotent)."""
    global _INSTALLED, _ORIG_STDOUT, _ORIG_STDERR
    if _INSTALLED:
        return
    _ORIG_STDOUT = sys.stdout
    _ORIG_STDERR = sys.stderr
    sys.stdout = _Tee(sys.stdout, "out")
    sys.stderr = _Tee(sys.stderr, "err")
    _INSTALLED = True


def snapshot() -> list[str]:
    """Return a copy of all currently-buffered lines."""
    with _LOCK:
        return list(_BUFFER)


def clear() -> None:
    with _LOCK:
        _BUFFER.clear()


def add_observer(fn: Callable[[str], None]) -> None:
    with _LOCK:
        if fn not in _OBSERVERS:
            _OBSERVERS.append(fn)


def remove_observer(fn: Callable[[str], None]) -> None:
    with _LOCK:
        try:
            _OBSERVERS.remove(fn)
        except ValueError:
            pass
