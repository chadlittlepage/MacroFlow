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
    NSObject,
    NSPopUpButton,
    NSScrollView,
    NSSplitView,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTextFieldRoundedBezel,
    NSView,
    NSViewHeightSizable,
    NSViewMinYMargin,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSIndexSet
from objc import super  # type: ignore

from macroflow.backends import resolve, videohub
from macroflow.macro import Macro, ResolveAction, VideohubAction
from macroflow.quad_preview import QuadPreviewView

# Per-track transform fields, ported from the DaVinci Script project.
# The values here are stored alongside the enable/disable bool and persisted
# to macroflow.json. (Application of these values to Resolve at fire time
# is a follow-up — for now they're saved but not pushed to the timeline.)
_FLOAT_FIELDS = [
    ("zoom_x", "Zoom X", 1.0),
    ("zoom_y", "Zoom Y", 1.0),
    ("position_x", "Position X", 0.0),
    ("position_y", "Position Y", 0.0),
    ("rotation_angle", "Rotation Angle", 0.0),
    ("anchor_point_x", "Anchor Point X", 0.0),
    ("anchor_point_y", "Anchor Point Y", 0.0),
    ("pitch", "Pitch", 0.0),
    ("yaw", "Yaw", 0.0),
]

_DEFAULT_TRANSFORM = {
    "quadrant": "Q1",
    **{key: default for key, _, default in _FLOAT_FIELDS},
    "flip_h": False,
    "flip_v": False,
}

# Tag we set on every transform NSTextField so controlTextDidChange_ can
# tell them apart from the label field (which uses a different code path).
_TRANSFORM_FIELD_TAG = 0xF1F1

# Per-field "value per horizontal pixel" for click-and-drag scrubbing.
# Tuned so a typical 100px drag yields a useful range for each field type.
_SCRUB_STEPS = {
    "zoom_x":         0.01,
    "zoom_y":         0.01,
    "position_x":     1.0,
    "position_y":     1.0,
    "rotation_angle": 0.5,
    "anchor_point_x": 1.0,
    "anchor_point_y": 1.0,
    "pitch":          0.5,
    "yaw":            0.5,
}


class _ScrubField(NSTextField):
    """NSTextField that supports click-and-drag scrubbing of its numeric value.

    - Plain click + drag horizontally → drag right increases, left decreases.
    - Plain click without drag → enters edit mode (becomes first responder).
    - Double-click → enters edit mode immediately.
    """

    def init(self):
        self = objc.super(_ScrubField, self).init()
        if self is not None:
            self._scrub_anchor = None
            self._scrub_start = 0.0
            self._scrub_active = False
            self._scrub_step = 1.0
            self._scrub_handler = None
        return self

    def set_scrub_step(self, step: float) -> None:
        self._scrub_step = float(step)

    def set_scrub_handler(self, fn) -> None:
        # fn(field, new_value, is_final) — called on every drag tick + on mouseUp
        self._scrub_handler = fn

    def mouseDown_(self, event):  # noqa: N802 (Cocoa)
        if int(event.clickCount()) >= 2:
            objc.super(_ScrubField, self).mouseDown_(event)
            return
        try:
            self._scrub_start = float(self.stringValue())
        except (ValueError, TypeError):
            self._scrub_start = 0.0
        self._scrub_anchor = event.locationInWindow()
        self._scrub_active = False

    def mouseDragged_(self, event):  # noqa: N802 (Cocoa)
        if self._scrub_anchor is None:
            return
        cur = event.locationInWindow()
        dx = float(cur.x - self._scrub_anchor.x)
        if not self._scrub_active and abs(dx) < 3:
            return
        self._scrub_active = True
        new_val = float(self._scrub_start) + dx * float(self._scrub_step)
        self.setStringValue_(f"{new_val:.3f}")
        if self._scrub_handler is not None:
            try:
                self._scrub_handler(self, new_val, False)
            except Exception:
                pass

    def mouseUp_(self, event):  # noqa: N802 (Cocoa)
        was_scrub = self._scrub_active
        had_anchor = self._scrub_anchor is not None
        self._scrub_anchor = None
        self._scrub_active = False
        if was_scrub:
            try:
                final_val = float(self.stringValue())
            except (ValueError, TypeError):
                final_val = 0.0
            if self._scrub_handler is not None:
                try:
                    self._scrub_handler(self, final_val, True)
                except Exception:
                    pass
            return
        # Plain click without a drag — enter text edit mode.
        if had_anchor:
            win = self.window()
            if win is not None:
                win.makeFirstResponder_(self)

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

# Modifier popup: stored as one of these strings, displayed with the
# friendly title in MODIFIER_DISPLAY. Top entry is "" = no modifier required.
MODIFIER_CHOICES: list[str] = ["", "Cmd", "Ctrl", "Opt", "Shift"]
MODIFIER_DISPLAY = {
    "": "—",
    "Cmd": "Cmd",
    "Ctrl": "Ctrl",
    "Opt": "Opt",
    "Shift": "Shift",
}


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


class _TrackTableView(NSTableView):
    """NSTableView with custom keyboard handling for the editor:

    - Up / Down: default NSTableView behavior (move selection).
    - Enter / Return: toggle the selected track's enabled flag.
    - Left / Right: step the selected track's quadrant Q1→Q2→Q3→Q4 (and back).
    """

    def keyDown_(self, event):  # noqa: N802 (Cocoa)
        chars = event.charactersIgnoringModifiers() or ""
        delegate = self.delegate()
        if chars in ("\r", "\n", "\x03"):
            if delegate is not None and hasattr(delegate, "tableEnterPressed_"):
                delegate.tableEnterPressed_(self)
                return
        if chars and len(chars) == 1:
            code = ord(chars[0])
            # NSLeftArrowFunctionKey / NSRightArrowFunctionKey.
            if code == 0xF702:
                if delegate is not None and hasattr(delegate, "tableArrowLeft_"):
                    delegate.tableArrowLeft_(self)
                    return
            if code == 0xF703:
                if delegate is not None and hasattr(delegate, "tableArrowRight_"):
                    delegate.tableArrowRight_(self)
                    return
        objc.super(_TrackTableView, self).keyDown_(event)


class _TrackListDataSource(NSObject):
    """Backs the editor's track NSTableView. Rows = (idx, "V<idx> — name")."""

    def init(self):
        self = objc.super(_TrackListDataSource, self).init()
        if self is not None:
            self.entries: list[tuple[int, str]] = []
        return self

    @objc.signature(b"q@:@")
    def numberOfRowsInTableView_(self, tv):  # NOQA: N802
        return len(self.entries)

    def tableView_objectValueForTableColumn_row_(self, tv, col, row):  # NOQA: N802
        if row < 0 or row >= len(self.entries):
            return ""
        return self.entries[row][1]


class MacroEditorWindow(NSWindow):
    """A non-modal editor for macros. Stays open across cell navigation."""

    def initWithController_row_col_(self, controller, row: int, col: int):  # NOQA: N802
        # Wide enough to fit the per-track transform panel + 16:9 quad
        # preview side-by-side with the track list. Min-size pinned so the
        # contents never overlap.
        win_w, win_h = 1050, 1100
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
        self._win_w = win_w
        # Resolve track info is cached per editor session — querying the
        # Fusion scripting bridge on every Prev/Next click locks the UI
        # for up to 5 seconds.
        self._cached_tracks: list | None = None
        # Same caching strategy for the live per-track transforms we read
        # off the first clip on each track in Resolve's current timeline.
        self._cached_track_transforms: dict[int, dict] | None = None
        # Cached timeline resolution. Used to compute quadrant position
        # offsets when the user picks a quadrant from the popup or clicks
        # one in the preview view.
        self._tl_w: int = 1920
        self._tl_h: int = 1080
        # Per-track, per-quadrant transform cache. When the user switches
        # quadrants we SAVE the current quadrant's values here, then either
        # RESTORE this quadrant's cached values (if it's been visited) or
        # fall back to timeline-derived defaults. Survives across cell
        # navigation in the editor session.
        self._quadrant_cache: dict[int, dict[str, dict]] = {}
        # When False, edits in this editor (transform changes, track enable
        # toggles, quadrant picks) don't push to Resolve — useful for
        # building a preset without disturbing a running edit. Defaults ON.
        self._live_resolve_updates: bool = True
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
        win_w = self._win_w
        TOP_PIN = NSViewMinYMargin  # element tracks the top edge on resize.

        # Prev / Next nav arrows in the top right. Pinned to the right edge
        # so they stay there if the user resizes wider.
        TOP_RIGHT = NSViewMinYMargin | 1  # MinYMargin (top) | NSViewMinXMargin
        prev_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(win_w - 120, win_h - 40, 50, 28),
        )
        prev_btn.setTitle_("◀")
        prev_btn.setBezelStyle_(1)
        prev_btn.setTarget_(self)
        prev_btn.setAction_("navPrev:")
        prev_btn.setAutoresizingMask_(TOP_RIGHT)
        content.addSubview_(prev_btn)

        next_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(win_w - 62, win_h - 40, 50, 28),
        )
        next_btn.setTitle_("▶")
        next_btn.setBezelStyle_(1)
        next_btn.setTarget_(self)
        next_btn.setAction_("navNext:")
        next_btn.setAutoresizingMask_(TOP_RIGHT)
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
            content, "Label:", self._macro.label, x=20, y=y, width=win_w - 40,
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
        # Modifier popup (Cmd / Ctrl / Opt / Shift / none) sits in front of
        # the hotkey popup. A "+" label between them mirrors how the macOS
        # menu shows shortcuts (e.g. "Cmd + 1").
        self._modifier_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(280, y, 90, 26),
        )
        for m in MODIFIER_CHOICES:
            self._modifier_popup.addItemWithTitle_(MODIFIER_DISPLAY[m])
        self._modifier_popup.setTarget_(self)
        self._modifier_popup.setAction_("modifierChanged:")
        self._modifier_popup.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(self._modifier_popup)
        plus_lbl = self._add_label(content, "+", x=378, y=y + 4)
        plus_lbl.setFont_(NSFont.boldSystemFontOfSize_(13))
        plus_lbl.setAutoresizingMask_(TOP_PIN)
        self._hotkey_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(396, y, 104, 26),
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
        # Per-macro Videohub Enable checkbox right of the section header.
        # Acts as the macro's local override of MacroGrid.videohub_enabled
        # (the global Settings toggle).
        self._vh_enable_check = NSButton.alloc().initWithFrame_(
            NSMakeRect(120, y - 1, 100, 22),
        )
        self._vh_enable_check.setButtonType_(3)  # NSSwitchButton
        self._vh_enable_check.setTitle_("Enable")
        self._vh_enable_check.setState_(1 if self._macro.videohub_enabled else 0)
        self._vh_enable_check.setTarget_(self)
        self._vh_enable_check.setAction_("videohubMacroEnableChanged:")
        self._vh_enable_check.setAutoresizingMask_(TOP_PIN)
        content.addSubview_(self._vh_enable_check)
        y -= 28
        dl = self._add_label(content, "Device:", x=20, y=y + 4)
        dl.setAutoresizingMask_(TOP_PIN)
        self._device_popup = self._add_popup(content, x=120, y=y, width=win_w - 140)
        self._device_popup.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        self._populate_devices()
        y -= 36
        pl = self._add_label(content, "Preset:", x=20, y=y + 4)
        pl.setAutoresizingMask_(TOP_PIN)
        self._preset_popup = self._add_popup(content, x=120, y=y, width=win_w - 140)
        self._preset_popup.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        self._device_popup.setTarget_(self)
        self._device_popup.setAction_("deviceChanged:")
        self._populate_presets_for_current_device()
        # Apply the macro+grid-wide Videohub state to the popups (greys
        # them out + shows "Disabled" if both per-macro and grid are off).
        self._apply_videohub_field_state()
        y -= 50

        # Resolve: 2-column track editor — list left, transform detail right.
        sh2 = self._add_section_header(
            content, "DaVinci Resolve video tracks", x=20, y=y,
        )
        sh2.setAutoresizingMask_(TOP_PIN | NSViewWidthSizable)
        y -= 24

        # Tracks region spans from y down to ~80 (above bottom buttons).
        tracks_bottom = 80
        tracks_top = y
        tracks_h = tracks_top - tracks_bottom
        # Default: list pane 290 wide (≈30% wider than the original 220),
        # detail pane fills the rest. User can drag the divider in either
        # direction to expose long track names.
        default_list_w = 290

        # ── NSSplitView holding the list pane and the detail pane ──
        split = NSSplitView.alloc().initWithFrame_(
            NSMakeRect(20, tracks_bottom, win_w - 40, tracks_h),
        )
        split.setVertical_(True)
        try:
            split.setDividerStyle_(2)  # NSSplitViewDividerStyleThin
        except Exception:
            pass
        split.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        # Editor window is also the split delegate so we can constrain how
        # far the user can drag the divider in either direction.
        split.setDelegate_(self)
        content.addSubview_(split)
        self._tracks_split = split

        # ── Left pane: scrollable NSTableView ──
        list_pane = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, default_list_w, tracks_h),
        )
        list_scroll = NSScrollView.alloc().initWithFrame_(
            list_pane.bounds(),
        )
        list_scroll.setHasVerticalScroller_(True)
        list_scroll.setBorderType_(NSBezelBorder)
        list_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        table = _TrackTableView.alloc().initWithFrame_(
            NSMakeRect(0, 0, default_list_w - 20, tracks_h),
        )
        col = NSTableColumn.alloc().initWithIdentifier_("track")
        col.setWidth_(default_list_w - 24)
        try:
            col.setMinWidth_(80.0)
            col.setMaxWidth_(2000.0)
            col.setResizingMask_(2)  # NSTableColumnUserResizingMask
        except Exception:
            pass
        # Track names come from Resolve and can't be changed here. Block
        # the cell-edit-on-double-click affordance so the user doesn't
        # think otherwise.
        try:
            col.setEditable_(False)
        except Exception:
            pass
        try:
            col.headerCell().setStringValue_("Track")
        except Exception:
            pass
        table.addTableColumn_(col)
        table.setHeaderView_(None)
        table.setRowHeight_(22)
        table.setAllowsMultipleSelection_(False)
        try:
            from AppKit import NSTableViewLastColumnOnlyAutoresizingStyle
            table.setColumnAutoresizingStyle_(NSTableViewLastColumnOnlyAutoresizingStyle)
        except ImportError:
            pass
        ds = _TrackListDataSource.alloc().init()
        table.setDataSource_(ds)
        table.setDelegate_(self)
        list_scroll.setDocumentView_(table)
        list_pane.addSubview_(list_scroll)
        split.addSubview_(list_pane)
        self._track_table = table
        self._track_data_source = ds

        # ── Right pane: detail container ──
        detail_pane_w = (win_w - 40) - default_list_w - 8  # split divider ~ 8
        detail_pane = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, detail_pane_w, tracks_h),
        )
        split.addSubview_(detail_pane)
        # NSSplitView ignores the initial subview frames and lays them out
        # itself. Force a layout pass FIRST, then snap the divider — without
        # adjustSubviews(), setPosition lands on whatever the split decides
        # the panes should be (we observed list=290, detail=321 instead of
        # the expected 290 / 612 split).
        try:
            split.adjustSubviews()
            split.setPosition_ofDividerAtIndex_(float(default_list_w), 0)
        except Exception:
            pass
        # Re-read what the split actually gave us, so the detail widgets get
        # frames sized to the real pane width rather than our requested one.
        actual_pane_w = float(detail_pane.frame().size.width)

        # All detail widgets live INSIDE detail_pane. Coordinates are
        # relative to that view's bottom-left. 16px padding on the left so
        # labels don't bump into the divider.
        detail_x = 16
        detail_w = actual_pane_w - detail_x - 16
        detail_y_top = tracks_h
        # Row 1: Enable checkbox
        ey = detail_y_top - 22
        self._track_enable = NSButton.alloc().initWithFrame_(
            NSMakeRect(detail_x, ey, 120, 22),
        )
        self._track_enable.setButtonType_(3)  # NSSwitchButton
        self._track_enable.setTitle_("Enable track")
        self._track_enable.setTarget_(self)
        self._track_enable.setAction_("trackEnableChanged:")
        self._track_enable.setAutoresizingMask_(TOP_PIN)
        detail_pane.addSubview_(self._track_enable)

        # Row 2: Quadrant popup
        qy = ey - 32
        ql = self._add_label(detail_pane, "Quadrant:", x=detail_x, y=qy + 4)
        ql.setAutoresizingMask_(TOP_PIN)
        self._quadrant_popup = NSPopUpButton.alloc().initWithFrame_(
            NSMakeRect(detail_x + 80, qy, 100, 24),
        )
        for q in ("Q1", "Q2", "Q3", "Q4"):
            self._quadrant_popup.addItemWithTitle_(q)
        self._quadrant_popup.setTarget_(self)
        self._quadrant_popup.setAction_("quadrantChanged:")
        self._quadrant_popup.setAutoresizingMask_(TOP_PIN)
        detail_pane.addSubview_(self._quadrant_popup)

        # Row 3..7: Transform fields, two columns
        self._transform_fields: dict = {}
        label_w = 100
        field_w = 100
        col2_offset = label_w + field_w + 24
        row_h = 28
        ty = qy - 36

        def add_field(side: int, key: str, label_text: str, default: float):
            lx = detail_x if side == 0 else detail_x + col2_offset
            lab = self._add_label(detail_pane, f"{label_text}:", x=lx, y=ty + 4)
            lab.setAutoresizingMask_(TOP_PIN)
            fld = _ScrubField.alloc().initWithFrame_(
                NSMakeRect(lx + label_w, ty, field_w, 22),
            )
            fld.setStringValue_(f"{default:.3f}")
            fld.setBezelStyle_(NSTextFieldRoundedBezel)
            fld.setBezeled_(True)
            fld.setEditable_(True)
            fld.setBackgroundColor_(
                NSColor.colorWithSRGBRed_green_blue_alpha_(*FIELD_BG),
            )
            fld.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(*TEXT_BRIGHT),
            )
            fld.setFont_(NSFont.systemFontOfSize_(12))
            fld.setDelegate_(self)
            fld.setTag_(_TRANSFORM_FIELD_TAG)
            fld.setAutoresizingMask_(TOP_PIN)
            fld.set_scrub_step(_SCRUB_STEPS.get(key, 1.0))
            fld.set_scrub_handler(self._on_scrub_field)
            detail_pane.addSubview_(fld)
            self._transform_fields[key] = fld

        # Lay out the float fields in pairs except Rotation Angle (solo).
        add_field(0, "zoom_x",        "Zoom X",        1.0)
        add_field(1, "zoom_y",        "Zoom Y",        1.0)
        ty -= row_h
        add_field(0, "position_x",    "Position X",    0.0)
        add_field(1, "position_y",    "Position Y",    0.0)
        ty -= row_h
        add_field(0, "rotation_angle", "Rotation",     0.0)
        ty -= row_h
        add_field(0, "anchor_point_x", "Anchor X",     0.0)
        add_field(1, "anchor_point_y", "Anchor Y",     0.0)
        ty -= row_h
        add_field(0, "pitch",         "Pitch",         0.0)
        add_field(1, "yaw",           "Yaw",           0.0)
        ty -= row_h

        # Flip H / Flip V checkboxes
        self._flip_h_check = NSButton.alloc().initWithFrame_(
            NSMakeRect(detail_x, ty, 130, 22),
        )
        self._flip_h_check.setButtonType_(3)
        self._flip_h_check.setTitle_("Flip Horizontal")
        self._flip_h_check.setTarget_(self)
        self._flip_h_check.setAction_("flipChanged:")
        self._flip_h_check.setAutoresizingMask_(TOP_PIN)
        detail_pane.addSubview_(self._flip_h_check)

        self._flip_v_check = NSButton.alloc().initWithFrame_(
            NSMakeRect(detail_x + col2_offset, ty, 130, 22),
        )
        self._flip_v_check.setButtonType_(3)
        self._flip_v_check.setTitle_("Flip Vertical")
        self._flip_v_check.setTarget_(self)
        self._flip_v_check.setAction_("flipChanged:")
        self._flip_v_check.setAutoresizingMask_(TOP_PIN)
        detail_pane.addSubview_(self._flip_v_check)
        ty -= 30

        # Reset Selected / Reset All buttons, right-justified just above
        # the quad preview. Pinned to top-right of the detail pane so they
        # stay glued there if the user widens the editor.
        btn_w = 130
        btn_h = 22
        ty -= btn_h
        right_x = detail_x + detail_w
        TOP_RIGHT_PIN = NSViewMinYMargin | 1  # top edge | NSViewMinXMargin

        # Live-update-Resolve checkbox (left of the Reset buttons). When
        # off, edits in the editor stay in the editor and don't move the
        # actual clip in Resolve — useful for building a preset against a
        # running project without disturbing it.
        live_check = NSButton.alloc().initWithFrame_(
            NSMakeRect(detail_x, ty, 180, btn_h),
        )
        live_check.setButtonType_(3)  # NSSwitchButton
        live_check.setTitle_("Live update Resolve")
        live_check.setState_(1 if self._live_resolve_updates else 0)
        live_check.setTarget_(self)
        live_check.setAction_("liveResolveToggled:")
        live_check.setAutoresizingMask_(NSViewMinYMargin)
        detail_pane.addSubview_(live_check)
        self._live_resolve_check = live_check

        reset_all_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(right_x - btn_w, ty, btn_w, btn_h),
        )
        reset_all_btn.setTitle_("Reset All Tracks")
        reset_all_btn.setBezelStyle_(1)
        reset_all_btn.setTarget_(self)
        reset_all_btn.setAction_("resetAllTracks:")
        reset_all_btn.setAutoresizingMask_(TOP_RIGHT_PIN)
        detail_pane.addSubview_(reset_all_btn)

        reset_sel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(right_x - 2 * btn_w - 8, ty, btn_w, btn_h),
        )
        reset_sel_btn.setTitle_("Reset Selected")
        reset_sel_btn.setBezelStyle_(1)
        reset_sel_btn.setTarget_(self)
        reset_sel_btn.setAction_("resetSelectedTrack:")
        reset_sel_btn.setAutoresizingMask_(TOP_RIGHT_PIN)
        detail_pane.addSubview_(reset_sel_btn)
        ty -= 6  # small gap between the buttons and the preview

        # 2x2 quadrant preview fills the bottom of the detail pane.
        preview_h = max(120, ty - 4)
        preview_y = ty - preview_h
        self._quad_preview = QuadPreviewView.alloc().initWithFrame_(
            NSMakeRect(detail_x, preview_y, detail_w, preview_h),
        )
        self._quad_preview.setAutoresizingMask_(
            NSViewHeightSizable | NSViewWidthSizable
        )
        # Click on a quadrant in the preview = same as picking it in the popup.
        self._quad_preview.set_click_handler(self._on_quad_preview_click)
        detail_pane.addSubview_(self._quad_preview)

        # Working state: per-track transform dict, indexed by track index.
        # _track_data_rows mirrors the table data source ordering so we can
        # read/write by row index.
        self._track_data_rows: list[tuple[int, str]] = []  # (idx, name)
        self._track_working: dict = {}  # idx -> {"enabled": bool, **transform}
        self._populate_resolve_tracks()

        # ── Bottom buttons (right-aligned) ──
        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(win_w - 100, 20, 80, 32),
        )
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("save:")
        save_btn.setAutoresizingMask_(1)  # NSViewMinXMargin
        content.addSubview_(save_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(win_w - 188, 20, 80, 32),
        )
        cancel_btn.setTitle_("Close")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")  # Escape
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancel:")
        cancel_btn.setAutoresizingMask_(1)
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
        if self._macro.hotkey_modifier in MODIFIER_CHOICES:
            self._modifier_popup.selectItemAtIndex_(
                MODIFIER_CHOICES.index(self._macro.hotkey_modifier),
            )
        else:
            self._modifier_popup.selectItemAtIndex_(0)

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
    def _track_default_dict(self) -> dict:
        return {"enabled": False, **dict(_DEFAULT_TRANSFORM)}

    @objc.python_method
    def _populate_resolve_tracks(self) -> None:
        if self._cached_tracks is None:
            self._cached_tracks = resolve.safe_get_video_track_info() or []
        if self._cached_track_transforms is None:
            self._cached_track_transforms = (
                resolve.safe_get_video_track_transforms() or {}
            )
        # Refresh timeline resolution so quadrant offsets snap to the right
        # values for whichever timeline is open.
        try:
            self._tl_w, self._tl_h = resolve.safe_get_timeline_resolution()
        except Exception:
            pass
        live = self._cached_tracks
        live_xforms = self._cached_track_transforms
        seen: set[int] = set()
        rows: list[tuple[int, str]] = []
        live_state: dict[int, bool] = {}
        for info in live:
            rows.append((int(info["index"]), str(info["name"])))
            seen.add(int(info["index"]))
            live_state[int(info["index"])] = bool(info["enabled"])
        # Include any tracks the saved macro references that Resolve no
        # longer reports — keeps prior config visible after track removals.
        for idx in sorted(self._macro.resolve.tracks.keys()):
            if int(idx) not in seen:
                rows.append((int(idx), f"V{idx}"))
        # Resolve shows higher-numbered tracks at the TOP of the timeline,
        # so list V<n> first and V1 last to match.
        rows.sort(key=lambda r: r[0], reverse=True)

        # Build a name -> (enabled, transform) map from the saved macro,
        # using the saved-time track names. This is what lets settings
        # follow a track when its index shifts due to inserts/deletes in
        # Resolve. Falls back to legacy idx-based lookup for old configs
        # that pre-date track_names.
        saved_names = self._macro.resolve.track_names or {}
        saved_by_name: dict[str, tuple[bool, dict]] = {}
        for sidx, sname in saved_names.items():
            if not sname:
                continue
            enabled = bool(self._macro.resolve.tracks.get(int(sidx), False))
            xform = self._macro.resolve.track_transforms.get(int(sidx)) or {}
            saved_by_name[str(sname)] = (enabled, xform)

        # Per-track precedence for the working transform:
        #   1. saved data matched by NAME (highest, survives index drift)
        #   2. saved data matched by idx (legacy / unnamed configs)
        #   3. Resolve's current state for this track
        #   4. _DEFAULT_TRANSFORM
        self._track_working = {}
        for idx, name in rows:
            entry = self._track_default_dict()
            saved = saved_by_name.get(str(name))
            if saved is not None:
                saved_enabled, saved_xform = saved
            else:
                saved_enabled = self._macro.resolve.tracks.get(int(idx))
                saved_xform = self._macro.resolve.track_transforms.get(int(idx)) or {}
            if saved_enabled is None:
                saved_enabled = live_state.get(int(idx), False)
            entry["enabled"] = bool(saved_enabled)
            live_xform = live_xforms.get(int(idx)) or {}
            for k in entry:
                if k == "enabled":
                    continue
                if k in saved_xform:
                    entry[k] = saved_xform[k]
                elif k in live_xform:
                    entry[k] = live_xform[k]
            self._track_working[int(idx)] = entry

        # Push display rows: "V<idx> — name [✓]" or "V<idx> — name"
        self._track_data_rows = list(rows)
        self._track_data_source.entries = [
            (idx, self._format_track_label(idx, name))
            for idx, name in rows
        ]
        # Tight, fixed row height — no auto-fit padding between rows.
        try:
            self._track_table.setRowHeight_(22.0)
        except Exception:
            pass
        self._track_table.reloadData()

        if rows:
            self._track_table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(0), False,
            )
            self._populate_track_detail()
        else:
            # No tracks — clear the detail panel so the user sees an empty
            # state instead of stale values from a previous cell.
            self._track_enable.setEnabled_(False)
            self._quadrant_popup.setEnabled_(False)
            for fld in self._transform_fields.values():
                fld.setEnabled_(False)
            self._flip_h_check.setEnabled_(False)
            self._flip_v_check.setEnabled_(False)
            self._quad_preview.set_active(None, "(Resolve not running)")

    @objc.python_method
    def _format_track_label(self, idx: int, name: str) -> str:
        # Single space slot for the checkmark — figure space (U+2007) has
        # the same advance width as a digit in proportional fonts, which
        # is close enough to ✓ that the "V<idx>" text lines up either way.
        entry = self._track_working.get(int(idx)) or {}
        icon = "✓" if entry.get("enabled") else " "
        return f"{icon} V{idx} — {name}"

    @objc.python_method
    def _selected_track_index(self) -> int | None:
        if not hasattr(self, "_track_table"):
            return None
        row = int(self._track_table.selectedRow())
        if row < 0 or row >= len(self._track_data_rows):
            return None
        return self._track_data_rows[row][0]

    @objc.python_method
    def _populate_track_detail(self) -> None:
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.get(int(idx)) or self._track_default_dict()
        self._track_enable.setEnabled_(True)
        self._quadrant_popup.setEnabled_(True)
        for fld in self._transform_fields.values():
            fld.setEnabled_(True)
        self._flip_h_check.setEnabled_(True)
        self._flip_v_check.setEnabled_(True)

        self._track_enable.setState_(1 if entry.get("enabled") else 0)
        self._quadrant_popup.selectItemWithTitle_(entry.get("quadrant", "Q1"))
        for key, _, _ in _FLOAT_FIELDS:
            fld = self._transform_fields.get(key)
            if fld is not None:
                fld.setStringValue_(f"{float(entry.get(key, 0.0)):.3f}")
        self._flip_h_check.setState_(1 if entry.get("flip_h") else 0)
        self._flip_v_check.setState_(1 if entry.get("flip_v") else 0)

        # Update the quad preview with the chosen quadrant + track name.
        name = ""
        for tidx, tname in self._track_data_rows:
            if tidx == idx:
                name = tname
                break
        self._quad_preview.set_active(entry.get("quadrant", "Q1"),
                                     f"V{idx} — {name}")

    # ---- Split view delegate (track list / detail divider) ------------------

    def splitView_constrainMinCoordinate_ofSubviewAt_(self, sv, prop_min, idx):  # NOQA: N802
        # Keep the list pane at least 160px wide so it stays usable.
        return 160.0

    def splitView_constrainMaxCoordinate_ofSubviewAt_(self, sv, prop_max, idx):  # NOQA: N802
        # Keep the detail pane at least 470px wide — enough for both columns
        # of transform fields plus 16px of left/right padding.
        try:
            sv_w = float(sv.frame().size.width)
        except Exception:
            return float(prop_max)
        return max(160.0, sv_w - 470.0)

    # ---- Table delegate / control callbacks ---------------------------------

    def tableViewSelectionDidChange_(self, notification):  # NOQA: N802
        self._populate_track_detail()

    def tableView_shouldEditTableColumn_row_(self, tv, col, row):  # NOQA: N802
        # The track list is read-only — names come from Resolve.
        return False

    def tableArrowLeft_(self, sender) -> None:  # NOQA: N802
        self._step_quadrant(-1)

    def tableArrowRight_(self, sender) -> None:  # NOQA: N802
        self._step_quadrant(+1)

    @objc.python_method
    def _step_quadrant(self, step: int) -> None:
        """Cycle the selected track's quadrant Q1→Q2→Q3→Q4 by keyboard
        arrows. Snaps Position to the new quadrant's offset and pushes to
        Resolve so the video moves live."""
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        order = ["Q1", "Q2", "Q3", "Q4"]
        cur = entry.get("quadrant", "Q1")
        try:
            i = order.index(cur)
        except ValueError:
            i = 0
        new = order[(i + step) % len(order)]
        self._apply_quadrant_to_entry(int(idx), entry, new)
        try:
            self._quadrant_popup.selectItemWithTitle_(new)
        except Exception:
            pass
        self._populate_track_detail()
        self._apply_live_track_transform(int(idx))

    def tableEnterPressed_(self, sender) -> None:  # NOQA: N802
        """Pressing Enter / Return on the track list toggles the selected
        track's enabled flag (same as ticking the Enable checkbox)."""
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        new_state = not bool(entry.get("enabled"))
        entry["enabled"] = new_state
        try:
            self._track_enable.setState_(1 if new_state else 0)
        except Exception:
            pass
        # Live: push to Resolve.
        self._apply_live_track_enable(int(idx), new_state)
        # Refresh the row label so the leading "✓ " appears/disappears.
        for i, (tidx, tname) in enumerate(self._track_data_rows):
            if tidx == idx:
                self._track_data_source.entries[i] = (
                    tidx, self._format_track_label(tidx, tname),
                )
                self._track_table.reloadData()
                self._track_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(i), False,
                )
                break

    def trackEnableChanged_(self, sender) -> None:  # NOQA: N802
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        new_enabled = bool(int(sender.state()) == 1)
        entry["enabled"] = new_enabled
        # Live: flip the track in Resolve right now so the change is visible.
        self._apply_live_track_enable(int(idx), new_enabled)
        # Refresh the row label to add/remove the leading "✓".
        for i, (tidx, tname) in enumerate(self._track_data_rows):
            if tidx == idx:
                self._track_data_source.entries[i] = (
                    tidx, self._format_track_label(tidx, tname),
                )
                self._track_table.reloadData()
                self._track_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(i), False,
                )
                break

    def quadrantChanged_(self, sender) -> None:  # NOQA: N802
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        quad = str(sender.titleOfSelectedItem() or "Q1")
        self._apply_quadrant_to_entry(int(idx), entry, quad)
        self._populate_track_detail()
        self._apply_live_track_transform(int(idx))

    @objc.python_method
    def _on_quad_preview_click(self, quad: str) -> None:
        """Click in the QuadPreviewView = same effect as picking the quadrant
        from the popup. Snaps Position to the new quadrant's offset and
        pushes to Resolve so the video moves."""
        if quad not in ("Q1", "Q2", "Q3", "Q4"):
            return
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        self._apply_quadrant_to_entry(int(idx), entry, quad)
        try:
            self._quadrant_popup.selectItemWithTitle_(quad)
        except Exception:
            pass
        self._populate_track_detail()
        self._apply_live_track_transform(int(idx))

    def flipChanged_(self, sender) -> None:  # NOQA: N802
        idx = self._selected_track_index()
        if idx is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        if sender is self._flip_h_check:
            entry["flip_h"] = bool(int(sender.state()) == 1)
        elif sender is self._flip_v_check:
            entry["flip_v"] = bool(int(sender.state()) == 1)
        self._apply_live_track_transform(int(idx))

    @objc.python_method
    def _reset_track_to_live(self, idx: int) -> None:
        """Restore <idx>'s working transform to the snapshot originally
        pulled from Resolve at editor-open time. Enabled flag is preserved.
        """
        live = (self._cached_track_transforms or {}).get(int(idx)) or {}
        prev_enabled = bool((self._track_working.get(int(idx)) or {}).get("enabled"))
        new_entry = self._track_default_dict()
        new_entry.update(live)
        new_entry["enabled"] = prev_enabled
        self._track_working[int(idx)] = new_entry

    def liveResolveToggled_(self, sender) -> None:  # NOQA: N802
        self._live_resolve_updates = bool(int(sender.state()) == 1)
        print(f"[editor] live Resolve updates: "
              f"{'ON' if self._live_resolve_updates else 'OFF'}")

    def resetSelectedTrack_(self, sender) -> None:  # NOQA: N802
        idx = self._selected_track_index()
        if idx is None:
            return
        self._reset_track_to_live(idx)
        self._populate_track_detail()
        self._apply_live_track_transform(int(idx))

    def resetAllTracks_(self, sender) -> None:  # NOQA: N802
        for idx in list(self._track_working.keys()):
            self._reset_track_to_live(idx)
        self._populate_track_detail()
        if not self._live_resolve_updates:
            return
        # Push every track in one go.
        try:
            bulk = {
                int(idx): {k: v for k, v in entry.items() if k != "enabled"}
                for idx, entry in self._track_working.items()
            }
            resolve.safe_apply_video_track_transforms(bulk)
        except Exception as e:
            print(f"[editor] reset-all live push failed: {e}")

    @objc.python_method
    def _on_scrub_field(self, field, new_val: float, is_final: bool) -> None:
        """Drag-scrub callback for a transform field. Updates the working
        dict on every drag tick; only pushes to Resolve on mouse-up so we
        don't flood the Fusion bridge."""
        idx = self._selected_track_index()
        if idx is None:
            return
        key = None
        for k, fld in self._transform_fields.items():
            if fld is field:
                key = k
                break
        if key is None:
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        entry[key] = float(new_val)
        if is_final:
            self._apply_live_track_transform(int(idx))

    @objc.python_method
    def _quadrant_offsets(self, quad: str) -> tuple[float, float]:
        """Pixel offsets (Pan, Tilt) from timeline center for each quadrant.

        Resolve's Tilt convention is math-style: POSITIVE = clip moves UP,
        NEGATIVE = DOWN. So the top row (Q1, Q2) takes POSITIVE Tilt and
        the bottom row (Q3, Q4) takes NEGATIVE Tilt. Magnitudes are
        (tl_w/2, tl_h/2) for a 4-up layout in the current timeline (the
        user's 4K project lines up at ±1920, ±1080).
        """
        qw = float(self._tl_w) / 2.0
        qh = float(self._tl_h) / 2.0
        return {
            "Q1": (-qw, +qh),  # top-left     — Pan left,  Tilt up
            "Q2": (+qw, +qh),  # top-right    — Pan right, Tilt up
            "Q3": (-qw, -qh),  # bottom-left  — Pan left,  Tilt down
            "Q4": (+qw, -qh),  # bottom-right — Pan right, Tilt down
        }.get(quad, (0.0, 0.0))

    @objc.python_method
    def _apply_quadrant_to_entry(self, idx: int, entry: dict, new_quad: str) -> None:
        """Snap position_x / position_y to the new quadrant's canonical
        offset and update the quadrant label. Other transform fields (Zoom,
        Rotation, Anchor, Pitch, Yaw, Flip) are NOT touched.

        This is two-way authoritative: the popup / preview-click / arrow-keys
        ARE the user saying "put this clip in this quadrant", so the position
        always lands on that quadrant's center. If the user wants a custom
        offset they edit Position X / Y directly afterwards.
        """
        ox, oy = self._quadrant_offsets(new_quad)
        entry["quadrant"] = new_quad
        entry["position_x"] = ox
        entry["position_y"] = oy
        print(f"[editor] V{idx} → {new_quad}: pos=({ox:.0f}, {oy:.0f})")

    @objc.python_method
    def _apply_live_track_transform(self, idx: int) -> None:
        """Push the working transform for <idx> to Resolve so the user sees
        the change on the timeline as they tweak. Skipped when the editor's
        'Live update Resolve' checkbox is off."""
        if not self._live_resolve_updates:
            return
        entry = self._track_working.get(int(idx))
        if not entry:
            return
        xform = {k: v for k, v in entry.items() if k != "enabled"}
        try:
            resolve.safe_apply_video_track_transforms({int(idx): xform})
        except Exception as e:
            print(f"[editor] live transform V{idx} failed: {e}")

    @objc.python_method
    def _apply_live_track_enable(self, idx: int, enabled: bool) -> None:
        if not self._live_resolve_updates:
            return
        try:
            resolve.safe_apply_track_state({int(idx): bool(enabled)})
        except Exception as e:
            print(f"[editor] live enable V{idx} failed: {e}")

    @objc.python_method
    def _stage_transform_field(self, field) -> None:
        """Pull the field's value back into the working transform dict."""
        idx = self._selected_track_index()
        if idx is None:
            return
        # Reverse-lookup which transform key this field is.
        key = None
        for k, fld in self._transform_fields.items():
            if fld is field:
                key = k
                break
        if key is None:
            return
        try:
            val = float(field.stringValue())
        except (ValueError, TypeError):
            return
        entry = self._track_working.setdefault(int(idx), self._track_default_dict())
        entry[key] = val

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

    def modifierChanged_(self, sender) -> None:  # NOQA: N802
        idx = sender.indexOfSelectedItem()
        mod = MODIFIER_CHOICES[idx] if 0 <= idx < len(MODIFIER_CHOICES) else ""
        self._macro.hotkey_modifier = mod
        self._stage_live("hotkey_modifier", mod)

    def videohubMacroEnableChanged_(self, sender) -> None:  # NOQA: N802
        self._macro.videohub_enabled = bool(int(sender.state()) == 1)
        self._apply_videohub_field_state()

    @objc.python_method
    def _apply_videohub_field_state(self) -> None:
        """Grey out the Device + Preset popups when the per-macro Enable
        checkbox is off. Underlying macro.videohub (device_id + preset_name)
        is left untouched, so re-checking Enable restores whatever was
        previously selected."""
        usable = bool(self._macro.videohub_enabled)
        try:
            self._device_popup.setEnabled_(usable)
            self._preset_popup.setEnabled_(usable)
        except Exception:
            pass
        if not usable:
            try:
                self._device_popup.removeAllItems()
                self._device_popup.addItemWithTitle_("Disabled")
                self._preset_popup.removeAllItems()
                self._preset_popup.addItemWithTitle_("Disabled")
            except Exception:
                pass
        else:
            # Re-populate from the saved macro.videohub fields, which we
            # never wiped — so the previous device/preset selection comes
            # back automatically.
            try:
                self._populate_devices()
                self._populate_presets_for_current_device()
            except Exception:
                pass

    def controlTextDidChange_(self, notification) -> None:  # NOQA: N802
        # NSTextField delegate hook — fires on every keystroke in the label
        # AND on every keystroke in any of the per-track transform fields
        # (we tag those with _TRANSFORM_FIELD_TAG so we can tell them apart).
        obj = notification.object()
        if obj is self._label_field:
            label = str(obj.stringValue() or "")
            self._macro.label = label
            self._stage_live("label", label)
            return
        try:
            tag = int(obj.tag())
        except Exception:
            tag = 0
        if tag == _TRANSFORM_FIELD_TAG:
            self._stage_transform_field(obj)

    def controlTextDidEndEditing_(self, notification) -> None:  # NOQA: N802
        # Push the just-committed transform value to Resolve. We push on
        # end-of-edit (Tab / Enter / focus loss) rather than on every
        # keystroke so a user typing "1920" doesn't trigger four writes.
        obj = notification.object()
        if obj is self._label_field:
            return
        try:
            tag = int(obj.tag())
        except Exception:
            return
        if tag != _TRANSFORM_FIELD_TAG:
            return
        idx = self._selected_track_index()
        if idx is None:
            return
        # Make sure the just-typed value is in the working dict before push.
        self._stage_transform_field(obj)
        self._apply_live_track_transform(int(idx))
        # Hand keyboard focus back to the track table so the user can keep
        # navigating with Up/Down/Left/Right + Enter (which otherwise stay
        # trapped in the field's NSText field editor).
        try:
            if hasattr(self, "_track_table") and self._track_table is not None:
                self.makeFirstResponder_(self._track_table)
        except Exception:
            pass

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
        self._vh_enable_check.setState_(
            1 if self._macro.videohub_enabled else 0,
        )
        self._populate_devices()
        self._populate_presets_for_current_device()
        self._apply_videohub_field_state()
        # Re-pull Resolve track info on every cell navigation so renames /
        # inserted-or-deleted tracks reflect immediately. The Fusion bridge
        # call costs ~1-2 seconds; acceptable per-nav UX.
        self._cached_tracks = None
        self._cached_track_transforms = None
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
        mod_idx = self._modifier_popup.indexOfSelectedItem()
        hotkey_mod = (MODIFIER_CHOICES[mod_idx]
                      if 0 <= mod_idx < len(MODIFIER_CHOICES) else "")
        vh_macro_on = bool(int(self._vh_enable_check.state()) == 1)
        device_id = self._selected_device_id()
        preset_idx = self._preset_popup.indexOfSelectedItem()
        preset_name = ""
        if preset_idx > 0:
            preset_name = str(self._preset_popup.titleOfSelectedItem() or "")
        # Track state lives in self._track_working, indexed by track idx.
        # We also capture the current Resolve name for each idx so saved
        # transforms can later be re-bound by name (and survive index drift).
        name_by_idx = {
            int(info["index"]): str(info.get("name") or "")
            for info in (self._cached_tracks or [])
        }
        tracks_state = []
        for idx, entry in sorted(self._track_working.items()):
            xform = tuple(sorted(
                (k, v) for k, v in entry.items() if k != "enabled"
            ))
            name = (name_by_idx.get(int(idx))
                    or self._macro.resolve.track_names.get(int(idx)) or "")
            tracks_state.append(
                (int(idx), bool(entry.get("enabled")), xform, name),
            )
        tracks_fs = tuple(tracks_state)
        return (label, color, hotkey, hotkey_mod, vh_macro_on,
                device_id, preset_name, tracks_fs)

    @objc.python_method
    def _commit_to_store(self, persist: bool = True) -> None:
        current = self._capture_form_state()
        if current == self._snapshot:
            # Nothing changed since this cell was loaded — don't touch the
            # store. Prevents passing through cells from creating phantom
            # macros, while still keeping any real edits the user made.
            return
        (label, color, hotkey, hotkey_mod, vh_macro_on,
         device_id, preset_name, tracks_fs) = current
        self._macro.label = label
        self._macro.color = color
        self._macro.hotkey = hotkey
        self._macro.hotkey_modifier = hotkey_mod
        self._macro.videohub_enabled = vh_macro_on
        self._macro.videohub = VideohubAction(
            device_id=device_id, preset_name=preset_name,
        )
        # Split working entries into the enable map + per-track transforms
        # + per-track name (for index-drift-tolerant re-bind on next open).
        tracks_map: dict[int, bool] = {}
        transforms_map: dict[int, dict] = {}
        names_map: dict[int, str] = {}
        for idx, enabled, xform, name in tracks_fs:
            tracks_map[int(idx)] = bool(enabled)
            xform_dict = dict(xform)
            # Skip storing a transform that's still 100% defaults — keeps
            # macroflow.json compact for the common case.
            if any(xform_dict.get(k) != _DEFAULT_TRANSFORM.get(k)
                   for k in xform_dict):
                transforms_map[int(idx)] = xform_dict
            if name:
                names_map[int(idx)] = name
        self._macro.resolve = ResolveAction(
            tracks=tracks_map,
            track_transforms=transforms_map,
            track_names=names_map,
        )
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
        ctrl = self._controller
        row, col = self._row, self._col
        # Snapshot the prior macro (as plain dict) so undo can restore it.
        prev = ctrl._store.grid.get(row, col)
        prev_dict = prev.to_dict() if prev is not None else None

        self._macro = Macro(id=ctrl._store.grid.cell_id(row, col))
        ctrl._store.grid.clear(row, col)
        ctrl._store.save()
        self._reload_fields()  # also resets the snapshot
        ctrl._refresh_cell_titles()

        def _undo():
            if prev_dict is not None:
                ctrl._store.grid.set(row, col, Macro.from_dict(prev_dict))
            else:
                ctrl._store.grid.clear(row, col)
            ctrl._store.save()
            ctrl._refresh_cell_titles()

        def _redo():
            ctrl._store.grid.clear(row, col)
            ctrl._store.save()
            ctrl._refresh_cell_titles()

        try:
            ctrl.push_undo(f"clear cell R{row+1}C{col+1}", _undo, _redo)
        except Exception:
            pass
