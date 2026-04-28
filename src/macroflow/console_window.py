"""Console window: live capture of stdout/stderr with Copy + Export.

Open from the Help menu (Cmd+Shift+C). New log lines append in real time;
Export writes the buffer to a timestamped .txt for bug reports.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

import time
from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSMakeSize,
    NSObject,
    NSPasteboard,
    NSPasteboardTypeString,
    NSSavePanel,
    NSScrollView,
    NSTextView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from PyObjCTools import AppHelper

from macroflow import log_capture

_RETAINED: list = []  # singleton — only keep the latest window


class _ConsoleController(NSObject):
    def init(self):
        self = objc.super(_ConsoleController, self).init()
        if self is not None:
            self.window = None
            self.text_view = None
        return self

    @objc.python_method
    def append_line(self, line: str) -> None:
        # Called from the log_capture observer (any thread). Marshal to main.
        AppHelper.callAfter(self._append_main, line + "\n")

    @objc.python_method
    def _append_main(self, text: str) -> None:
        if self.text_view is None:
            return
        storage = self.text_view.textStorage()
        if storage is None:
            return
        from AppKit import NSAttributedString
        storage.appendAttributedString_(NSAttributedString.alloc().initWithString_(text))
        self.text_view.scrollToEndOfDocument_(None)

    def clearClicked_(self, sender):  # NOQA: N802
        log_capture.clear()
        if self.text_view is not None:
            self.text_view.setString_("")

    def copyClicked_(self, sender):  # NOQA: N802
        if self.text_view is None:
            return
        text = str(self.text_view.string() or "")
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

    def exportClicked_(self, sender):  # NOQA: N802
        panel = NSSavePanel.savePanel()
        panel.setTitle_("Export MacroFlow Console Log")
        panel.setAllowedFileTypes_(["txt"])
        ts = time.strftime("%Y%m%d-%H%M%S")
        panel.setNameFieldStringValue_(f"macroflow-console-{ts}.txt")
        if int(panel.runModal()) != 1:
            return
        url = panel.URL()
        if url is None:
            return
        path = Path(str(url.path()))
        body = ""
        if self.text_view is not None:
            body = str(self.text_view.string() or "")
        if not body:
            body = "\n".join(log_capture.snapshot())
        try:
            path.write_text(body)
        except Exception as e:
            print(f"[console] failed to write {path}: {e}")


def show_console_window() -> None:
    # Reuse if already open.
    if _RETAINED:
        ctrl, win = _RETAINED[-1]
        win.makeKeyAndOrderFront_(None)
        return

    controller = _ConsoleController.alloc().init()
    win_w, win_h = 760, 480
    style = (NSWindowStyleMaskTitled
             | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable
             | NSWindowStyleMaskResizable)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, win_w, win_h), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("MacroFlow Console")
    window.setReleasedWhenClosed_(False)
    window.setMinSize_(NSMakeSize(480, 240))
    window.center()
    controller.window = window

    content = window.contentView()

    # Buttons strip at the bottom.
    BTN_H = 32
    BTN_Y = 16
    clear_btn = NSButton.alloc().initWithFrame_(NSMakeRect(16, BTN_Y, 80, BTN_H))
    clear_btn.setTitle_("Clear")
    clear_btn.setBezelStyle_(1)
    clear_btn.setTarget_(controller)
    clear_btn.setAction_("clearClicked:")
    content.addSubview_(clear_btn)

    copy_btn = NSButton.alloc().initWithFrame_(NSMakeRect(104, BTN_Y, 100, BTN_H))
    copy_btn.setTitle_("Copy All")
    copy_btn.setBezelStyle_(1)
    copy_btn.setTarget_(controller)
    copy_btn.setAction_("copyClicked:")
    content.addSubview_(copy_btn)

    export_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(win_w - 116, BTN_Y, 100, BTN_H),
    )
    export_btn.setTitle_("Export…")
    export_btn.setBezelStyle_(1)
    export_btn.setKeyEquivalent_("\r")
    export_btn.setTarget_(controller)
    export_btn.setAction_("exportClicked:")
    export_btn.setAutoresizingMask_(1)  # NSViewMinXMargin: stick to right edge
    content.addSubview_(export_btn)

    # Scrollable text view above the buttons.
    text_y = BTN_Y + BTN_H + 12
    scroll = NSScrollView.alloc().initWithFrame_(
        NSMakeRect(16, text_y, win_w - 32, win_h - text_y - 16),
    )
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)  # NSBezelBorder
    scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

    tv = NSTextView.alloc().initWithFrame_(scroll.bounds())
    tv.setEditable_(False)
    tv.setSelectable_(True)
    tv.setRichText_(False)
    tv.setAutoresizingMask_(NSViewWidthSizable)
    tv.setFont_(NSFont.fontWithName_size_("Menlo", 11)
                or NSFont.userFixedPitchFontOfSize_(11))
    tv.setBackgroundColor_(NSColor.textBackgroundColor())
    tv.setTextColor_(NSColor.textColor())
    scroll.setDocumentView_(tv)
    content.addSubview_(scroll)
    controller.text_view = tv

    # Seed with whatever's already buffered.
    seed = "\n".join(log_capture.snapshot())
    if seed:
        tv.setString_(seed + "\n")
        tv.scrollToEndOfDocument_(None)

    log_capture.add_observer(controller.append_line)

    window.makeKeyAndOrderFront_(None)
    if hasattr(NSApp, "activate"):
        NSApp.activate()
    else:
        NSApp.activateIgnoringOtherApps_(True)

    _RETAINED.clear()
    _RETAINED.append((controller, window))
