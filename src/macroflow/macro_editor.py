"""Macro editor sheet: pick a Videohub preset + per-track Resolve enable state.

Opened from the main grid by double-clicking or right-clicking a cell.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

from AppKit import (
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSButton,
    NSColor,
    NSMakeRect,
    NSPopUpButton,
    NSScrollView,
    NSStackView,
    NSStackViewDistributionFill,
    NSTextField,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from objc import super  # type: ignore

from macroflow.backends import resolve, videohub
from macroflow.macro import Macro, ResolveAction, VideohubAction


class MacroEditorWindow(NSWindow):
    """A modal-ish editor for a single Macro."""

    def initWithMacro_(self, macro: Macro):  # NOQA: N802
        rect = NSMakeRect(0, 0, 520, 520)
        style = (NSWindowStyleMaskTitled
                 | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskResizable)
        self = super().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False,
        )
        if self is None:
            return None
        self.setTitle_(f"Edit Macro — {macro.id}")
        self._macro = macro
        self._on_save = None
        self._build_ui()
        self.center()
        return self

    def setSaveCallback_(self, fn) -> None:  # NOQA: N802
        self._on_save = fn

    # -- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        content = self.contentView()
        if content is None:
            return

        # Label
        y = 480
        self._label_field = self._add_labeled_text(
            content, "Label:", self._macro.label, x=20, y=y, width=480,
        )
        y -= 50

        # Videohub: device + preset
        self._add_section_header(content, "Videohub", x=20, y=y)
        y -= 28
        self._device_popup = self._add_popup(content, x=120, y=y, width=380)
        self._add_label(content, "Device:", x=20, y=y + 4)
        self._populate_devices()
        y -= 36
        self._preset_popup = self._add_popup(content, x=120, y=y, width=380)
        self._add_label(content, "Preset:", x=20, y=y + 4)
        self._device_popup.setTarget_(self)
        self._device_popup.setAction_("deviceChanged:")
        self._populate_presets_for_current_device()
        y -= 50

        # Resolve: per-track checkboxes (scrollable)
        self._add_section_header(content, "DaVinci Resolve video tracks", x=20, y=y)
        y -= 28

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(20, 80, 480, y - 60),
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutoresizingMask_(NSViewWidthSizable)
        track_stack = NSStackView.alloc().initWithFrame_(
            NSMakeRect(0, 0, 460, 100),
        )
        track_stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        track_stack.setDistribution_(NSStackViewDistributionFill)
        track_stack.setSpacing_(4)
        scroll.setDocumentView_(track_stack)
        content.addSubview_(scroll)
        self._track_stack = track_stack
        self._track_checkboxes: dict[int, NSButton] = {}
        # Tri-state per track: 0=unchanged, 1=force on, -1=force off.
        # Resolve isn't necessarily running at edit time, so we render the
        # currently-saved state regardless of whether we can probe live tracks.
        self._populate_resolve_tracks()

        # Buttons
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(420, 20, 80, 32))
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)  # rounded
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("save:")
        content.addSubview_(save_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(330, 20, 80, 32))
        cancel_btn.setTitle_("Cancel")
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

    def _add_label(self, parent: NSView, text: str, x: float, y: float) -> NSTextField:
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 100, 20))
        f.setStringValue_(text)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        parent.addSubview_(f)
        return f

    def _add_section_header(self, parent: NSView, text: str, x: float, y: float):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 480, 22))
        f.setStringValue_(text)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        f.setTextColor_(NSColor.secondaryLabelColor())
        parent.addSubview_(f)
        return f

    def _add_labeled_text(self, parent, label: str, value: str,
                          x: float, y: float, width: float) -> NSTextField:
        self._add_label(parent, label, x, y + 4)
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x + 100, y, width - 100, 22))
        field.setStringValue_(value)
        parent.addSubview_(field)
        return field

    def _add_popup(self, parent: NSView, x: float, y: float, width: float) -> NSPopUpButton:
        popup = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(x, y, width, 26))
        parent.addSubview_(popup)
        return popup

    # -- Population -----------------------------------------------------------

    def _populate_devices(self) -> None:
        self._device_popup.removeAllItems()
        self._device_popup.addItemWithTitle_("(none)")
        self._devices = videohub.list_devices()
        for d in self._devices:
            self._device_popup.addItemWithTitle_(f"{d.display_name} — {d.ip}")
        # Select current
        current = self._macro.videohub.device_id
        if current:
            for i, d in enumerate(self._devices, start=1):
                if d.unique_id == current:
                    self._device_popup.selectItemAtIndex_(i)
                    return
        self._device_popup.selectItemAtIndex_(0)

    def _selected_device_id(self) -> str:
        idx = self._device_popup.indexOfSelectedItem()
        if idx <= 0 or idx > len(self._devices):
            return ""
        return self._devices[idx - 1].unique_id

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

    def _populate_resolve_tracks(self) -> None:
        # Remove any existing checkboxes
        for v in list(self._track_stack.arrangedSubviews()):
            self._track_stack.removeArrangedSubview_(v)
            v.removeFromSuperview()
        self._track_checkboxes.clear()

        live = resolve.get_video_track_info()
        # Merge live tracks with whatever the macro already has saved, so a
        # macro authored offline still shows its tracks even when Resolve isn't
        # running.
        seen: set[int] = set()
        rows: list[tuple[int, str, bool]] = []
        for info in live:
            rows.append((info["index"], info["name"], info["enabled"]))
            seen.add(int(info["index"]))
        for idx, _ in sorted(self._macro.resolve.tracks.items()):
            if int(idx) not in seen:
                rows.append((int(idx), f"V{idx}", True))
        rows.sort(key=lambda r: r[0])

        if not rows:
            note = self._add_label(
                self._track_stack,
                "(Resolve not running — tracks will populate when connected)",
                x=8, y=0,
            )
            note.setTextColor_(NSColor.secondaryLabelColor())
            return

        for idx, name, _live_enabled in rows:
            cb = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 460, 22))
            cb.setButtonType_(3)  # NSSwitchButton
            cb.setTitle_(f"V{idx} — {name}")
            cb.setAllowsMixedState_(True)  # Off=force-off, Mixed=untouched, On=force-on
            saved = self._macro.resolve.tracks.get(int(idx))
            if saved is None:
                cb.setState_(-1)  # NSMixedState (untouched)
            else:
                cb.setState_(1 if saved else 0)
            self._track_stack.addArrangedSubview_(cb)
            self._track_checkboxes[int(idx)] = cb

    # -- Actions --------------------------------------------------------------

    def deviceChanged_(self, sender) -> None:  # NOQA: N802 (Cocoa selector)
        self._populate_presets_for_current_device()

    def save_(self, sender) -> None:  # NOQA: N802
        self._macro.label = str(self._label_field.stringValue() or "")
        device_id = self._selected_device_id()
        preset_idx = self._preset_popup.indexOfSelectedItem()
        preset_name = ""
        if preset_idx > 0:
            preset_name = str(self._preset_popup.titleOfSelectedItem() or "")
        self._macro.videohub = VideohubAction(
            device_id=device_id, preset_name=preset_name,
        )
        tracks: dict[int, bool] = {}
        for idx, cb in self._track_checkboxes.items():
            state = int(cb.state())
            if state == -1:
                continue  # untouched
            tracks[idx] = (state == 1)
        self._macro.resolve = ResolveAction(tracks=tracks)
        if self._on_save is not None:
            self._on_save(self._macro)
        self.close()

    def cancel_(self, sender) -> None:  # NOQA: N802
        self.close()

    def clearCell_(self, sender) -> None:  # NOQA: N802
        self._macro.label = ""
        self._macro.videohub = VideohubAction()
        self._macro.resolve = ResolveAction()
        if self._on_save is not None:
            self._on_save(self._macro)
        self.close()
