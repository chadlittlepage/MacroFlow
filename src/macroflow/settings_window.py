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
    NSPopUpButton,
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

GRID_CHOICES = [(4, 4), (6, 6), (8, 8), (10, 10), (12, 12), (20, 20), (40, 40)]


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
        self._schedule_debounced_save()

    def titleChanged_(self, sender):  # NOQA: N802
        v = float(sender.floatValue())
        self.controller._store.grid.title_font_size = v
        self.title_label.setStringValue_(f"Title: {int(v)}pt")
        self.controller._apply_font_sizes()
        self._schedule_debounced_save()

    def hotkeyChanged_(self, sender):  # NOQA: N802
        v = float(sender.floatValue())
        self.controller._store.grid.hotkey_font_size = v
        self.hotkey_label.setStringValue_(f"Hotkey: {int(v)}pt")
        self.controller._apply_font_sizes()
        self._schedule_debounced_save()

    @objc.python_method
    def _schedule_debounced_save(self) -> None:
        """Coalesce slider-drag saves. NSSlider in continuous mode fires its
        action on every pixel of mouse movement — saving on each tick was
        writing macroflow.json dozens of times per drag. Schedule a single
        deferred save on the runloop and cancel-and-reschedule on each new
        tick; the actual save lands ~250 ms after the user stops moving.
        """
        NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
            self, b"_saveNow:", None,
        )
        self.performSelector_withObject_afterDelay_(b"_saveNow:", None, 0.25)

    def _saveNow_(self, _arg):  # NOQA: N802 (Cocoa selector)
        try:
            self.controller._store.save()
        except Exception as e:
            print(f"[settings] debounced save failed: {e}")

    def gridSizeChanged_(self, sender):  # NOQA: N802
        idx = int(sender.indexOfSelectedItem())
        if idx < 0 or idx >= len(GRID_CHOICES):
            return
        rows, cols = GRID_CHOICES[idx]
        self.controller.apply_grid_size(rows, cols)

    def videohubToggled_(self, sender):  # NOQA: N802
        on = bool(int(sender.state()) == 1)
        self.controller.set_videohub_enabled(on)

    def keepOnTopToggled_(self, sender):  # NOQA: N802
        on = bool(int(sender.state()) == 1)
        self.controller.set_keep_on_top(on)

    def globalHotkeysToggled_(self, sender):  # NOQA: N802
        on = bool(int(sender.state()) == 1)
        self.controller.set_global_hotkeys(on)
        # _apply_global_hotkeys may have flipped the flag back to False if
        # the user declined the Accessibility prompt — re-sync the checkbox.
        try:
            sender.setState_(
                1 if self.controller._store.grid.global_hotkeys else 0,
            )
        except Exception:
            pass

    def resetDefaults_(self, sender):  # NOQA: N802
        grid = self.controller._store.grid
        prev = (grid.display_font_size, grid.title_font_size, grid.hotkey_font_size)
        new = (float(DEFAULT_DISPLAY), float(DEFAULT_TITLE), float(DEFAULT_HOTKEY))
        if prev == new:
            return
        self._apply_font_sizes(*new)

        ctrl = self.controller

        def _apply(values):
            d, t, h = values
            grid.display_font_size = float(d)
            grid.title_font_size = float(t)
            grid.hotkey_font_size = float(h)
            try:
                self.display_slider.setFloatValue_(float(d))
                self.title_slider.setFloatValue_(float(t))
                self.hotkey_slider.setFloatValue_(float(h))
                self.display_label.setStringValue_(f"Display: {int(d)}pt")
                self.title_label.setStringValue_(f"Title: {int(t)}pt")
                self.hotkey_label.setStringValue_(f"Hotkey: {int(h)}pt")
            except Exception:
                pass
            ctrl._apply_font_sizes()
            ctrl._store.save()

        try:
            ctrl.push_undo(
                "reset font sizes to defaults",
                lambda: _apply(prev),
                lambda: _apply(new),
            )
        except Exception:
            pass

    @objc.python_method
    def _apply_font_sizes(self, display: float, title: float, hotkey: float) -> None:
        grid = self.controller._store.grid
        grid.display_font_size = float(display)
        grid.title_font_size = float(title)
        grid.hotkey_font_size = float(hotkey)
        self.display_slider.setFloatValue_(float(display))
        self.title_slider.setFloatValue_(float(title))
        self.hotkey_slider.setFloatValue_(float(hotkey))
        self.display_label.setStringValue_(f"Display: {int(display)}pt")
        self.title_label.setStringValue_(f"Title: {int(title)}pt")
        self.hotkey_label.setStringValue_(f"Hotkey: {int(hotkey)}pt")
        self.controller._apply_font_sizes()
        self.controller._store.save()


def show_settings_window(controller) -> None:
    sc = _SettingsController.alloc().init()
    sc.attach(controller)

    win_w, win_h = 360, 500
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

    # Grid size dropdown (4x4 .. 20x20). Lives below the sliders.
    grid_row_y = win_h - 240
    grid_label = NSTextField.alloc().initWithFrame_(
        NSMakeRect(20, grid_row_y + 4, 100, 18),
    )
    grid_label.setStringValue_("Grid size:")
    grid_label.setBezeled_(False)
    grid_label.setDrawsBackground_(False)
    grid_label.setEditable_(False)
    grid_label.setSelectable_(False)
    grid_label.setFont_(NSFont.boldSystemFontOfSize_(12))
    grid_label.setTextColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
    )
    content.addSubview_(grid_label)

    grid_popup = NSPopUpButton.alloc().initWithFrame_(
        NSMakeRect(120, grid_row_y, win_w - 140, 26),
    )
    for r, c in GRID_CHOICES:
        grid_popup.addItemWithTitle_(f"{r} × {c}  ({r * c} cells)")
    current = (grid.rows, grid.cols)
    if current in GRID_CHOICES:
        grid_popup.selectItemAtIndex_(GRID_CHOICES.index(current))
    grid_popup.setTarget_(sc)
    grid_popup.setAction_("gridSizeChanged:")
    content.addSubview_(grid_popup)
    sc.grid_popup = grid_popup

    # Videohub master switch — when off, the app runs without any Videohub
    # assumptions (no status probe, no recall on macro fire, no editor
    # device list).
    vh_y = grid_row_y - 40
    vh_check = NSButton.alloc().initWithFrame_(
        NSMakeRect(20, vh_y, win_w - 40, 22),
    )
    vh_check.setButtonType_(3)  # NSButtonTypeSwitch
    vh_check.setTitle_("Enable Videohub backend")
    vh_check.setState_(1 if grid.videohub_enabled else 0)
    vh_check.setTarget_(sc)
    vh_check.setAction_("videohubToggled:")
    content.addSubview_(vh_check)
    sc.videohub_check = vh_check

    # Window & Hotkey Behavior section — ported from Videohub Controller.
    sect_y = vh_y - 36
    sect_lbl = NSTextField.alloc().initWithFrame_(
        NSMakeRect(20, sect_y, win_w - 40, 18),
    )
    sect_lbl.setStringValue_("Window & Hotkey Behavior")
    sect_lbl.setBezeled_(False)
    sect_lbl.setDrawsBackground_(False)
    sect_lbl.setEditable_(False)
    sect_lbl.setSelectable_(False)
    sect_lbl.setFont_(NSFont.boldSystemFontOfSize_(13))
    sect_lbl.setTextColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
    )
    content.addSubview_(sect_lbl)

    def _add_toggle(y_pos: float, title: str, sub: str,
                    state: bool, action: str):
        cb = NSButton.alloc().initWithFrame_(
            NSMakeRect(20, y_pos, win_w - 40, 22),
        )
        cb.setButtonType_(3)
        cb.setTitle_(title)
        cb.setState_(1 if state else 0)
        cb.setTarget_(sc)
        cb.setAction_(action)
        content.addSubview_(cb)
        sub_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(38, y_pos - 16, win_w - 60, 14),
        )
        sub_lbl.setStringValue_(sub)
        sub_lbl.setBezeled_(False)
        sub_lbl.setDrawsBackground_(False)
        sub_lbl.setEditable_(False)
        sub_lbl.setSelectable_(False)
        sub_lbl.setFont_(NSFont.systemFontOfSize_(11))
        sub_lbl.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_DIM),
        )
        content.addSubview_(sub_lbl)
        return cb

    sc.keep_on_top_check = _add_toggle(
        sect_y - 28, "Keep on Top",
        "Float above other apps like DaVinci Resolve",
        grid.keep_on_top, "keepOnTopToggled:",
    )
    sc.global_hotkeys_check = _add_toggle(
        sect_y - 72, "Global Hotkeys",
        "Hotkeys fire even when MacroFlow is not focused.\n"
        "Requires Accessibility permission.",
        grid.global_hotkeys, "globalHotkeysToggled:",
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
