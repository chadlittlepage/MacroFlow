"""Macro editor sheet: pick a Videohub preset + per-track Resolve enable state.

Opened from the main grid by Cmd-clicking a cell. Navigation arrows in the
top right step through cells without closing.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSButton,
    NSColor,
    NSColorWell,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSPopUpButton,
    NSScrollView,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from objc import super  # type: ignore

from macroflow.backends import resolve, videohub
from macroflow.macro import Macro, ResolveAction, VideohubAction

WINDOW_BG = (0.17, 0.17, 0.17, 1.0)                    # VHC panel calibrated → renders ~#4a
FIELD_BG = (0x49 / 255, 0x49 / 255, 0x49 / 255, 1.0)   # #494949 accent
TEXT_DIM = (0.60, 0.60, 0.58, 1.0)
TEXT_BRIGHT = (0.92, 0.92, 0.92, 1.0)


# Flipped doc view = top-left origin. Lets us lay tracks out from the top
# down without fighting Cocoa's default bottom-aligned scroll behaviour.
class _FlippedDocView(NSView):
    def isFlipped(self):  # noqa: N802 (Cocoa accessor)
        return True

HOTKEY_CHOICES: list[str] = (
    [""]
    + [chr(c) for c in range(ord("a"), ord("z") + 1)]
    + [chr(c) for c in range(ord("0"), ord("9") + 1)]
    + [f"F{n}" for n in range(1, 13)]
)


def hex_to_nscolor(hex_str: str) -> NSColor:
    # Use sRGB so the picked color matches what the grid cell paints
    # (cells paint via NSColor.colorWithSRGBRed_(...).CGColor()).
    s = hex_str.strip().lstrip("#")
    if len(s) != 6:
        return NSColor.controlBackgroundColor()
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError:
        return NSColor.controlBackgroundColor()
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


def nscolor_to_hex(color) -> str:
    try:
        from AppKit import NSColorSpace
        c = color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
        if c is None:
            return "#4a556c"
        r = int(round(c.redComponent() * 255))
        g = int(round(c.greenComponent() * 255))
        b = int(round(c.blueComponent() * 255))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#4a556c"


class MacroEditorWindow(NSWindow):
    """A non-modal editor for macros. Stays open across cell navigation."""

    def initWithController_row_col_(self, controller, row: int, col: int):  # NOQA: N802
        # 700pt tall fits up to ~12 video tracks plus all the chrome at the
        # default size. Min size pins this so the user can't shrink the
        # editor below where the elements fit.
        win_w, win_h = 540, 700
        rect = NSMakeRect(0, 0, win_w, win_h)
        style = (NSWindowStyleMaskTitled
                 | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskResizable)
        self = super().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False,
        )
        if self is None:
            return None
        self.setReleasedWhenClosed_(False)
        self.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*WINDOW_BG),
        )
        from AppKit import NSAppearance as _NSApp
        _dark = _NSApp.appearanceNamed_("NSAppearanceNameDarkAqua")
        if _dark:
            self.setAppearance_(_dark)
        self.setMinSize_(NSMakeSize(win_w, win_h))
        self._controller = controller
        self._row = row
        self._col = col
        self._win_h = win_h
        # Resolve track info is cached per editor session — querying the
        # Fusion scripting bridge on every Prev/Next click locks the UI
        # for up to 5 seconds.
        self._cached_tracks: list | None = None
        self._macro = self._fresh_macro_for(row, col)
        self.setTitle_(f"Edit Macro — {self._macro.id}")
        self._build_ui()
        self.center()
        return self

    @objc.python_method
    def _fresh_macro_for(self, row: int, col: int) -> Macro:
        existing = self._controller._store.grid.get(row, col)
        if existing is not None:
            # Edit a copy; commit on save / navigation.
            from copy import deepcopy
            return deepcopy(existing)
        return Macro(id=self._controller._store.grid.cell_id(row, col))

    # -- UI construction ------------------------------------------------------

    @objc.python_method
    def _build_ui(self) -> None:
        content = self.contentView()
        if content is None:
            return

        win_h = self._win_h
        TOP_PIN = NSViewMinYMargin  # element tracks the top edge on resize.

        # Prev / Next nav arrows in the top right.
        prev_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(420, win_h - 40, 50, 28),
        )
        prev_btn.setTitle_("◀")
        prev_btn.setBezelStyle_(1)
        prev_btn.setTarget_(self)
        prev_btn.setAction_("navPrev:")
        prev_btn.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(prev_btn)

        next_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(478, win_h - 40, 50, 28),
        )
        next_btn.setTitle_("▶")
        next_btn.setBezelStyle_(1)
        next_btn.setTarget_(self)
        next_btn.setAction_("navNext:")
        next_btn.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(next_btn)

        self._cell_indicator = self._add_label(content, "", x=20, y=win_h - 42)
        # Cell indicator (e.g. "R2C2 (6 / 16)") at 40% larger than the
        # standard 12pt label font.
        self._cell_indicator.setFrame_(NSMakeRect(20, win_h - 42, 280, 26))
        self._cell_indicator.setFont_(NSFont.boldSystemFontOfSize_(17))
        self._cell_indicator.setAutoresizingMask_(TOP_PIN)

        # Label
        y = win_h - 80
        self._label_field = self._add_labeled_text(
            content, "Label:", self._macro.label, x=20, y=y, width=480,
        )
        self._label_field.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        # Stage label edits onto the live cell as the user types.
        self._label_field.setDelegate_(self)
        y -= 40

        # Color well + hotkey popup on one row
        cl = self._add_label(content, "Color:", x=20, y=y + 4)
        cl.setAutoresizingMask_(TOP_PIN)
        self._color_well = NSColorWell.alloc().initWithFrame_(
            NSMakeRect(120, y, 60, 26),
        )
        self._color_well.setColor_(hex_to_nscolor(self._macro.color))
        self._color_well.setTarget_(self)
        self._color_well.setAction_("colorChanged:")
        try:
            self._color_well.setContinuous_(True)
        except Exception:
            pass
        self._color_well.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(self._color_well)

        hl = self._add_label(content, "Hotkey:", x=210, y=y + 4)
        hl.setAutoresizingMask_(TOP_PIN)
        self._hotkey_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(280, y, 220, 26),
        )
        for k in HOTKEY_CHOICES:
            # Display uppercase; storage stays lowercase for keypress matching.
            self._hotkey_popup.addItemWithTitle_(k.upper() if k else "(none)")
        self._select_hotkey()
        self._hotkey_popup.setTarget_(self)
        self._hotkey_popup.setAction_("hotkeyChanged:")
        self._hotkey_popup.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(self._hotkey_popup)
        y -= 40

        # Videohub: device + preset
        sh1 = self._add_section_header(content, "Videohub", x=20, y=y)
        sh1.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        y -= 28
        dl = self._add_label(content, "Device:", x=20, y=y + 4)
        dl.setAutoresizingMask_(TOP_PIN)
        self._device_popup = self._add_popup(content, x=120, y=y, width=380)
        self._device_popup.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        self._populate_devices()
        y -= 36
        pl = self._add_label(content, "Preset:", x=20, y=y + 4)
        pl.setAutoresizingMask_(TOP_PIN)
        self._preset_popup = self._add_popup(content, x=120, y=y, width=380)
        self._preset_popup.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        self._device_popup.setTarget_(self)
        self._device_popup.setAction_("deviceChanged:")
        self._populate_presets_for_current_device()
        y -= 50

        # Resolve: per-track checkboxes (scrollable)
        sh2 = self._add_section_header(
            content, "DaVinci Resolve video tracks", x=20, y=y,
        )
        sh2.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        y -= 28

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(20, 80, 500, y - 60),
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        # Grow with the window in BOTH axes so resizing makes the track
        # list bigger rather than overlapping the rest of the chrome.
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(scroll)
        self._track_scroll = scroll
        self._track_checkboxes: dict = {}
        self._populate_resolve_tracks()

        # Bottom buttons.
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(440, 20, 80, 32))
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("save:")
        content.addSubview_(save_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(350, 20, 80, 32))
        cancel_btn.setTitle_("Close")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")  # Escape
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancel:")
        content.addSubview_(cancel_btn)

        clear_btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, 20, 100, 32))
        clear_btn.setTitle_("Clear cell")
        clear_btn.setBezelStyle_(1)
        clear_btn.setTarget_(self)
        clear_btn.setAction_("clearCell:")
        content.addSubview_(clear_btn)

        self._update_cell_indicator()
        # Snapshot the just-built form state so _commit_to_store has a
        # baseline to diff against. Without this, the first Save/Navigate
        # raises AttributeError on `self._snapshot`.
        self._snapshot = self._capture_form_state()

    @objc.python_method
    def _add_label(self, parent, text: str, x: float, y: float) -> NSTextField:
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 100, 20))
        f.setStringValue_(text)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        f.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
        )
        parent.addSubview_(f)
        return f

    @objc.python_method
    def _add_section_header(self, parent, text: str, x: float, y: float):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 480, 22))
        f.setStringValue_(text)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        f.setFont_(NSFont.boldSystemFontOfSize_(12))
        f.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_DIM),
        )
        parent.addSubview_(f)
        return f

    @objc.python_method
    def _add_labeled_text(self, parent, label: str, value: str,
                          x: float, y: float, width: float) -> NSTextField:
        # Pin the inline label to the top edge so it doesn't drift away
        # from the input field when the window resizes.
        inline = self._add_label(parent, label, x, y + 4)
        inline.setAutoresizingMask_(NSViewMinYMargin)
        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(x + 100, y, width - 100, 22),
        )
        field.setStringValue_(value)
        field.setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(*FIELD_BG),
        )
        field.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
        )
        parent.addSubview_(field)
        return field

    @objc.python_method
    def _add_popup(self, parent, x: float, y: float, width: float) -> NSPopUpButton:
        popup = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(x, y, width, 26))
        parent.addSubview_(popup)
        return popup

    # -- Population -----------------------------------------------------------

    @objc.python_method
    def _select_hotkey(self) -> None:
        if self._macro.hotkey in HOTKEY_CHOICES:
            self._hotkey_popup.selectItemAtIndex_(
                HOTKEY_CHOICES.index(self._macro.hotkey),
            )
        else:
            self._hotkey_popup.selectItemAtIndex_(0)

    @objc.python_method
    def _populate_devices(self) -> None:
        self._device_popup.removeAllItems()
        self._device_popup.addItemWithTitle_("(none)")
        self._devices = videohub.list_devices()
        active_id = videohub.load_config().get("last_device_id", "")
        for d in self._devices:
            star = "  ★ active" if d.unique_id == active_id else ""
            self._device_popup.addItemWithTitle_(
                f"{d.display_name} — {d.ip}{star}"
            )
        current = self._macro.videohub.device_id
        if current:
            for i, d in enumerate(self._devices, start=1):
                if d.unique_id == current:
                    self._device_popup.selectItemAtIndex_(i)
                    return
        if active_id:
            for i, d in enumerate(self._devices, start=1):
                if d.unique_id == active_id:
                    self._device_popup.selectItemAtIndex_(i)
                    return
        self._device_popup.selectItemAtIndex_(0)

    @objc.python_method
    def _selected_device_id(self) -> str:
        idx = self._device_popup.indexOfSelectedItem()
        if idx <= 0 or idx > len(self._devices):
            return ""
        return self._devices[idx - 1].unique_id

    @objc.python_method
    def _populate_presets_for_current_device(self) -> None:
        self._preset_popup.removeAllItems()
        self._preset_popup.addItemWithTitle_("(none)")
        device_id = self._selected_device_id()
        if not device_id:
            return
        names = videohub.list_presets(device_id)
        for n in names:
            self._preset_popup.addItemWithTitle_(n)
        current = self._macro.videohub.preset_name
        if current and current in names:
            self._preset_popup.selectItemAtIndex_(names.index(current) + 1)

    @objc.python_method
    def _populate_resolve_tracks(self) -> None:
        self._track_checkboxes.clear()

        if self._cached_tracks is None:
            self._cached_tracks = resolve.safe_get_video_track_info() or []
        live = self._cached_tracks
        seen: set[int] = set()
        rows: list[tuple[int, str, bool]] = []
        live_state: dict[int, bool] = {}
        for info in live:
            rows.append((info["index"], info["name"], info["enabled"]))
            seen.add(int(info["index"]))
            live_state[int(info["index"])] = bool(info["enabled"])
        for idx, _ in sorted(self._macro.resolve.tracks.items()):
            if int(idx) not in seen:
                rows.append((int(idx), f"V{idx}", True))
        rows.sort(key=lambda r: r[0])

        scroll_size = self._track_scroll.contentSize()
        doc_w = scroll_size.width
        row_h = 26
        top_pad = 6

        if not rows:
            doc_h = scroll_size.height
            doc_view = _FlippedDocView.alloc().initWithFrame_(
                NSMakeRect(0, 0, doc_w, doc_h),
            )
            note = NSTextField.alloc().initWithFrame_(
                NSMakeRect(8, top_pad, doc_w - 16, 20),
            )
            note.setStringValue_(
                "(Resolve not running — tracks will populate when connected)",
            )
            note.setBezeled_(False)
            note.setDrawsBackground_(False)
            note.setEditable_(False)
            note.setSelectable_(False)
            note.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_DIM),
            )
            doc_view.addSubview_(note)
            self._track_scroll.setDocumentView_(doc_view)
            return

        n = len(rows)
        content_h = n * row_h + top_pad
        # Doc view fills the scroll area or grows beyond it for many tracks.
        doc_h = max(scroll_size.height, content_h)
        doc_view = _FlippedDocView.alloc().initWithFrame_(
            NSMakeRect(0, 0, doc_w, doc_h),
        )
        # rows is ascending V1..Vn; render in reverse so Vn lands at top.
        # Flipped coords: y=0 is the TOP. V8 at y=top_pad, V7 below, etc.
        display_order = list(reversed(rows))
        for i, (idx, name, _live_enabled) in enumerate(display_order):
            y = top_pad + i * row_h
            cb = NSButton.alloc().initWithFrame_(
                NSMakeRect(8, y, doc_w - 16, 22),
            )
            cb.setButtonType_(3)
            cb.setTitle_(f"V{idx} — {name}")
            # Two-state only: checked = ON, unchecked = OFF. Saved macro
            # value wins; otherwise default to whatever Resolve currently
            # shows for that track.
            cb.setAllowsMixedState_(False)
            saved = self._macro.resolve.tracks.get(int(idx))
            if saved is None:
                saved = live_state.get(int(idx), False)
            cb.setState_(1 if saved else 0)
            doc_view.addSubview_(cb)
            self._track_checkboxes[int(idx)] = cb
        self._track_scroll.setDocumentView_(doc_view)
        # Flipped doc views scroll naturally to top by default; no need to
        # call scrollPoint.

    @objc.python_method
    def _update_cell_indicator(self) -> None:
        rows = self._controller._store.grid.rows
        cols = self._controller._store.grid.cols
        idx = self._row * cols + self._col + 1
        total = rows * cols
        self._cell_indicator.setStringValue_(
            f"R{self._row+1}C{self._col+1}   ({idx} / {total})",
        )
        self.setTitle_(f"Edit Macro — {self._macro.id}")

    # -- Actions --------------------------------------------------------------

    def colorChanged_(self, sender) -> None:  # NOQA: N802
        self._macro.color = nscolor_to_hex(sender.color())
        self._stage_live("color", self._macro.color)

    def hotkeyChanged_(self, sender) -> None:  # NOQA: N802
        idx = sender.indexOfSelectedItem()
        hotkey = HOTKEY_CHOICES[idx] if 0 <= idx < len(HOTKEY_CHOICES) else ""
        self._macro.hotkey = hotkey
        self._stage_live("hotkey", hotkey)

    def controlTextDidChange_(self, notification) -> None:  # NOQA: N802
        # NSTextField delegate hook — fires on every keystroke in the label.
        obj = notification.object()
        if obj is self._label_field:
            label = str(obj.stringValue() or "")
            self._macro.label = label
            self._stage_live("label", label)

    @objc.python_method
    def _stage_live(self, attr: str, value) -> None:
        # Push the in-progress macro into the store so the cell repaints
        # without waiting for Save / navigation.
        ctrl = self._controller
        if ctrl is None:
            return
        existing = ctrl._store.grid.get(self._row, self._col)
        if existing is None:
            ctrl._store.grid.set(self._row, self._col, self._macro)
        else:
            setattr(existing, attr, value)
        ctrl._refresh_cell_titles()

    def deviceChanged_(self, sender) -> None:  # NOQA: N802
        self._populate_presets_for_current_device()

    def navPrev_(self, sender) -> None:  # NOQA: N802
        self._navigate(-1)

    def navNext_(self, sender) -> None:  # NOQA: N802
        self._navigate(1)

    @objc.python_method
    def _navigate(self, step: int) -> None:
        # Auto-save on navigate — but the empty-check inside
        # _commit_to_store treats a macro with only Resolve tracks as empty,
        # so passing through cells without setting a label/hotkey/Videohub
        # never colours the cell.
        self._commit_to_store(persist=True)
        rows = self._controller._store.grid.rows
        cols = self._controller._store.grid.cols
        idx = (self._row * cols + self._col + step) % (rows * cols)
        self._row, self._col = divmod(idx, cols)
        self._macro = self._fresh_macro_for(self._row, self._col)
        self._reload_fields()

    @objc.python_method
    def _reload_fields(self) -> None:
        self._label_field.setStringValue_(self._macro.label)
        self._color_well.setColor_(hex_to_nscolor(self._macro.color))
        self._select_hotkey()
        self._populate_devices()
        self._populate_presets_for_current_device()
        self._populate_resolve_tracks()
        self._update_cell_indicator()
        # Snapshot the just-loaded form state so we can tell later whether
        # the user actually changed anything before navigating.
        self._snapshot = self._capture_form_state()

    @objc.python_method
    def _capture_form_state(self) -> tuple:
        label = str(self._label_field.stringValue() or "")
        color = nscolor_to_hex(self._color_well.color())
        hk_idx = self._hotkey_popup.indexOfSelectedItem()
        hotkey = (HOTKEY_CHOICES[hk_idx]
                  if 0 <= hk_idx < len(HOTKEY_CHOICES) else "")
        device_id = self._selected_device_id()
        preset_idx = self._preset_popup.indexOfSelectedItem()
        preset_name = ""
        if preset_idx > 0:
            preset_name = str(self._preset_popup.titleOfSelectedItem() or "")
        tracks = frozenset(
            (idx, int(cb.state()) == 1)
            for idx, cb in self._track_checkboxes.items()
        )
        return (label, color, hotkey, device_id, preset_name, tracks)

    @objc.python_method
    def _commit_to_store(self, persist: bool = True) -> None:
        current = self._capture_form_state()
        if current == self._snapshot:
            # Nothing changed since this cell was loaded — don't touch the
            # store. Prevents passing through cells from creating phantom
            # macros, while still keeping any real edits the user made.
            return
        label, color, hotkey, device_id, preset_name, tracks_fs = current
        self._macro.label = label
        self._macro.color = color
        self._macro.hotkey = hotkey
        self._macro.videohub = VideohubAction(
            device_id=device_id, preset_name=preset_name,
        )
        self._macro.resolve = ResolveAction(tracks={i: v for i, v in tracks_fs})
        ctrl = self._controller
        ctrl._store.grid.set(self._row, self._col, self._macro)
        if persist:
            ctrl._store.save()
        # Now that the macro is committed, this state is the new clean
        # baseline.
        self._snapshot = current
        ctrl._refresh_cell_titles()

    def save_(self, sender) -> None:  # NOQA: N802
        self._commit_to_store(persist=True)

    def cancel_(self, sender) -> None:  # NOQA: N802
        self.close()

    def clearCell_(self, sender) -> None:  # NOQA: N802
        self._macro = Macro(id=self._controller._store.grid.cell_id(self._row, self._col))
        self._controller._store.grid.clear(self._row, self._col)
        self._controller._store.save()
        self._reload_fields()  # also resets the snapshot
        self._controller._refresh_cell_titles()
