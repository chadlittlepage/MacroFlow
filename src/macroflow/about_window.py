"""About window for MacroFlow.

Mirrors Videohub Controller's About panel: photo background with a dark
overlay, app name, version, author block, copyright.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSImage,
    NSImageScaleAxesIndependently,
    NSImageView,
    NSMakeRect,
    NSObject,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Quartz import CGColorCreateGenericRGB

from macroflow import __version__

_RETAINED: list = []  # singleton — only keep the latest window


def _find_asset(name: str) -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        # .app bundle: .app/Contents/Resources/assets/
        here.parent.parent.parent.parent.parent / "Resources" / "assets" / name,
        # Dev mode: project_root/assets/
        here.parent.parent.parent.parent / "assets" / name,
        Path.cwd() / "assets" / name,
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


class _AboutController(NSObject):
    def init(self):
        self = objc.super(_AboutController, self).init()
        if self is not None:
            self.window = None
        return self

    def closeClicked_(self, sender):  # NOQA: N802
        if self.window:
            self.window.close()


def show_about_window() -> None:
    controller = _AboutController.alloc().init()

    win_w, win_h = 640, 480
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, win_w, win_h),
        style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("About MacroFlow")
    window.setReleasedWhenClosed_(False)
    window.center()
    controller.window = window

    content = window.contentView()

    # Background image
    bg_path = _find_asset("about_background.jpg")
    if bg_path is not None:
        bg = NSImage.alloc().initByReferencingFile_(str(bg_path))
        if bg is not None:
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, win_w, win_h))
            iv.setImage_(bg)
            iv.setImageScaling_(NSImageScaleAxesIndependently)
            iv.setAutoresizingMask_(2 | 16)
            content.addSubview_(iv)

    # Dark overlay for readability
    overlay = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, win_w, win_h))
    overlay.setWantsLayer_(True)
    overlay.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.0, 0.0, 0.0, 0.65))
    overlay.setAutoresizingMask_(2 | 16)
    content.addSubview_(overlay)

    def add_label(text: str, y: float, size: float, bold: bool = False) -> NSTextField:
        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, y, win_w - 40, size + 8)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(True)
        label.setTextColor_(NSColor.whiteColor())
        label.setAlignment_(2)  # NSCenterTextAlignment
        if bold:
            label.setFont_(NSFont.boldSystemFontOfSize_(size))
        else:
            label.setFont_(NSFont.systemFontOfSize_(size))
        label.setAutoresizingMask_(2 | 8)
        content.addSubview_(label)
        return label

    add_label("MacroFlow", win_h - 80, 32, bold=True)
    add_label(f"Version {__version__}", win_h - 120, 16)
    add_label(
        "Macro grid for DaVinci Resolve and Blackmagic Videohub",
        win_h - 165, 14,
    )

    add_label("Created by Chad Littlepage", win_h - 240, 16, bold=True)
    add_label("chad.littlepage@gmail.com", win_h - 270, 13)
    add_label("323.974.0444", win_h - 295, 13)

    add_label("© 2026 Chad Littlepage", win_h - 360, 11)

    close_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect((win_w - 100) / 2, 30, 100, 32)
    )
    close_btn.setTitle_("Close")
    close_btn.setBezelStyle_(1)
    close_btn.setKeyEquivalent_("\r")
    close_btn.setTarget_(controller)
    close_btn.setAction_("closeClicked:")
    close_btn.setAutoresizingMask_(1 | 4 | 32)
    content.addSubview_(close_btn)

    window.setLevel_(NSFloatingWindowLevel + 1)
    window.makeKeyAndOrderFront_(None)
    if hasattr(NSApp, "activate"):
        NSApp.activate()
    else:
        NSApp.activateIgnoringOtherApps_(True)

    _RETAINED.clear()
    _RETAINED.append((controller, window))
