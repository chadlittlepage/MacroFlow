"""Settings window for MacroFlow.

Sliders for font sizes (LCD/Display, Cell Title, Hotkey). Live-applies to
the running UI as you drag.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSObject,
    NSSlider,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

_RETAINED: list = []

WINDOW_BG = (0.17, 0.17, 0.17, 1.0)                    # VHC panel calibrated → renders ~#4a
LIGHT_BG = (0x49 / 255, 0x49 / 255, 0x49 / 255, 1.0)   # #494949 accent
TEXT_DIM = (0.60, 0.60, 0.58, 1.0)
TEXT_BRIGHT = (0.92, 0.92, 0.92, 1.0)

DEFAULT_DISPLAY = 12.0
DEFAULT_TITLE = 13.0
DEFAULT_HOTKEY = 26.0


class _SettingsController(NSObject):
    def init(self):
        self = objc.super(_SettingsController, self).init()
        if self is None:
            return None
        self.window = None
        self.controller = None  # AppController
        return self

    @objc.python_method
    def attach(self, controller) -> None:
        self.controller = controller

    def displayChanged_(self, sender):  # NOQA: N802
        v = float(sender.floatValue())
        self.controller._store.grid.display_font_size = v
        self.display_label.setStringValue_(f"Display: {int(v)}pt")
        self.controller._apply_font_sizes()
        self.controller._store.save()

    def titleChanged_(self, sender):  # NOQA: N802
        v = float(sender.floatValue())
        self.controller._store.grid.title_font_size = v
        self.title_label.setStringValue_(f"Title: {int(v)}pt")
        self.controller._apply_font_sizes()
        self.controller._store.save()

    def hotkeyChanged_(self, sender):  # NOQA: N802
        v = float(sender.floatValue())
        self.controller._store.grid.hotkey_font_size = v
        self.hotkey_label.setStringValue_(f"Hotkey: {int(v)}pt")
        self.controller._apply_font_sizes()
        self.controller._store.save()

    def resetDefaults_(self, sender):  # NOQA: N802
        grid = self.controller._store.grid
        grid.display_font_size = DEFAULT_DISPLAY
        grid.title_font_size = DEFAULT_TITLE
        grid.hotkey_font_size = DEFAULT_HOTKEY
        self.display_slider.setFloatValue_(DEFAULT_DISPLAY)
        self.title_slider.setFloatValue_(DEFAULT_TITLE)
        self.hotkey_slider.setFloatValue_(DEFAULT_HOTKEY)
        self.display_label.setStringValue_(f"Display: {int(DEFAULT_DISPLAY)}pt")
        self.title_label.setStringValue_(f"Title: {int(DEFAULT_TITLE)}pt")
        self.hotkey_label.setStringValue_(f"Hotkey: {int(DEFAULT_HOTKEY)}pt")
        self.controller._apply_font_sizes()
        self.controller._store.save()


def show_settings_window(controller) -> None:
    sc = _SettingsController.alloc().init()
    sc.attach(controller)

    win_w, win_h = 360, 300
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, win_w, win_h), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("MacroFlow Settings")
    window.setReleasedWhenClosed_(False)
    window.setBackgroundColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*WINDOW_BG),
    )
    from AppKit import NSAppearance as _NSApp
    _dark = _NSApp.appearanceNamed_("NSAppearanceNameDarkAqua")
    if _dark:
        window.setAppearance_(_dark)
    window.center()
    sc.window = window
    content = window.contentView()

    grid = controller._store.grid

    def add_slider(y: float, label_text: str, value: float,
                   min_v: float, max_v: float, action: str) -> tuple:
        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, y + 22, 200, 18),
        )
        label.setStringValue_(f"{label_text}: {int(value)}pt")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.boldSystemFontOfSize_(12))
        label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
        )
        content.addSubview_(label)

        slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(20, y, win_w - 40, 22),
        )
        slider.setMinValue_(min_v)
        slider.setMaxValue_(max_v)
        slider.setFloatValue_(value)
        slider.setContinuous_(True)
        slider.setTarget_(sc)
        slider.setAction_(action)
        content.addSubview_(slider)
        return label, slider

    sc.display_label, sc.display_slider = add_slider(
        win_h - 70, "Display", grid.display_font_size, 8, 22, "displayChanged:",
    )
    sc.title_label, sc.title_slider = add_slider(
        win_h - 130, "Title", grid.title_font_size, 8, 24, "titleChanged:",
    )
    sc.hotkey_label, sc.hotkey_slider = add_slider(
        win_h - 190, "Hotkey", grid.hotkey_font_size, 12, 60, "hotkeyChanged:",
    )

    # Reset to Defaults button — sits beneath the three sliders.
    reset_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect((win_w - 160) / 2, 30, 160, 32),
    )
    reset_btn.setTitle_("Reset to Defaults")
    reset_btn.setBezelStyle_(NSBezelStyleRounded)
    reset_btn.setTarget_(sc)
    reset_btn.setAction_("resetDefaults:")
    content.addSubview_(reset_btn)

    window.setLevel_(NSFloatingWindowLevel)
    window.makeKeyAndOrderFront_(None)
    _RETAINED.clear()
    _RETAINED.append((sc, window))
