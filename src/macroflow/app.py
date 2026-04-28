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
LCD_HEIGHT = 36
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
        videohub.set_mock_mode(self._store.grid.mock_videohub)
        # Init the VHC notification bridge on the main thread before any
        # macro fire (which runs on a worker thread) can use it.
        videohub.init_bridge()
        self._build_window()
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
        top_strip = LCD_HEIGHT + STATUS_BAR_HEIGHT + 8
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
        lcd_strip_h = LCD_HEIGHT - 6
        lcd_wrap = NSView.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING, win_h - LCD_HEIGHT - 4,
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
        text_h = 18
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
        bar_y = win_h - LCD_HEIGHT - STATUS_BAR_HEIGHT - 6
        self._vh_dot = NSView.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING, bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2,
                       DOT_DIAMETER, DOT_DIAMETER),
        )
        self._vh_dot.setWantsLayer_(True)
        if self._vh_dot.layer() is not None:
            self._vh_dot.layer().setCornerRadius_(DOT_DIAMETER / 2)
            self._vh_dot.layer().setMasksToBounds_(True)
        content.addSubview_(self._vh_dot)

        label_h = 16
        label_y = bar_y + (STATUS_BAR_HEIGHT - label_h) / 2
        vh_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(GRID_PADDING + DOT_DIAMETER + 6, label_y, 110, label_h),
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

        rv_x = GRID_PADDING + DOT_DIAMETER + 6 + 120
        self._rv_dot = NSView.alloc().initWithFrame_(
            NSMakeRect(rv_x, bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2,
                       DOT_DIAMETER, DOT_DIAMETER),
        )
        self._rv_dot.setWantsLayer_(True)
        if self._rv_dot.layer() is not None:
            self._rv_dot.layer().setCornerRadius_(DOT_DIAMETER / 2)
            self._rv_dot.layer().setMasksToBounds_(True)
        content.addSubview_(self._rv_dot)

        rv_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(rv_x + DOT_DIAMETER + 6, label_y, 180, label_h),
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

        # Initial status check + periodic refresh.
        self._set_status_dot(self._vh_dot, False)
        self._set_status_dot(self._rv_dot, False)
        self._refresh_status_dots()

        # Grid — start below the LCD and status bar.
        grid_top = win_h - LCD_HEIGHT - STATUS_BAR_HEIGHT - 8
        for r in range(rows):
            for c in range(cols):
                x = GRID_PADDING + c * (cell_w + CELL_PADDING)
                y = grid_top - (r + 1) * cell_h - r * CELL_PADDING - CELL_PADDING
                btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(x, y, cell_w, cell_h),
                )
                # Borderless + layer-backed so the macro's color paints
                # regardless of focus (setBezelColor reverts on focus loss).
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
                # Tracking area: mouseEntered/mouseExited fire on hover so
                # the LCD can show this macro's description.
                opts = (NSTrackingMouseEnteredAndExited
                        | NSTrackingActiveInKeyWindow
                        | NSTrackingInVisibleRect)
                ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                    btn.bounds(), opts, self,
                    {"tag": str(tag)},
                )
                btn.addTrackingArea_(ta)
                content.addSubview_(btn)
                self._cell_buttons[(r, c)] = btn
        self._refresh_cell_titles()
        self._apply_font_sizes()

        # Pin a minimum window size so cells stay usable, then subscribe to
        # NSWindowDidResize so cells/LCD/status bar reflow when the user
        # drags the corner.
        min_w = GRID_PADDING * 2 + cols * 80 + (cols - 1) * CELL_PADDING
        min_h = GRID_PADDING * 2 + rows * 60 + (rows - 1) * CELL_PADDING + top_strip
        self._window.setMinSize_((min_w, min_h))
        self._lcd_wrap = lcd_wrap
        self._status_views = {
            "vh_dot": self._vh_dot,
            "vh_label": vh_label,
            "rv_dot": self._rv_dot,
            "rv_label": rv_label,
        }
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
        """Background-thread probe of both backends; main-thread dot update."""
        def _probe() -> None:
            import socket
            vh_ok = False
            try:
                with socket.create_connection(("127.0.0.1", 9990), timeout=0.5):
                    vh_ok = True
            except OSError:
                vh_ok = False
            try:
                rv_ok = bool(resolve.connect())
            except Exception:
                rv_ok = False

            def _apply() -> None:
                self._set_status_dot(self._vh_dot, vh_ok)
                self._set_status_dot(self._rv_dot, rv_ok)
                # Re-arm the probe in 5 seconds.
                from PyObjCTools import AppHelper as _ah
                _ah.callLater(5.0, self._refresh_status_dots)

            AppHelper.callAfter(_apply)

        threading.Thread(target=_probe, daemon=True).start()

    @objc.python_method
    def _install_hotkey_monitor(self) -> None:
        """Local monitor: when MacroFlow is the focused app, single-key
        presses (no modifiers other than Shift) fire the matching macro."""
        def _handler(event):
            mods = int(event.modifierFlags())
            # Ignore presses combined with Cmd/Ctrl/Option — let the system
            # have those for shortcuts. Plain key (or Shift+key) fires.
            if mods & (NSEventModifierFlagCommand
                       | NSEventModifierFlagControl
                       | NSEventModifierFlagOption):
                return event
            keycode = int(event.keyCode())
            if keycode in _FN_KEYCODES:
                key = _FN_KEYCODES[keycode]
            else:
                chars = event.charactersIgnoringModifiers() or ""
                if not chars:
                    return event
                key = str(chars)[0].lower()
            for macro in self._store.grid.macros.values():
                if macro.hotkey and macro.hotkey == key:
                    print(f"[hotkey] '{key}' -> firing {macro.id}")
                    r, c = (int(x) for x in macro.id.split(","))
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
        return (macro.label or f"R{r+1}C{c+1}", macro.hotkey or "")

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

        # LCD strip — full width.
        lcd_w = win_w - GRID_PADDING * 2
        lcd_strip_h = LCD_HEIGHT - 6
        self._lcd_wrap.setFrame_(
            NSMakeRect(GRID_PADDING, win_h - LCD_HEIGHT - 4, lcd_w, lcd_strip_h),
        )
        # Re-center the text field inside the wrapper.
        text_h = 18
        self._lcd.setFrame_(
            NSMakeRect(8, (lcd_strip_h - text_h) / 2, lcd_w - 16, text_h),
        )

        # Status bar.
        bar_y = win_h - LCD_HEIGHT - STATUS_BAR_HEIGHT - 6
        label_h = 16
        label_y = bar_y + (STATUS_BAR_HEIGHT - label_h) / 2
        dot_y = bar_y + (STATUS_BAR_HEIGHT - DOT_DIAMETER) / 2
        self._status_views["vh_dot"].setFrame_(
            NSMakeRect(GRID_PADDING, dot_y, DOT_DIAMETER, DOT_DIAMETER),
        )
        self._status_views["vh_label"].setFrame_(
            NSMakeRect(GRID_PADDING + DOT_DIAMETER + 6, label_y, 110, label_h),
        )
        rv_x = GRID_PADDING + DOT_DIAMETER + 6 + 120
        self._status_views["rv_dot"].setFrame_(
            NSMakeRect(rv_x, dot_y, DOT_DIAMETER, DOT_DIAMETER),
        )
        self._status_views["rv_label"].setFrame_(
            NSMakeRect(rv_x + DOT_DIAMETER + 6, label_y, 220, label_h),
        )

        # Grid cells fill the remaining area, dividing it evenly.
        avail_w = win_w - GRID_PADDING * 2 - (cols - 1) * CELL_PADDING
        avail_h = (win_h - self._top_strip - GRID_PADDING - CELL_PADDING - (rows - 1) * CELL_PADDING)
        cell_w = max(1.0, avail_w / cols)
        cell_h = max(1.0, avail_h / rows)
        grid_top = win_h - LCD_HEIGHT - STATUS_BAR_HEIGHT - 8
        for (r, c), btn in self._cell_buttons.items():
            x = GRID_PADDING + c * (cell_w + CELL_PADDING)
            y = grid_top - (r + 1) * cell_h - r * CELL_PADDING - CELL_PADDING
            btn.setFrame_(NSMakeRect(x, y, cell_w, cell_h))
            title = self._title_layers.get((r, c))
            hk = self._hotkey_layers.get((r, c))
            if title is not None and hk is not None:
                self._position_text_layers(btn, title, hk)

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

    @objc.python_method
    def _fire(self, row: int, col: int) -> None:
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
