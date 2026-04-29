"""Main Cocoa app: a clickable grid of macro buttons.

Each cell fires a Macro (Videohub preset + Resolve track state, in parallel).
Right-click or double-click a cell to open the macro editor.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import sys

from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSEvent,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFont,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSOpenPanel,
    NSSavePanel,
    NSTextField,
    NSTrackingActiveInKeyWindow,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
import threading

import objc
from objc import super  # type: ignore
from PyObjCTools import AppHelper

from macroflow.backends import resolve, videohub
from macroflow.macro import Macro, MacroStore
from macroflow.macro_editor import MacroEditorWindow

# Quartz is required (we ship macOS-only via PyObjC); a missing import means
# the whole app can't run, so falling back silently in hot paths just hides
# real failures. Import once at module load.
try:
    from Quartz import CATextLayer, CGColorCreateGenericRGB
except ImportError:  # pragma: no cover — only fires on broken installs
    CATextLayer = None
    CGColorCreateGenericRGB = None

_DEFAULT_MACRO_COLOR = Macro.__dataclass_fields__["color"].default


class _MacroCellButton(NSButton):
    """NSButton subclass that forwards right-clicks to its target.

    Default NSButton behavior fires the action only on left-mouse-down. We
    want right-click to open the macro editor for that cell, so we override
    rightMouseDown_ and call back to the AppController via a known method.
    """

    def rightMouseDown_(self, event):  # noqa: N802 (Cocoa accessor)
        target = self.target()
        if target is not None and hasattr(target, "cellRightClicked_"):
            target.cellRightClicked_(self)
        else:
            # Fall back to default behavior so we don't swallow the event.
            super().rightMouseDown_(event)

# Glyphs displayed on the cell in front of the hotkey letter when the macro
# requires a modifier. Mirrors the standard macOS menu shortcuts.
_MODIFIER_GLYPH = {
    "":      "",
    "Cmd":   "⌘",
    "Ctrl":  "⌃",
    "Opt":   "⌥",
    "Shift": "⇧",
}
_WHITE_CGCOLOR = (CGColorCreateGenericRGB(1.0, 1.0, 1.0, 1.0)
                  if CGColorCreateGenericRGB is not None else None)

# Function-key keycodes (NSEvent.keyCode) — characters() doesn't return
# meaningful glyphs for these so we look them up by keyCode instead.
_FN_KEYCODES = {
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6",
    98: "F7", 100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12",
}

GRID_PADDING = 16
CELL_PADDING = 8
LCD_HEIGHT = 36                      # baseline height at default display font
LCD_DEFAULT_FONT_PT = 12.0           # display_font_size that LCD_HEIGHT was tuned for
STATUS_BAR_HEIGHT = 32
DOT_DIAMETER = 10
GREEN_RGB = (0.20, 0.80, 0.35, 1.0)
RED_RGB = (0.85, 0.20, 0.30, 1.0)

# Window background uses VHC's panel calibrated value (0.17 → renders as the
# medium gray that previously painted the macro cells). Macro squares now use
# sRGB #4a4a4a as the accent color.
WINDOW_BG = (0.17, 0.17, 0.17, 1.0)
LCD_BG = (0x1a / 255, 0x21 / 255, 0x17 / 255, 1.0)     # #1a2117
TEXT_DIM = (0.60, 0.60, 0.58, 1.0)
LCD_DIM = (0.55, 0.55, 0.50, 1.0)
LCD_ACCENT = (0.90, 0.78, 0.10, 1.0)  # warm yellow keeps the LCD readable
EMPTY_CELL_HEX = "#494949"
# Field bg (text inputs in the editor) keeps the macro-square accent value.
FIELD_BG = (0x49 / 255, 0x49 / 255, 0x49 / 255, 1.0)   # #494949


class AppController(NSObject):
    """Owns the window, grid, and macro store."""

    def init(self):
        self = super().init()
        if self is None:
            return None
        self._store = MacroStore()
        self._cell_buttons: dict = {}
        self._title_layers: dict = {}   # (row, col) -> CATextLayer
        self._hotkey_layers: dict = {}  # (row, col) -> CATextLayer
        # App-level undo / redo stacks. Each entry is a tuple
        #   (label, undo_callable, redo_callable_or_None)
        # New actions clear the redo stack — same convention as macOS apps.
        self._undo_stack: list = []
        self._redo_stack: list = []
        self._undo_max = 50
        # Set True while perform_undo/perform_redo runs; push_undo skips
        # while it's set so undo callables don't re-record themselves.
        self._in_undo = False
        videohub.set_mock_mode(self._store.grid.mock_videohub)
        videohub.set_enabled(self._store.grid.videohub_enabled)
        # Init the VHC notification bridge on the main thread before any
        # macro fire (which runs on a worker thread) can use it. Even when
        # videohub is disabled at startup, the bridge stays available so a
        # later toggle-on works without restarting the app.
        videohub.init_bridge()
        # Snapshot the Resolve project state (track enable + transforms) so
        # we can restore it on quit. Captured in a worker thread so a slow
        # Fusion bridge doesn't hold up the main window.
        self._initial_resolve_state: dict | None = None
        self._capture_initial_resolve_state_async()
        self._global_key_monitor = None
        self._build_window()
        # Apply persisted window/hotkey behaviors after the window exists.
        if self._store.grid.keep_on_top:
            self._apply_keep_on_top(True)
        if self._store.grid.global_hotkeys:
            self._apply_global_hotkeys(True)
        return self

    # -- Window ---------------------------------------------------------------

    @objc.python_method
    def _build_window(self) -> None:
        rows = self._store.grid.rows
        cols = self._store.grid.cols
        # "Natural" cell size used for the initial window. _relayout() then
        # scales cells to whatever the user resizes the window to.
        cell_w, cell_h = 140, 100
        win_w = GRID_PADDING * 2 + cols * cell_w + (cols - 1) * CELL_PADDING
        # LCD strip grows with the display font so big fonts stay centered
        # and the GUI gets pushed down rather than overlapped.
        self._lcd_height = self._lcd_height_for_font()
        top_strip = self._lcd_height + STATUS_BAR_HEIGHT + 8
        win_h = GRID_PADDING * 2 + rows * cell_h + (rows - 1) * CELL_PADDING + top_strip
        self._top_strip = top_strip

        style = (NSWindowStyleMaskTitled
                 | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskResizable)
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, win_w, win_h),
            style, NSBackingStoreBuffered, False,
        )
        self._window.setTitle_("MacroFlow")
        self._window.setReleasedWhenClosed_(False)
        # Enable Cmd+F (View → Toggle Full Screen). Without
        # NSWindowCollectionBehaviorFullScreenPrimary the AppKit selector
        # toggleFullScreen: is a no-op and the menu item stays disabled.
        try:
            current_behavior = int(self._window.collectionBehavior())
            self._window.setCollectionBehavior_(current_behavior | (1 << 7))
        except Exception:
            pass
        self._window.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*WINDOW_BG),
        )
        # Force dark appearance so calibrated colors render the same as VHC.
        # Without this, the window picks up the system theme and the calibrated
        # gamma curve resolves to lighter values.
        from AppKit import NSAppearance
        _dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        if _dark:
            self._window.setAppearance_(_dark)

        content = self._window.contentView()
        if content is None:
            return

        # Dark backing strip behind the LCD + status row, like VHC's header bar.
        top_bar = NSView.alloc().initWithFrame_(
            NSMakeRect(0, win_h - top_strip, win_w, top_strip),
        )
        top_bar.setWantsLayer_(True)
        if top_bar.layer() is not None:
            top_bar.layer().setBackgroundColor_(
                NSColor.colorWithSRGBRed_green_blue_alpha_(
                    0x13 / 255, 0x13 / 255, 0x13 / 255, 1.0,
                ).CGColor(),
            )
        content.addSubview_(top_bar)
        self._top_bar = top_bar

        # LCD strip (last fired macro / hover description). Full width.
        # Olive-on-dark to match Videohub Controller's title-bar LCD display.
        lcd_w = win_w - GRID_PADDING * 2
        lcd_strip_h = self._lcd_height - 6
        lcd_wrap = NSView.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING, win_h - self._lcd_height - 4,
                       lcd_w, lcd_strip_h),
        )
        lcd_wrap.setWantsLayer_(True)
        try:
            from Quartz import CGColorCreateGenericRGB as _cg
            lcd_wrap.layer().setBackgroundColor_(_cg(*LCD_BG))
        except Exception:
            pass
        if lcd_wrap.layer() is not None:
            lcd_wrap.layer().setCornerRadius_(4.0)
            lcd_wrap.layer().setMasksToBounds_(True)
        content.addSubview_(lcd_wrap)
        # Text field placed manually at the vertical centre of the strip.
        text_h = self._lcd_text_height_for_font()
        lcd = NSTextField.alloc().initWithFrame_(
            NSMakeRect(8, (lcd_strip_h - text_h) / 2,
                       lcd_w - 16, text_h),
        )
        lcd.setBezeled_(False)
        lcd.setBordered_(False)
        lcd.setDrawsBackground_(False)
        lcd.setEditable_(False)
        lcd.setSelectable_(False)
        lcd.setStringValue_("MacroFlow ready — click a cell to fire, Cmd+click to edit")
        lcd.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*LCD_ACCENT),
        )
        lcd.setFont_(NSFont.boldSystemFontOfSize_(12))
        if lcd.cell() is not None:
            lcd.cell().setUsesSingleLineMode_(True)
            lcd.cell().setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
        lcd_wrap.addSubview_(lcd)
        self._lcd = lcd
        self._lcd_idle_text = (
            "MacroFlow ready — click a cell to fire, Cmd+click to edit"
        )

        # Status bar — connection dots for Videohub Controller + Resolve.
        # Sits directly below the LCD, where the diagnostics panel used to be.
        bar_y = win_h - self._lcd_height - STATUS_BAR_HEIGHT - 6
        # DAVINCI RESOLVE first (left-most) so it stays left-justified when
        # the Videohub indicator is hidden via Settings.
        label_h = 16
        label_y = bar_y + (STATUS_BAR_HEIGHT - label_h) / 2
        self._rv_dot = NSView.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING, bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2,
                       DOT_DIAMETER, DOT_DIAMETER),
        )
        self._rv_dot.setWantsLayer_(True)
        if self._rv_dot.layer() is not None:
            self._rv_dot.layer().setCornerRadius_(DOT_DIAMETER / 2)
            self._rv_dot.layer().setMasksToBounds_(True)
        content.addSubview_(self._rv_dot)

        rv_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING + DOT_DIAMETER + 6, label_y, 130, label_h),
        )
        rv_label.setBezeled_(False)
        rv_label.setBordered_(False)
        rv_label.setDrawsBackground_(False)
        rv_label.setEditable_(False)
        rv_label.setSelectable_(False)
        rv_label.setStringValue_("DAVINCI RESOLVE")
        rv_label.setFont_(NSFont.boldSystemFontOfSize_(12))
        rv_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_DIM),
        )
        if rv_label.cell() is not None:
            rv_label.cell().setUsesSingleLineMode_(True)
        content.addSubview_(rv_label)

        vh_x = GRID_PADDING + DOT_DIAMETER + 6 + 140
        self._vh_dot = NSView.alloc().initWithFrame_(
            NSMakeRect(vh_x, bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2,
                       DOT_DIAMETER, DOT_DIAMETER),
        )
        self._vh_dot.setWantsLayer_(True)
        if self._vh_dot.layer() is not None:
            self._vh_dot.layer().setCornerRadius_(DOT_DIAMETER / 2)
            self._vh_dot.layer().setMasksToBounds_(True)
        content.addSubview_(self._vh_dot)

        vh_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(vh_x + DOT_DIAMETER + 6, label_y, 76, label_h),
        )
        vh_label.setBezeled_(False)
        vh_label.setBordered_(False)
        vh_label.setDrawsBackground_(False)
        vh_label.setEditable_(False)
        vh_label.setSelectable_(False)
        vh_label.setStringValue_("VIDEOHUB")
        vh_label.setFont_(NSFont.boldSystemFontOfSize_(12))
        vh_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_DIM),
        )
        if vh_label.cell() is not None:
            vh_label.cell().setUsesSingleLineMode_(True)
        content.addSubview_(vh_label)

        # Preset row — popup + Save + Delete on the right of the status bar,
        # styled like Videohub Controller's preset chooser. Pinned to the
        # right edge so a wider window leaves the buttons in place.
        BTN_W = 64
        BTN_H = 24
        POPUP_W = 200
        right_edge = win_w - GRID_PADDING
        row_y = bar_y + (STATUS_BAR_HEIGHT - BTN_H) / 2
        del_x = right_edge - BTN_W
        save_x = del_x - BTN_W - 6
        popup_x = save_x - POPUP_W - 6
        from AppKit import NSPopUpButton
        self._preset_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(popup_x, row_y, POPUP_W, BTN_H),
        )
        self._preset_popup.setTarget_(self)
        self._preset_popup.setAction_("presetChanged:")
        # Stick to right edge AND top edge (NSViewMinXMargin | NSViewMinYMargin)
        # so the row stays put when the window resizes in either axis.
        self._preset_popup.setAutoresizingMask_(1 | 8)
        content.addSubview_(self._preset_popup)
        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(save_x, row_y, BTN_W, BTN_H),
        )
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)
        save_btn.setTarget_(self)
        save_btn.setAction_("presetSave:")
        save_btn.setAutoresizingMask_(1 | 8)
        content.addSubview_(save_btn)
        del_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(del_x, row_y, BTN_W, BTN_H),
        )
        del_btn.setTitle_("Delete")
        del_btn.setBezelStyle_(1)
        del_btn.setTarget_(self)
        del_btn.setAction_("presetDelete:")
        del_btn.setAutoresizingMask_(1 | 8)
        content.addSubview_(del_btn)
        self._preset_save_btn = save_btn
        self._preset_delete_btn = del_btn
        self._refresh_preset_popup()

        # Initial status check + periodic refresh.
        self._set_status_dot(self._vh_dot, False)
        self._set_status_dot(self._rv_dot, False)
        self._refresh_status_dots()

        # Grid — start below the LCD and status bar.
        grid_top = win_h - self._lcd_height - STATUS_BAR_HEIGHT - 8
        for r in range(rows):
            for c in range(cols):
                x = GRID_PADDING + c * (cell_w + CELL_PADDING)
                y = grid_top - (r + 1) * cell_h - r * CELL_PADDING - CELL_PADDING
                self._build_cell(content, r, c, cols,
                                 NSMakeRect(x, y, cell_w, cell_h))
        self._refresh_cell_titles()
        self._apply_font_sizes()

        # Pin a minimum window size so cells stay usable, then subscribe to
        # NSWindowDidResize so cells/LCD/status bar reflow when the user
        # drags the corner.
        min_cell_w = 80 if cols <= 12 else 30
        min_cell_h = 60 if rows <= 12 else 24
        # Floor at the size needed to fit the LCD + status row + preset row
        # without anything overlapping or clipping (≈720×520 for a 4×4).
        min_w = max(720,
                    GRID_PADDING * 2 + cols * min_cell_w + (cols - 1) * CELL_PADDING)
        min_h = max(520,
                    GRID_PADDING * 2 + rows * min_cell_h
                    + (rows - 1) * CELL_PADDING + top_strip)
        self._window.setMinSize_((min_w, min_h))
        self._lcd_wrap = lcd_wrap
        self._status_views = {
            "vh_dot": self._vh_dot,
            "vh_label": vh_label,
            "rv_dot": self._rv_dot,
            "rv_label": rv_label,
        }
        # Honor the saved videohub_enabled flag for the initial paint.
        self._apply_videohub_visibility()
        from Foundation import NSNotificationCenter
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, "windowDidResize:", "NSWindowDidResizeNotification",
            self._window,
        )

        self._window.makeKeyAndOrderFront_(None)
        self._install_hotkey_monitor()

    @objc.python_method
    def _set_status_dot(self, dot_view, ok: bool) -> None:
        try:
            from Quartz import CGColorCreateGenericRGB
        except Exception:
            return
        layer = dot_view.layer()
        if layer is None:
            return
        rgb = GREEN_RGB if ok else RED_RGB
        layer.setBackgroundColor_(CGColorCreateGenericRGB(*rgb))

    @objc.python_method
    def _refresh_status_dots(self) -> None:
        """Background-thread probe of both backends; main-thread dot update.

        Resolve: round-trips a GetProjectManager() call so a stale cached
        handle (Resolve was running, then quit) shows red.
        Videohub: TCP-probes the saved router IP(s) from VHC's config. Was
        previously hitting 127.0.0.1:9990 which is the Blackmagic Videohub
        Daemon — that's a system service that's almost always running and
        has nothing to do with whether the actual router (or VHC) is up.
        """
        videohub_on = bool(self._store.grid.videohub_enabled)

        def _probe() -> None:
            vh_ok = False
            if videohub_on:
                try:
                    vh_ok = bool(videohub.is_alive())
                except Exception:
                    vh_ok = False
            try:
                rv_ok = bool(resolve.is_alive())
            except Exception:
                rv_ok = False

            def _apply() -> None:
                if videohub_on:
                    self._set_status_dot(self._vh_dot, vh_ok)
                self._set_status_dot(self._rv_dot, rv_ok)
                from PyObjCTools import AppHelper as _ah
                _ah.callLater(5.0, self._refresh_status_dots)

            AppHelper.callAfter(_apply)

        threading.Thread(target=_probe, daemon=True).start()

    @objc.python_method
    def _apply_videohub_visibility(self) -> None:
        """Show / hide the VIDEOHUB status dot + label per the master switch."""
        on = bool(self._store.grid.videohub_enabled)
        try:
            self._vh_dot.setHidden_(not on)
        except Exception:
            pass
        vh_label = self._status_views.get("vh_label") if hasattr(self, "_status_views") else None
        if vh_label is not None:
            try:
                vh_label.setHidden_(not on)
            except Exception:
                pass

    # -- Undo / redo ----------------------------------------------------------

    # -- Resolve project capture / restore ------------------------------------

    @objc.python_method
    def _capture_initial_resolve_state_async(self) -> None:
        def _run():
            try:
                info = resolve.safe_get_video_track_info() or []
                xforms = resolve.safe_get_video_track_transforms() or {}
            except Exception as e:
                print(f"[macroflow] capture initial Resolve state failed: {e}")
                return
            if not info and not xforms:
                return
            snap = {
                "tracks": {int(i["index"]): bool(i["enabled"]) for i in info},
                "transforms": {int(k): dict(v) for k, v in xforms.items()},
            }
            self._initial_resolve_state = snap
            print(
                f"[macroflow] captured initial Resolve state — "
                f"{len(snap['tracks'])} tracks, "
                f"{len(snap['transforms'])} transforms",
            )

        threading.Thread(target=_run, daemon=True).start()

    @objc.python_method
    def restore_initial_resolve_state(self) -> None:
        """Push the captured-on-launch Resolve state back, restoring track
        enable flags + transforms. Called from applicationWillTerminate_."""
        snap = getattr(self, "_initial_resolve_state", None)
        if not snap:
            return
        print("[macroflow] restoring initial Resolve project state")
        try:
            if snap.get("tracks"):
                resolve.safe_apply_track_state(snap["tracks"])
        except Exception as e:
            print(f"[macroflow] restore tracks failed: {e}")
        try:
            if snap.get("transforms"):
                resolve.safe_apply_video_track_transforms(snap["transforms"])
        except Exception as e:
            print(f"[macroflow] restore transforms failed: {e}")

    # -- Undo / redo ----------------------------------------------------------

    @objc.python_method
    def push_undo(self, label: str, undo_fn, redo_fn=None) -> None:
        """Record an undoable action. `undo_fn` reverts; `redo_fn` re-applies
        (optional — without it, a redo isn't offered after an undo)."""
        if self._in_undo:
            return  # Don't record anything while we're already undoing/redoing
        self._undo_stack.append((label, undo_fn, redo_fn))
        self._redo_stack.clear()
        if len(self._undo_stack) > self._undo_max:
            self._undo_stack.pop(0)

    @objc.python_method
    def perform_undo(self) -> None:
        if not self._undo_stack:
            try:
                self._lcd.setStringValue_("Nothing to undo")
            except Exception:
                pass
            return
        label, undo_fn, redo_fn = self._undo_stack.pop()
        self._in_undo = True
        try:
            undo_fn()
        except Exception as e:
            print(f"[undo] '{label}' failed: {e}")
        finally:
            self._in_undo = False
        try:
            self._lcd.setStringValue_(f"Undid: {label}")
        except Exception:
            pass
        if redo_fn is not None:
            self._redo_stack.append((label, undo_fn, redo_fn))

    @objc.python_method
    def perform_redo(self) -> None:
        if not self._redo_stack:
            try:
                self._lcd.setStringValue_("Nothing to redo")
            except Exception:
                pass
            return
        label, undo_fn, redo_fn = self._redo_stack.pop()
        self._in_undo = True
        try:
            if redo_fn is not None:
                redo_fn()
        except Exception as e:
            print(f"[redo] '{label}' failed: {e}")
        finally:
            self._in_undo = False
        try:
            self._lcd.setStringValue_(f"Redid: {label}")
        except Exception:
            pass
        self._undo_stack.append((label, undo_fn, redo_fn))

    @objc.python_method
    def set_videohub_enabled(self, enabled: bool) -> None:
        """Toggle the Videohub backend at runtime (called from Settings)."""
        prev = bool(self._store.grid.videohub_enabled)
        enabled = bool(enabled)
        if prev == enabled:
            return
        self._store.grid.videohub_enabled = enabled
        videohub.set_enabled(enabled)
        self._apply_videohub_visibility()
        # Re-mark presets that include Videohub actions so the user sees
        # which ones are partially neutered by the new state.
        try:
            self._refresh_preset_popup()
        except Exception:
            pass
        self._store.save()
        self.push_undo(
            f"Videohub backend {'on' if enabled else 'off'}",
            lambda: self.set_videohub_enabled(prev),
            lambda: self.set_videohub_enabled(enabled),
        )

    # -- Keep on Top + Global Hotkeys (Settings → Window & Hotkey Behavior) --

    @objc.python_method
    def _apply_keep_on_top(self, on: bool) -> None:
        from AppKit import NSFloatingWindowLevel, NSNormalWindowLevel
        try:
            self._window.setLevel_(
                NSFloatingWindowLevel if on else NSNormalWindowLevel,
            )
            print(f"[settings] Keep on Top: {'ON' if on else 'OFF'}")
        except Exception as e:
            print(f"[settings] keep-on-top failed: {e}")

    @objc.python_method
    def set_keep_on_top(self, on: bool) -> None:
        self._store.grid.keep_on_top = bool(on)
        self._apply_keep_on_top(bool(on))
        self._store.save()

    @objc.python_method
    def _is_accessibility_trusted(self) -> bool:
        try:
            import ctypes
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/"
                "ApplicationServices",
            )
            cf.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(cf.AXIsProcessTrusted())
        except Exception:
            return True  # if we can't check, assume yes

    @objc.python_method
    def _prompt_accessibility(self) -> bool:
        """Show the 'enable in Accessibility' alert. Returns True if user
        clicked Open Settings (we then can't actually verify until next
        toggle attempt), False otherwise."""
        from AppKit import NSAlert, NSAlertFirstButtonReturn, NSAppearance
        import subprocess
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Accessibility Permission Required")
        alert.setInformativeText_(
            "Global Hotkeys need Accessibility permission to capture "
            "keystrokes when MacroFlow is in the background.\n\n"
            "Click Open Settings, enable MacroFlow in the Accessibility "
            "list, then re-tick Global Hotkeys.",
        )
        alert.addButtonWithTitle_("Open Settings")
        alert.addButtonWithTitle_("Not Now")
        try:
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            if dark:
                alert.window().setAppearance_(dark)
        except Exception:
            pass
        if int(alert.runModal()) == NSAlertFirstButtonReturn:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?"
                "Privacy_Accessibility",
            ])
            return True
        return False

    @objc.python_method
    def _apply_global_hotkeys(self, on: bool) -> None:
        # Tear down any existing monitor first.
        if self._global_key_monitor is not None:
            try:
                NSEvent.removeMonitor_(self._global_key_monitor)
            except Exception:
                pass
            self._global_key_monitor = None
            print("[hotkeys] global monitor removed")
        if not on:
            return
        if not self._is_accessibility_trusted():
            self._prompt_accessibility()
            # Save flag false — user has to re-tick after granting permission.
            self._store.grid.global_hotkeys = False
            self._store.save()
            return

        def _global_handler(event):
            try:
                self._handle_global_hotkey_event(event)
            except Exception as e:
                print(f"[hotkeys] global handler: {e}")

        self._global_key_monitor = (
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, _global_handler,
            )
        )
        print("[hotkeys] global monitor installed")

    @objc.python_method
    def _handle_global_hotkey_event(self, event) -> None:
        """Match the event against macro hotkeys (same logic as the local
        monitor, minus arrow nav / text-edit suppression). Fires on the main
        thread via AppHelper.callAfter."""
        capture_mask = (
            NSEventModifierFlagCommand
            | NSEventModifierFlagControl
            | NSEventModifierFlagOption
            | NSEventModifierFlagShift
        )
        mod_to_flag = {
            "Cmd":   int(NSEventModifierFlagCommand),
            "Ctrl":  int(NSEventModifierFlagControl),
            "Opt":   int(NSEventModifierFlagOption),
            "Shift": int(NSEventModifierFlagShift),
        }
        event_mods = int(event.modifierFlags()) & int(capture_mask)
        keycode = int(event.keyCode())
        if keycode in _FN_KEYCODES:
            key = _FN_KEYCODES[keycode]
        else:
            chars = event.charactersIgnoringModifiers() or ""
            if not chars:
                return
            key = str(chars)[0].lower()
        rows = self._store.grid.rows
        cols = self._store.grid.cols
        for macro in self._store.grid.macros.values():
            if not macro.hotkey or macro.hotkey != key:
                continue
            required = mod_to_flag.get(
                getattr(macro, "hotkey_modifier", "") or "", 0,
            )
            if event_mods != required:
                continue
            try:
                r, c = (int(x) for x in macro.id.split(","))
            except ValueError:
                continue
            if r >= rows or c >= cols:
                continue
            print(f"[hotkeys] (global) '{key}' -> firing {macro.id}")
            AppHelper.callAfter(self._fire, r, c)
            return

    @objc.python_method
    def set_global_hotkeys(self, on: bool) -> None:
        on = bool(on)
        self._store.grid.global_hotkeys = on
        self._apply_global_hotkeys(on)
        # _apply_global_hotkeys may flip the flag back to False if perm denied.
        self._store.save()

    @objc.python_method
    def _install_hotkey_monitor(self) -> None:
        """Local monitor: when MacroFlow is the focused app, key presses fire
        the matching macro. Each macro can require a specific modifier
        (Cmd/Ctrl/Opt/Shift) — only events with EXACTLY that modifier active
        will fire. Macros with no modifier fire only on bare keypresses."""
        # Subset of modifiers we care about for matching.
        capture_mask = (
            NSEventModifierFlagCommand
            | NSEventModifierFlagControl
            | NSEventModifierFlagOption
            | NSEventModifierFlagShift
        )
        mod_to_flag = {
            "Cmd":   int(NSEventModifierFlagCommand),
            "Ctrl":  int(NSEventModifierFlagControl),
            "Opt":   int(NSEventModifierFlagOption),
            "Shift": int(NSEventModifierFlagShift),
        }

        def _handler(event):
            # Don't fire macros while the user is typing into a text field
            # (label / Settings sliders / transform fields / preset name).
            # The field editor for any NSTextField becomes the first responder
            # while editing, and it inherits from NSText.
            try:
                from AppKit import NSText
                key_window = NSApp.keyWindow()
                if key_window is not None:
                    fr = key_window.firstResponder()
                    if fr is not None and fr.isKindOfClass_(NSText):
                        return event  # let the field consume the keystroke
            except Exception:
                pass
            event_mods = int(event.modifierFlags()) & int(capture_mask)
            keycode = int(event.keyCode())
            if keycode in _FN_KEYCODES:
                key = _FN_KEYCODES[keycode]
            else:
                chars = event.charactersIgnoringModifiers() or ""
                if not chars:
                    return event
                # Grid keyboard nav — only when the main grid window is the
                # key window (so the editor's track table can still own Up/
                # Down/Left/Right/Enter when it's focused).
                if event_mods == 0 and NSApp.keyWindow() is self._window:
                    code = ord(chars[0])
                    arrow_map = {
                        0xF700: (-1, 0),  # up
                        0xF701: (+1, 0),  # down
                        0xF702: (0, -1),  # left
                        0xF703: (0, +1),  # right
                    }
                    if code in arrow_map:
                        dr, dc = arrow_map[code]
                        self._move_grid_selection(dr, dc)
                        return None  # consume
                    if chars in ("\r", "\n", "\x03"):
                        sel = getattr(self, "_selected_fire_key", None)
                        if sel is None:
                            sel = (0, 0)
                        r, c = sel
                        if (r < self._store.grid.rows
                                and c < self._store.grid.cols):
                            self._fire(r, c)
                            return None
                key = str(chars)[0].lower()
            grid_rows = self._store.grid.rows
            grid_cols = self._store.grid.cols
            for macro in self._store.grid.macros.values():
                if not macro.hotkey or macro.hotkey != key:
                    continue
                required = mod_to_flag.get(
                    getattr(macro, "hotkey_modifier", "") or "", 0,
                )
                # Exact-match on the modifier set: empty modifier requires
                # zero modifier flags; named modifier requires only that one.
                if event_mods != required:
                    continue
                try:
                    r, c = (int(x) for x in macro.id.split(","))
                except ValueError:
                    continue
                # Skip macros that fell outside the grid after a resize —
                # they're still in the store but no longer visible/clickable.
                if r >= grid_rows or c >= grid_cols:
                    continue
                mod_label = getattr(macro, "hotkey_modifier", "") or "none"
                print(f"[hotkey] '{key}' (mod={mod_label}) -> firing {macro.id}")
                self._fire(r, c)
                return None  # consume the event
            return event

        try:
            self._key_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, _handler,
            )
        except Exception as e:
            print(f"[hotkey] failed to install monitor: {e}")
            self._key_monitor = None

    @objc.python_method
    def _refresh_cell_titles(self) -> None:
        for (r, c), btn in self._cell_buttons.items():
            macro = self._store.grid.get(r, c)
            label_line, hotkey_glyph = self._title_for(macro, r, c)
            # Empty cells stay neutral; macros paint their saved color. A
            # custom (non-default) color also counts as "has content" so the
            # editor's color well shows a live preview on the cell.
            has_content = macro is not None and macro.color and (
                macro.label or macro.videohub.is_set() or macro.hotkey
                or macro.color != _DEFAULT_MACRO_COLOR
            )
            color_hex = macro.color if has_content else EMPTY_CELL_HEX
            # Clear the button's own title — both title and hotkey are
            # painted via CATextLayers we control directly.
            btn.setTitle_("")
            # Layer fill — persists across focus loss because we draw it
            # ourselves via Core Animation, not through the OS button bezel.
            layer = btn.layer()
            if layer is not None:
                s = color_hex.lstrip("#")
                if len(s) == 6:
                    try:
                        rr = int(s[0:2], 16) / 255.0
                        gg = int(s[2:4], 16) / 255.0
                        bb = int(s[4:6], 16) / 255.0
                        # Use TRUE sRGB so the input hex matches what's rendered.
                        # CGColorCreateGenericRGB uses calibrated GenericRGB
                        # (gamma 1.8), which brightens values by ~+0x13 vs sRGB.
                        layer.setBackgroundColor_(
                            NSColor.colorWithSRGBRed_green_blue_alpha_(
                                rr, gg, bb, 1.0,
                            ).CGColor(),
                        )
                    except Exception:
                        pass
            self._update_text_layers(r, c, btn, label_line, hotkey_glyph)

    @objc.python_method
    def _update_text_layers(self, row: int, col: int, btn,
                              label_line: str, hotkey_glyph: str) -> None:
        if CATextLayer is None:
            return
        layer = btn.layer()
        if layer is None:
            return
        key = (row, col)
        title_pt = float(self._store.grid.title_font_size)
        hotkey_pt = float(self._store.grid.hotkey_font_size)
        title = self._title_layers.get(key)
        if title is None:
            title = CATextLayer.layer()
            title.setAlignmentMode_("center")
            title.setForegroundColor_(_WHITE_CGCOLOR)
            title.setFont_("HelveticaNeue-Medium")
            try:
                title.setContentsScale_(2.0)
            except Exception:
                pass
            layer.addSublayer_(title)
            self._title_layers[key] = title
        title.setFontSize_(title_pt)
        hk = self._hotkey_layers.get(key)
        if hk is None:
            hk = CATextLayer.layer()
            hk.setAlignmentMode_("center")
            hk.setForegroundColor_(_WHITE_CGCOLOR)
            hk.setFont_("HelveticaNeue-Bold")
            try:
                hk.setContentsScale_(2.0)
            except Exception:
                pass
            layer.addSublayer_(hk)
            self._hotkey_layers[key] = hk
        hk.setFontSize_(hotkey_pt)
        title.setString_(label_line or "")
        # Display uppercase glyph; the macro's stored hotkey stays lowercase
        # for matching against keypresses.
        hk.setString_((hotkey_glyph or "").upper())
        self._position_text_layers(btn, title, hk)

    @objc.python_method
    def _lcd_height_for_font(self) -> int:
        """LCD strip height that scales with the display font so the text
        stays visually centered as it grows. Anchored at LCD_HEIGHT for
        font sizes <= LCD_DEFAULT_FONT_PT so the default look is unchanged."""
        try:
            pt = float(self._store.grid.display_font_size)
        except Exception:
            pt = LCD_DEFAULT_FONT_PT
        scale = max(1.0, pt / LCD_DEFAULT_FONT_PT)
        return int(LCD_HEIGHT * scale)

    @objc.python_method
    def _lcd_text_height_for_font(self) -> int:
        """Height of the LCD text field — must match the rendered glyph
        box so we can vertically center it in the strip."""
        try:
            pt = float(self._store.grid.display_font_size)
        except Exception:
            pt = LCD_DEFAULT_FONT_PT
        return max(18, int(pt * 1.4 + 4))

    @objc.python_method
    def _apply_font_sizes(self) -> None:
        """Push current font sizes from the store into the live UI.
        Wrapped in a CATransaction with implicit animations disabled so
        slider drags don't make the glyphs bounce around."""
        try:
            self._lcd.setFont_(NSFont.boldSystemFontOfSize_(
                float(self._store.grid.display_font_size),
            ))
        except Exception:
            pass
        # Grow the LCD strip + top strip so the display font stays centered
        # and the rest of the GUI gets pushed down rather than overlapped.
        new_lcd_h = self._lcd_height_for_font()
        if new_lcd_h != getattr(self, "_lcd_height", LCD_HEIGHT):
            self._lcd_height = new_lcd_h
            self._top_strip = new_lcd_h + STATUS_BAR_HEIGHT + 8
            try:
                self._relayout()
            except Exception:
                pass
        title_pt = float(self._store.grid.title_font_size)
        hotkey_pt = float(self._store.grid.hotkey_font_size)
        try:
            from Quartz import CATransaction
            CATransaction.begin()
            CATransaction.setDisableActions_(True)
        except Exception:
            CATransaction = None
        try:
            for key, layer in self._title_layers.items():
                try:
                    layer.setFontSize_(title_pt)
                except Exception:
                    pass
            for key, layer in self._hotkey_layers.items():
                try:
                    layer.setFontSize_(hotkey_pt)
                except Exception:
                    pass
            for (r, c), btn in self._cell_buttons.items():
                t = self._title_layers.get((r, c))
                h = self._hotkey_layers.get((r, c))
                if t is not None and h is not None:
                    self._position_text_layers(btn, t, h)
        finally:
            if CATransaction is not None:
                CATransaction.commit()

    @objc.python_method
    def _position_text_layers(self, btn, title_layer, hk_layer) -> None:
        # CATextLayer on a button uses flipped (top-left origin) coords.
        bw = float(btn.bounds().size.width)
        bh = float(btn.bounds().size.height)
        title_pt = float(self._store.grid.title_font_size)
        hotkey_pt = float(self._store.grid.hotkey_font_size)
        title_h = title_pt * 1.4 + 4
        hk_h = hotkey_pt * 1.4 + 4
        title_y = bh * 0.16
        title_centre = title_y + title_h / 2
        hk_centre = (title_centre + bh) / 2
        # Disable Core Animation's implicit animations — otherwise every
        # frame/font change animates over 0.25s and the text bounces around
        # while the user drags a slider.
        try:
            from Quartz import CATransaction
            CATransaction.begin()
            CATransaction.setDisableActions_(True)
            title_layer.setFrame_(NSMakeRect(0, title_y, bw, title_h))
            hk_layer.setFrame_(NSMakeRect(0, hk_centre - hk_h / 2, bw, hk_h))
            CATransaction.commit()
        except Exception:
            title_layer.setFrame_(NSMakeRect(0, title_y, bw, title_h))
            hk_layer.setFrame_(NSMakeRect(0, hk_centre - hk_h / 2, bw, hk_h))

    @objc.python_method
    @staticmethod
    def _title_for(macro: Macro | None, r: int, c: int) -> tuple[str, str]:
        """Returns (label_line, hotkey_glyph). Either may be empty."""
        intentional = (
            macro is not None
            and (bool(macro.label) or bool(macro.hotkey) or macro.videohub.is_set())
        )
        if not intentional:
            return (f"R{r+1}C{c+1}", "")
        # Prepend the modifier glyph (⌘ ⌃ ⌥ ⇧) if the macro requires one.
        glyph = _MODIFIER_GLYPH.get(getattr(macro, "hotkey_modifier", "") or "", "")
        hotkey_str = f"{glyph}{macro.hotkey}" if (glyph and macro.hotkey) else (macro.hotkey or "")
        return (macro.label or f"R{r+1}C{c+1}", hotkey_str)

    # -- Grid construction / resize -----------------------------------------

    @objc.python_method
    def _build_cell(self, content, r: int, c: int, cols: int, frame) -> None:
        # Borderless + layer-backed so the macro's color paints regardless
        # of focus (setBezelColor reverts on focus loss). Subclassed so
        # right-click opens the editor (forwarded to cellRightClicked_).
        btn = _MacroCellButton.alloc().initWithFrame_(frame)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        layer = btn.layer()
        if layer is not None:
            layer.setCornerRadius_(8.0)
            layer.setMasksToBounds_(True)
        tag = r * cols + c
        btn.setTag_(tag)
        btn.setTarget_(self)
        btn.setAction_("cellClicked:")
        cell = btn.cell()
        if cell is not None:
            cell.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
            cell.setWraps_(True)
        opts = (NSTrackingMouseEnteredAndExited
                | NSTrackingActiveInKeyWindow
                | NSTrackingInVisibleRect)
        ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            btn.bounds(), opts, self, {"tag": str(tag)},
        )
        btn.addTrackingArea_(ta)
        content.addSubview_(btn)
        self._cell_buttons[(r, c)] = btn

    @objc.python_method
    def apply_grid_size(self, rows: int, cols: int) -> None:
        """Live-resize the grid (e.g. 4x4 → 12x12) without restarting."""
        if rows <= 0 or cols <= 0:
            return
        if (rows, cols) == (self._store.grid.rows, self._store.grid.cols):
            return
        prev_rows, prev_cols = self._store.grid.rows, self._store.grid.cols
        new_rows, new_cols = int(rows), int(cols)
        content = self._window.contentView()
        if content is None:
            return
        # Tear down existing cells and their text sublayers.
        for btn in self._cell_buttons.values():
            try:
                btn.removeFromSuperview()
            except Exception:
                pass
        self._cell_buttons.clear()
        self._title_layers.clear()
        self._hotkey_layers.clear()
        # The previously-outlined "selected" cell is gone now too.
        self._selected_fire_key: tuple[int, int] | None = None
        self._store.grid.rows = int(rows)
        self._store.grid.cols = int(cols)
        # Rebuild cells at provisional sizes; _relayout immediately reflows.
        cell_w, cell_h = 140.0, 100.0
        for r in range(rows):
            for c in range(cols):
                self._build_cell(
                    content, r, c, cols,
                    NSMakeRect(0, 0, cell_w, cell_h),
                )
        # Tighten the window's minimum so it can't be shrunk below the new
        # grid's usable size. Cell minimums scale down for very large grids
        # (e.g. 40x40) so the window doesn't insist on being wider than the
        # screen. Hard floor matches the LCD + status + preset rows.
        min_cell_w = 80 if cols <= 12 else 30
        min_cell_h = 60 if rows <= 12 else 24
        min_w = max(720,
                    GRID_PADDING * 2 + cols * min_cell_w + (cols - 1) * CELL_PADDING)
        min_h = max(520,
                    GRID_PADDING * 2 + rows * min_cell_h
                    + (rows - 1) * CELL_PADDING + self._top_strip)
        try:
            self._window.setMinSize_((min_w, min_h))
        except Exception:
            pass
        # If the current window is smaller than the new minimum, grow it.
        frame = self._window.frame()
        new_w = max(frame.size.width, min_w)
        new_h = max(frame.size.height, min_h)
        if (new_w, new_h) != (frame.size.width, frame.size.height):
            self._window.setContentSize_((new_w, new_h))
        self._refresh_cell_titles()
        self._relayout()
        self._apply_font_sizes()
        self._store.save()
        self.push_undo(
            f"grid {prev_rows}×{prev_cols} → {new_rows}×{new_cols}",
            lambda: self.apply_grid_size(prev_rows, prev_cols),
            lambda: self.apply_grid_size(new_rows, new_cols),
        )

    # -- Presets --------------------------------------------------------------

    @staticmethod
    def _preset_uses_videohub(snap: dict) -> bool:
        """True if any macro in the snapshot has a Videohub action set."""
        for m in (snap.get("macros") or {}).values():
            vh = m.get("videohub") or {}
            if vh.get("device_id") and vh.get("preset_name"):
                return True
        return False

    @objc.python_method
    def _refresh_preset_popup(self) -> None:
        if not hasattr(self, "_preset_popup"):
            return
        names = sorted(self._store.grid.presets.keys())
        self._preset_popup.removeAllItems()
        if names:
            self._preset_popup.addItemWithTitle_("(select preset)")
        else:
            self._preset_popup.addItemWithTitle_("(no presets)")
        for n in names:
            snap = self._store.grid.presets.get(n) or {}
            # Always mark presets that include Videohub macros so the user
            # knows recall will turn the Videohub backend ON. Presets without
            # the marker will turn it OFF on recall.
            label = (f"{n}  •  uses Videohub"
                     if self._preset_uses_videohub(snap) else n)
            self._preset_popup.addItemWithTitle_(label)
        self._preset_popup.selectItemAtIndex_(0)
        try:
            self._preset_delete_btn.setEnabled_(bool(names))
        except Exception:
            pass

    def presetChanged_(self, sender) -> None:  # NOQA: N802
        idx = int(sender.indexOfSelectedItem())
        names = sorted(self._store.grid.presets.keys())
        if idx <= 0 or idx > len(names):
            return
        # Look up by index — the displayed title may include a suffix
        # ("• needs Videohub") so we can't reverse it from the title.
        self._recall_preset(names[idx - 1])

    def presetSave_(self, sender) -> None:  # NOQA: N802
        from AppKit import (
            NSAlert,
            NSAlertFirstButtonReturn,
            NSMakeRect,
            NSTextField,
        )
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Save Preset")
        alert.setInformativeText_(
            "Snapshot the current grid (rows, cols, all macros) as a named "
            "preset. Saving over an existing name overwrites it.",
        )
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        # Suggest an unused default name.
        n = 1
        while f"Preset {n}" in self._store.grid.presets:
            n += 1
        tf = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 24))
        tf.setStringValue_(f"Preset {n}")
        alert.setAccessoryView_(tf)
        try:
            from AppKit import NSAppearance
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            if dark:
                alert.window().setAppearance_(dark)
        except Exception:
            pass
        if int(alert.runModal()) != NSAlertFirstButtonReturn:
            return
        name = str(tf.stringValue() or "").strip()
        if not name:
            return
        self._store.grid.presets[name] = self._store.snapshot_current()
        self._store.save()
        self._refresh_preset_popup()
        # Re-select the just-saved entry.
        names = sorted(self._store.grid.presets.keys())
        if name in names:
            self._preset_popup.selectItemAtIndex_(names.index(name) + 1)
        self._lcd.setStringValue_(f"Preset saved: {name}")

    def presetDelete_(self, sender) -> None:  # NOQA: N802
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        idx = int(self._preset_popup.indexOfSelectedItem())
        names = sorted(self._store.grid.presets.keys())
        if idx <= 0 or idx > len(names):
            return
        name = names[idx - 1]
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Delete preset \"{name}\"?")
        alert.setInformativeText_("Cmd+Z restores it.")
        alert.addButtonWithTitle_("Delete")
        alert.addButtonWithTitle_("Cancel")
        try:
            from AppKit import NSAppearance
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            if dark:
                alert.window().setAppearance_(dark)
        except Exception:
            pass
        if int(alert.runModal()) != NSAlertFirstButtonReturn:
            return
        # Snapshot the preset before removal so undo can put it back.
        prev_snap = dict(self._store.grid.presets.get(name) or {})
        self._store.grid.presets.pop(name, None)
        self._store.save()
        self._refresh_preset_popup()
        self._lcd.setStringValue_(f"Preset deleted: {name}")

        def _undo():
            self._store.grid.presets[name] = dict(prev_snap)
            self._store.save()
            self._refresh_preset_popup()

        def _redo():
            self._store.grid.presets.pop(name, None)
            self._store.save()
            self._refresh_preset_popup()

        self.push_undo(f"delete preset '{name}'", _undo, _redo)

    @objc.python_method
    def _recall_preset(self, name: str) -> None:
        snap = self._store.grid.presets.get(name)
        if not snap:
            return
        # Snapshot the grid BEFORE recall so undo can put it back exactly,
        # including the videohub_enabled state and dimensions.
        pre_snap = {
            "rows": self._store.grid.rows,
            "cols": self._store.grid.cols,
            "videohub_enabled": bool(self._store.grid.videohub_enabled),
            "macros": {mid: m.to_dict() for mid, m in self._store.grid.macros.items()},
        }
        rows = int(snap.get("rows", self._store.grid.rows))
        cols = int(snap.get("cols", self._store.grid.cols))
        # The preset dictates the Videohub backend state. If the preset
        # uses Videohub, we enable it. If it doesn't, we disable it. No-op
        # when the requested state already matches.
        needs_vh = self._preset_uses_videohub(snap)
        if needs_vh != bool(self._store.grid.videohub_enabled):
            self.set_videohub_enabled(needs_vh)
        # Resize the grid first if needed (rebuilds cells).
        if (rows, cols) != (self._store.grid.rows, self._store.grid.cols):
            self.apply_grid_size(rows, cols)
        # Replace the in-memory macros wholesale with the preset's snapshot.
        self._store.grid.macros = {
            mid: Macro.from_dict(m) for mid, m in (snap.get("macros") or {}).items()
        }
        self._store.save()
        self._refresh_cell_titles()
        suffix = " (Videohub on)" if needs_vh else " (Videohub off)"
        self._lcd.setStringValue_(f"Recalled preset: {name}{suffix}")

        def _undo():
            # Restore videohub state, grid size, and macros from the
            # pre-recall snapshot.
            if (int(pre_snap["rows"]), int(pre_snap["cols"])) != (
                self._store.grid.rows, self._store.grid.cols,
            ):
                self.apply_grid_size(int(pre_snap["rows"]), int(pre_snap["cols"]))
            if bool(pre_snap["videohub_enabled"]) != bool(self._store.grid.videohub_enabled):
                self.set_videohub_enabled(bool(pre_snap["videohub_enabled"]))
            self._store.grid.macros = {
                mid: Macro.from_dict(m)
                for mid, m in (pre_snap["macros"] or {}).items()
            }
            self._store.save()
            self._refresh_cell_titles()

        self.push_undo(
            f"recall preset '{name}'",
            _undo,
            lambda: self._recall_preset(name),
        )

    # -- Cell interactions ----------------------------------------------------

    @objc.python_method
    def _row_col_from_tag(self, tag: int) -> tuple[int, int]:
        cols = self._store.grid.cols
        return (tag // cols, tag % cols)

    def mouseEntered_(self, event) -> None:  # NOQA: N802
        info = event.trackingArea().userInfo() or {}
        try:
            tag = int(str(info.get("tag", "")))
        except ValueError:
            return
        row, col = self._row_col_from_tag(tag)
        macro = self._store.grid.get(row, col)
        self._lcd.setStringValue_(self._hover_text(macro, row, col))

    def mouseExited_(self, event) -> None:  # NOQA: N802
        self._lcd.setStringValue_(self._lcd_idle_text)

    @objc.python_method
    def _hover_text(self, macro: Macro | None, row: int, col: int) -> str:
        if macro is None or (not macro.label
                              and not macro.videohub.is_set()
                              and not macro.resolve.is_set()
                              and not macro.hotkey):
            return f"R{row+1}C{col+1} — empty"
        bits: list[str] = [macro.label or f"R{row+1}C{col+1}"]
        if macro.hotkey:
            bits.append(f"hotkey [{macro.hotkey.upper()}]")
        if macro.videohub.is_set():
            bits.append(f"VH: {macro.videohub.preset_name}")
        if macro.resolve.is_set():
            on = sum(1 for v in macro.resolve.tracks.values() if v)
            off = sum(1 for v in macro.resolve.tracks.values() if not v)
            bits.append(f"Resolve: {on} on / {off} off")
        return "  •  ".join(bits)

    def windowDidResize_(self, notification) -> None:  # NOQA: N802
        self._relayout()

    @objc.python_method
    def _relayout(self) -> None:
        content = self._window.contentView()
        if content is None:
            return
        win_w = float(content.frame().size.width)
        win_h = float(content.frame().size.height)
        rows = self._store.grid.rows
        cols = self._store.grid.cols

        # Top dark backing strip.
        if hasattr(self, "_top_bar") and self._top_bar is not None:
            self._top_bar.setFrame_(
                NSMakeRect(0, win_h - self._top_strip, win_w, self._top_strip),
            )

        # LCD strip — full width. Height tracks the display font size so big
        # fonts stay vertically centered with breathing room.
        lcd_h = getattr(self, "_lcd_height", LCD_HEIGHT)
        lcd_w = win_w - GRID_PADDING * 2
        lcd_strip_h = lcd_h - 6
        self._lcd_wrap.setFrame_(
            NSMakeRect(GRID_PADDING, win_h - lcd_h - 4, lcd_w, lcd_strip_h),
        )
        # Re-center the text field inside the wrapper.
        text_h = self._lcd_text_height_for_font()
        self._lcd.setFrame_(
            NSMakeRect(8, (lcd_strip_h - text_h) / 2, lcd_w - 16, text_h),
        )

        # Status bar.
        bar_y = win_h - lcd_h - STATUS_BAR_HEIGHT - 6
        label_h = 16
        label_y = bar_y + (STATUS_BAR_HEIGHT - label_h) / 2
        dot_y = bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2
        # Resolve on the left, Videohub on the right (so disabling Videohub
        # leaves Resolve still left-justified).
        self._status_views["rv_dot"].setFrame_(
            NSMakeRect(GRID_PADDING, dot_y, DOT_DIAMETER, DOT_DIAMETER),
        )
        self._status_views["rv_label"].setFrame_(
            NSMakeRect(GRID_PADDING + DOT_DIAMETER + 6, label_y, 130, label_h),
        )
        vh_x = GRID_PADDING + DOT_DIAMETER + 6 + 140
        self._status_views["vh_dot"].setFrame_(
            NSMakeRect(vh_x, dot_y, DOT_DIAMETER, DOT_DIAMETER),
        )
        self._status_views["vh_label"].setFrame_(
            NSMakeRect(vh_x + DOT_DIAMETER + 6, label_y, 76, label_h),
        )

        # Preset row (popup + Save + Delete) shares the status-bar y-band on the
        # right. Autoresizing only handles window-size changes, so we
        # reposition explicitly when the LCD strip grows.
        BTN_W = 64
        BTN_H = 24
        POPUP_W = 200
        right_edge = win_w - GRID_PADDING
        row_y = bar_y + (STATUS_BAR_HEIGHT - BTN_H) / 2
        del_x = right_edge - BTN_W
        save_x = del_x - BTN_W - 6
        popup_x = save_x - POPUP_W - 6
        if hasattr(self, "_preset_popup"):
            self._preset_popup.setFrame_(NSMakeRect(popup_x, row_y, POPUP_W, BTN_H))
        if hasattr(self, "_preset_save_btn"):
            self._preset_save_btn.setFrame_(NSMakeRect(save_x, row_y, BTN_W, BTN_H))
        if hasattr(self, "_preset_delete_btn"):
            self._preset_delete_btn.setFrame_(NSMakeRect(del_x, row_y, BTN_W, BTN_H))

        # Grid cells fill the remaining area, dividing it evenly.
        avail_w = win_w - GRID_PADDING * 2 - (cols - 1) * CELL_PADDING
        avail_h = (win_h - self._top_strip - GRID_PADDING - CELL_PADDING - (rows - 1) * CELL_PADDING)
        cell_w = max(1.0, avail_w / cols)
        cell_h = max(1.0, avail_h / rows)
        grid_top = win_h - lcd_h - STATUS_BAR_HEIGHT - 8
        for (r, c), btn in self._cell_buttons.items():
            x = GRID_PADDING + c * (cell_w + CELL_PADDING)
            y = grid_top - (r + 1) * cell_h - r * CELL_PADDING - CELL_PADDING
            btn.setFrame_(NSMakeRect(x, y, cell_w, cell_h))
            title = self._title_layers.get((r, c))
            hk = self._hotkey_layers.get((r, c))
            if title is not None and hk is not None:
                self._position_text_layers(btn, title, hk)
            # Keep the selected cell's inner 1px black ring sized to the cell.
            if (r, c) == getattr(self, "_selected_fire_key", None):
                ring = getattr(self, "_selection_inner_ring", None)
                if ring is not None:
                    try:
                        ring.setFrame_(NSMakeRect(1.0, 1.0,
                                                  cell_w - 2.0, cell_h - 2.0))
                    except Exception:
                        pass

    def cellClicked_(self, sender) -> None:  # NOQA: N802
        row, col = self._row_col_from_tag(int(sender.tag()))
        # Cmd-click or Control-click opens the editor; plain click fires.
        flags = 0
        evt = NSApp.currentEvent()
        if evt is not None:
            flags = int(evt.modifierFlags())
        if flags & (NSEventModifierFlagCommand | NSEventModifierFlagControl):
            self._open_editor(row, col)
        else:
            self._fire(row, col)

    def cellRightClicked_(self, sender) -> None:  # NOQA: N802
        # Right-click opens the editor for that cell (alongside Cmd+/Ctrl+click).
        row, col = self._row_col_from_tag(int(sender.tag()))
        # Mark it as the "selected" cell so Edit > Edit Macro targets it too.
        self._flash_cell(row, col)
        self._open_editor(row, col)

    @objc.python_method
    def edit_selected_cell(self) -> None:
        """Open the editor for the cell the user most recently
        fired/right-clicked. If no cell has been touched yet, default to (0,0)."""
        sel = getattr(self, "_selected_fire_key", None)
        if sel is None:
            sel = (0, 0)
        r, c = sel
        if r < self._store.grid.rows and c < self._store.grid.cols:
            self._open_editor(r, c)

    @objc.python_method
    def _fire(self, row: int, col: int) -> None:
        # Outline the cell first — even if it's empty, the click still
        # selects it (so subsequent Edit > Edit Macro / Cmd+E targets it).
        self._flash_cell(row, col)
        macro = self._store.grid.get(row, col)
        if macro is None:
            self._lcd.setStringValue_(f"R{row+1}C{col+1} is empty")
            return
        self._lcd.setStringValue_(f"Firing: {macro.label or macro.id} ...")

        def _bg() -> None:
            results = macro.fire()
            AppHelper.callAfter(self._fire_done, macro, results)

        threading.Thread(target=_bg, daemon=True).start()

    @objc.python_method
    def _move_grid_selection(self, dr: int, dc: int) -> None:
        """Move the keyboard-driven selection (the white-on-black outline)
        by (dr, dc) cells. Horizontal moves WRAP — arrowing past the right
        edge goes to the leftmost column on the same row, and vice-versa.
        Vertical moves clamp at the grid edges. Defaults to (0,0) if nothing
        was selected yet."""
        cur = getattr(self, "_selected_fire_key", None)
        if cur is None:
            cur = (0, 0)
        r, c = cur
        rows = self._store.grid.rows
        cols = self._store.grid.cols
        new_r = max(0, min(rows - 1, r + dr))
        new_c = (c + dc) % cols if cols > 0 else 0
        if (new_r, new_c) == (r, c) and getattr(self, "_selected_fire_key", None):
            return
        self._flash_cell(new_r, new_c)

    @objc.python_method
    def _flash_cell(self, row: int, col: int) -> None:
        """Mark the just-fired cell as 'selected' with a two-tone outline:
        1px 60%-white on the outside, 1px black inset 1px inward. Persists
        until another cell is selected.
        """
        if CGColorCreateGenericRGB is None:
            return
        # Clear the prior selection's outline + drop the inner ring sublayer.
        prev = getattr(self, "_selected_fire_key", None)
        if prev is not None and prev != (row, col):
            prev_btn = self._cell_buttons.get(prev)
            if prev_btn is not None:
                prev_layer = prev_btn.layer()
                if prev_layer is not None:
                    try:
                        prev_layer.setBorderWidth_(0.0)
                        prev_layer.setBorderColor_(None)
                    except Exception:
                        pass
        inner = getattr(self, "_selection_inner_ring", None)
        if inner is not None:
            try:
                inner.removeFromSuperlayer()
            except Exception:
                pass
            self._selection_inner_ring = None

        btn = self._cell_buttons.get((row, col))
        if btn is None:
            return
        layer = btn.layer()
        if layer is None:
            return
        try:
            # Outer 1px ring at 60% white.
            layer.setBorderWidth_(1.0)
            layer.setBorderColor_(CGColorCreateGenericRGB(1.0, 1.0, 1.0, 0.60))
        except Exception:
            return

        # Inner 1px black ring, inset 1px from the outer border.
        try:
            from Quartz import CALayer
            ring = CALayer.layer()
            ring.setBorderWidth_(1.0)
            ring.setBorderColor_(CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0))
            b = layer.bounds()
            ring.setFrame_(NSMakeRect(
                1.0, 1.0,
                float(b.size.width) - 2.0,
                float(b.size.height) - 2.0,
            ))
            try:
                ring.setCornerRadius_(max(0.0, float(layer.cornerRadius()) - 1.0))
                ring.setMasksToBounds_(True)
            except Exception:
                pass
            # Insert below the title/hotkey CATextLayers so it never covers them.
            layer.insertSublayer_atIndex_(ring, 0)
            self._selection_inner_ring = ring
        except Exception:
            pass

        self._selected_fire_key = (row, col)

    @objc.python_method
    def _fire_done(self, macro: Macro, results: dict) -> None:
        if not results:
            self._lcd.setStringValue_(f"{macro.label or macro.id} — (no actions)")
            return
        parts: list[str] = []
        if "videohub" in results:
            preset = (videohub.LAST_RECALL or {}).get("preset_name", "")
            if preset:
                parts.append(f"VH '{preset}'")
        if "resolve" in results:
            la = resolve.LAST_APPLY or {}
            flipped = la.get("flipped") or []
            unchanged = la.get("unchanged") or []
            if flipped:
                parts.append("Resolve " + " ".join(
                    f"V{i}{'↑' if en else '↓'}" for i, en in flipped))
            elif unchanged:
                parts.append(f"Resolve already-set ({len(unchanged)})")
        tail = " • ".join(parts) if parts else "(no actions)"
        self._lcd.setStringValue_(f"{macro.label or macro.id} — {tail}")

    @objc.python_method
    def _open_editor(self, row: int, col: int) -> None:
        editor = MacroEditorWindow.alloc().initWithController_row_col_(
            self, row, col,
        )
        editor.makeKeyAndOrderFront_(None)
        if not hasattr(self, "_editors"):
            self._editors = []
        self._editors.append(editor)

    @objc.python_method
    def _export_settings(self) -> None:
        panel = NSSavePanel.savePanel()
        panel.setTitle_("Export MacroFlow Settings")
        panel.setNameFieldStringValue_("macroflow.json")
        panel.setAllowedFileTypes_(["json"])
        if int(panel.runModal()) != 1:  # NSModalResponseOK
            return
        url = panel.URL()
        if url is None:
            return
        dest = str(url.path())
        try:
            import json
            from macroflow.macro import CONFIG_PATH
            data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            with open(dest, "w") as f:
                json.dump(data, f, indent=2)
            self._lcd.setStringValue_(f"Exported settings to {dest}")
        except Exception as e:
            self._lcd.setStringValue_(f"Export failed: {e}")

    @objc.python_method
    def _import_settings(self) -> None:
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Import MacroFlow Settings")
        panel.setAllowsMultipleSelection_(False)
        panel.setCanChooseDirectories_(False)
        panel.setAllowedFileTypes_(["json"])
        if int(panel.runModal()) != 1:
            return
        urls = panel.URLs()
        if not urls:
            return
        src = str(urls[0].path())
        try:
            import json
            from macroflow.macro import (
                CONFIG_PATH,
                MacroStore,
                atomic_write_shared_json,
            )
            data = json.loads(open(src).read())
            atomic_write_shared_json(CONFIG_PATH, data)
            # Reload into our running store and refresh the UI in place.
            self._store = MacroStore()
            videohub.set_mock_mode(self._store.grid.mock_videohub)
            self._refresh_cell_titles()
            self._lcd.setStringValue_(f"Imported settings from {src}")
        except Exception as e:
            self._lcd.setStringValue_(f"Import failed: {e}")

    @objc.python_method
    def _on_macro_saved(self, row: int, col: int, macro: Macro) -> None:
        empty = (not macro.label
                 and not macro.videohub.is_set()
                 and not macro.resolve.is_set())
        if empty:
            self._store.grid.clear(row, col)
        else:
            self._store.grid.set(row, col, macro)
        self._store.save()
        self._refresh_cell_titles()


class _AppDelegate(NSObject):
    def applicationShouldTerminateAfterLastWindowClosed_(self, app):  # NOQA: N802
        # Don't quit when an editor (or settings/about) is closed while
        # the main window happens to be hidden. The user quits via Cmd+Q.
        return False

    def applicationWillTerminate_(self, notif):  # NOQA: N802
        # On Cmd+Q / menu Quit, restore the DaVinci Resolve project to the
        # state we captured when MacroFlow launched (track enable flags +
        # per-track transforms). Each safe_apply_* call is bounded by a
        # 5-second timeout, so worst-case quit-restore is ~10 seconds.
        if _APP_CONTROLLER is not None:
            try:
                _APP_CONTROLLER.restore_initial_resolve_state()
            except Exception as e:
                print(f"[macroflow] quit-restore failed: {e}")

    def exportSettings_(self, sender):  # NOQA: N802
        if _APP_CONTROLLER is not None:
            _APP_CONTROLLER._export_settings()

    def importSettings_(self, sender):  # NOQA: N802
        if _APP_CONTROLLER is not None:
            _APP_CONTROLLER._import_settings()

    def showAboutWindow_(self, sender):  # NOQA: N802
        from macroflow.about_window import show_about_window
        show_about_window()

    def showSettingsWindow_(self, sender):  # NOQA: N802
        if _APP_CONTROLLER is None:
            return
        from macroflow.settings_window import show_settings_window
        show_settings_window(_APP_CONTROLLER)

    def showHelpWindow_(self, sender):  # NOQA: N802
        from macroflow.help_window import show_help_window
        show_help_window()

    def undo_(self, sender):  # NOQA: N802
        # Edit menu → Undo (Cmd+Z). NSText fields handle their own undo first
        # via the responder chain, so we only see this when no text editor
        # has focus (i.e., the LCD strip / cell grid).
        if _APP_CONTROLLER is not None:
            _APP_CONTROLLER.perform_undo()

    def editMacro_(self, sender):  # NOQA: N802
        # Edit menu → Edit Macro… opens the editor for the most recently
        # fired / right-clicked cell (or 0,0 if nothing's been touched).
        if _APP_CONTROLLER is not None:
            _APP_CONTROLLER.edit_selected_cell()

    def redo_(self, sender):  # NOQA: N802
        if _APP_CONTROLLER is not None:
            _APP_CONTROLLER.perform_redo()

    def showConsoleWindow_(self, sender):  # NOQA: N802
        from macroflow.console_window import show_console_window
        show_console_window()


def _build_main_menu(app, app_name: str = "MacroFlow") -> None:
    menubar = NSMenu.alloc().init()

    # App menu — both the NSMenu's title AND the parent NSMenuItem's title
    # need to be set explicitly so AppKit displays "MacroFlow" in the bold
    # app menu position instead of falling back to CFBundleName ("Python"
    # when running from source).
    app_item = NSMenuItem.alloc().init()
    app_item.setTitle_(app_name)
    menubar.addItem_(app_item)
    app_menu = NSMenu.alloc().initWithTitle_(app_name)
    app_menu.addItemWithTitle_action_keyEquivalent_(
        f"About {app_name}", "showAboutWindow:", "",
    )
    app_menu.addItem_(NSMenuItem.separatorItem())
    settings_item = app_menu.addItemWithTitle_action_keyEquivalent_(
        "Settings…", "showSettingsWindow:", ",",
    )
    settings_item.setKeyEquivalentModifierMask_(1 << 20)  # Cmd+,
    app_menu.addItem_(NSMenuItem.separatorItem())
    hide_item = app_menu.addItemWithTitle_action_keyEquivalent_(
        f"Hide {app_name}", "hide:", "h",
    )
    hide_item.setKeyEquivalentModifierMask_(1 << 20)  # Cmd
    app_menu.addItem_(NSMenuItem.separatorItem())
    quit_item = app_menu.addItemWithTitle_action_keyEquivalent_(
        f"Quit {app_name}", "terminate:", "q",
    )
    quit_item.setKeyEquivalentModifierMask_(1 << 20)
    app_item.setSubmenu_(app_menu)

    # File menu
    file_item = NSMenuItem.alloc().init()
    menubar.addItem_(file_item)
    file_menu = NSMenu.alloc().initWithTitle_("File")
    export_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Export Settings…", "exportSettings:", "e",
    )
    export_item.setKeyEquivalentModifierMask_(1 << 17 | 1 << 20)  # Shift+Cmd
    file_menu.addItem_(export_item)
    import_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Import Settings…", "importSettings:", "i",
    )
    import_item.setKeyEquivalentModifierMask_(1 << 17 | 1 << 20)
    file_menu.addItem_(import_item)
    file_item.setSubmenu_(file_menu)

    # Edit menu — gives Cmd+C/V/X/A inside the macro editor's text fields.
    edit_item = NSMenuItem.alloc().init()
    menubar.addItem_(edit_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Undo", "undo:", "z")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Redo", "redo:", "Z")
    edit_menu.addItem_(NSMenuItem.separatorItem())
    edit_menu.addItemWithTitle_action_keyEquivalent_("Cut", "cut:", "x")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Copy", "copy:", "c")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Paste", "paste:", "v")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Select All", "selectAll:", "a")
    edit_menu.addItem_(NSMenuItem.separatorItem())
    em_item = edit_menu.addItemWithTitle_action_keyEquivalent_(
        "Edit Macro…", "editMacro:", "e",
    )
    em_item.setKeyEquivalentModifierMask_(1 << 20)  # Cmd
    edit_item.setSubmenu_(edit_menu)

    # View menu — Cmd+F toggles native macOS fullscreen, which restores the
    # previous window size when toggled back off.
    view_item = NSMenuItem.alloc().init()
    menubar.addItem_(view_item)
    view_menu = NSMenu.alloc().initWithTitle_("View")
    fs_item = view_menu.addItemWithTitle_action_keyEquivalent_(
        "Toggle Full Screen", "toggleFullScreen:", "f",
    )
    fs_item.setKeyEquivalentModifierMask_(1 << 20)  # Cmd
    view_item.setSubmenu_(view_menu)

    # Help menu — manual + console for bug capture.
    help_item = NSMenuItem.alloc().init()
    menubar.addItem_(help_item)
    help_menu = NSMenu.alloc().initWithTitle_("Help")
    manual_item = help_menu.addItemWithTitle_action_keyEquivalent_(
        f"{app_name} Manual", "showHelpWindow:", "?",
    )
    manual_item.setKeyEquivalentModifierMask_(1 << 20)  # Cmd
    help_menu.addItem_(NSMenuItem.separatorItem())
    console_item = help_menu.addItemWithTitle_action_keyEquivalent_(
        "Show Console", "showConsoleWindow:", "c",
    )
    console_item.setKeyEquivalentModifierMask_(1 << 17 | 1 << 20)  # Shift+Cmd
    help_item.setSubmenu_(help_menu)

    app.setMainMenu_(menubar)
    # macOS auto-populates the Help menu with a search field and "Search"
    # item when it knows which menu is the help menu. Tell it.
    try:
        app.setHelpMenu_(help_menu)
    except Exception:
        pass


# Module-level holds so PyObjC doesn't collect these while NSApp.run() is
# still spinning (NSApp doesn't retain Python references; without these
# globals the AppController object can be freed and any subsequent button
# click crashes in objc_msgSend / object_getattro).
_APP_DELEGATE = None
_APP_CONTROLLER = None


def main() -> int:
    global _APP_DELEGATE, _APP_CONTROLLER
    # Tee stdout/stderr into a ring buffer so the Console window has a live
    # feed for bug reports. Done first so launch messages are captured too.
    try:
        from macroflow import log_capture
        log_capture.install()
    except Exception as e:
        print(f"[main] log capture install failed: {e}")
    # Force the menu-bar app name to "MacroFlow" by overriding both
    # NSProcessInfo and the bundle's info dictionary BEFORE NSApplication
    # initializes (it caches CFBundleName when first asked).
    try:
        from Foundation import NSBundle, NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_("MacroFlow")
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            try:
                info["CFBundleName"] = "MacroFlow"
                info["CFBundleDisplayName"] = "MacroFlow"
            except Exception:
                pass
    except Exception:
        pass
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    # Force dark appearance app-wide so calibrated colors render the same way
    # VHC does (without this they gamma-shift ~30 RGB units lighter).
    try:
        from AppKit import NSAppearance
        _dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        if _dark:
            app.setAppearance_(_dark)
    except Exception:
        pass
    _APP_DELEGATE = _AppDelegate.alloc().init()
    app.setDelegate_(_APP_DELEGATE)
    _APP_CONTROLLER = AppController.alloc().init()
    _build_main_menu(app)
    NSApp.activate()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
