"""MacroFlow Manual & Specs window.

Read-only scrollable text view shown from the Help menu. Content is the
full manual + technical specs so a user can self-serve before filing a
bug report.

This Script and Code created by:
Chad Littlepage
chad.littlepage@gmail.com
323.974.0444
"""

from __future__ import annotations

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

from macroflow import __version__

_RETAINED: list = []


_MANUAL = f"""MacroFlow {__version__}
Macro grid for DaVinci Resolve and Blackmagic Videohub
========================================================

OVERVIEW
--------
MacroFlow is a clickable grid of macro cells. Each cell can fire one or
more backend actions in parallel from a single click or hotkey. The two
backends shipped today are:

  • Videohub  — recall a saved preset on a Blackmagic Videohub router.
  • DaVinci Resolve — enable / disable a chosen subset of video tracks
                       on the current timeline.

Both run on independent worker threads so a slow Resolve query never
blocks a Videohub recall (or vice-versa).

THE GRID
--------
The default grid is 4×4 (16 cells). Cells size and spacing automatically
recompute when you resize the window.

  • Click a cell                Fire its macro.
  • Cmd+Click (or Ctrl+Click)   Open the macro editor for that cell.
  • Press a cell's hotkey       Fire that cell from anywhere in the app.
  • Cmd+F                       Toggle native macOS full-screen.
                                Toggling out restores the previous size.

Status row beneath the LCD strip shows two indicator dots:
  • VIDEOHUB        green when a Videohub Controller / router is reachable.
  • DAVINCI RESOLVE green when the Resolve scripting bridge is responding.

The LCD strip echoes the most recent action and any hover descriptions.

THE MACRO EDITOR
----------------
Open with Cmd+Click. The editor stays open across cells; use ◀ / ▶ in the
top right to step through every cell in the grid. Auto-save runs on
navigation, so you can sweep through cells without explicit Save clicks.

Fields:
  • Label    Free-text name shown on the cell.
  • Color    sRGB hex; the cell paints this color in real time as you pick.
             A non-default color alone is enough to tint the cell.
  • Hotkey   Single key (a–z, 0–9) or function key (F1–F12). Live preview
             on the cell. Hotkeys are app-local — they only fire while
             MacroFlow is the key application.
  • Videohub Pick a router (auto-discovered from Videohub Controller's
             config) and one of its saved presets.
  • Resolve  Per-track ON/OFF checkbox list. Tracks not toggled in the
             editor are left untouched when the macro fires.

Buttons:
  • Save        Persist the current cell to disk.
  • Close       Close the editor without saving (color edits ARE retained
                in-memory until the next launch — Save first to persist).
  • Clear cell  Wipe this cell back to empty and persist immediately.

FIRING SEMANTICS
----------------
Each macro fires its non-empty actions in parallel. Empty actions
(no Videohub preset, empty Resolve track map) are skipped. Each backend
is given up to 5 seconds to complete; a slow backend doesn't block the
others. The result LCD message reports each backend's success/failure.

HOTKEYS
-------
The hotkey monitor is a local NSEvent monitor — it only fires when the
MacroFlow window is the key window. Modifier keys (Cmd / Ctrl / Opt) are
ignored so they remain available for menu shortcuts. The function-key
table is `_FN_KEYCODES` in `app.py` if you need to extend it.

SETTINGS  (Cmd+,)
-----------------
Three sliders adjust on-cell typography:
  • Display    LCD strip font size.
  • Title      Cell label font size.
  • Hotkey     Big hotkey-glyph font size.

Reset to Defaults restores 12 / 13 / 26 pt.

CONSOLE  (Help → Show Console, Cmd+Shift+C)
-------------------------------------------
A live tail of every print / traceback the app emits. Use Export to write
the buffer to a timestamped .txt — attach that file when reporting a bug.
The buffer holds the most recent 10,000 lines; older ones roll off.

VIDEOHUB BACKEND
----------------
MacroFlow reads its router list and presets straight from Videohub
Controller's config:

   /Users/Shared/Videohub Controller/videohub_controller.json

Recall path is direct TCP/9990 to the router. There is also a fallback
that hands the recall off to a running VHC via NSDistributedNotification
(`init_bridge()` in `backends/videohub.py`).

Mock mode (`mock_videohub: true` in macroflow.json) bypasses the network
entirely and just logs the call — useful for editor smoke tests.

DAVINCI RESOLVE BACKEND
-----------------------
The bridge is loaded from one of these paths (first match wins):

   $RESOLVE_SCRIPT_API/Modules/DaVinciResolveScript.py
   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/
       Scripting/Modules/DaVinciResolveScript.py
   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/
       Modules/Lua/DaVinciResolveScript.py
   ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/
       Modules/Lua/DaVinciResolveScript.py

All Resolve calls are wrapped through `_run_off_main` so a stuck Fusion
bridge can't corrupt the Cocoa autoreleasepool on the main thread. Each
call has a 5-second timeout.

STORAGE & PERMISSIONS
---------------------
Macros and grid settings are persisted to:

   /Users/Shared/MacroFlow/macroflow.json

The directory is forced 0o777 and the file 0o666 so any user account on
the Mac (admin or standard) can read/write the same grid. This mirrors
Videohub Controller's behavior.

JSON shape:
   {{
     "rows": 4, "cols": 4, "mock_videohub": false,
     "font_sizes": {{"display": 12, "title": 13, "hotkey": 26}},
     "macros": {{
       "0,0": {{
         "id": "0,0", "label": "TEST 1", "color": "#a4a833",
         "hotkey": "1",
         "videohub": {{"device_id": "...", "preset_name": "1 Preset"}},
         "resolve":  {{"tracks": {{"1": true, "3": false}}}}
       }}
     }}
   }}

UI COLOR PALETTE  (matches Videohub Controller)
-----------------------------------------------
  • Window bg     calibrated (0.17, 0.17, 0.17) — renders ~#4a in DarkAqua.
  • Top header    sRGB #131313.
  • Macro cells   sRGB #494949.
  • LCD strip     sRGB #1a2117 with warm-yellow text (0.90, 0.78, 0.10).

DarkAqua is forced on every NSWindow. Without it, calibrated values
gamma-shift and the app no longer matches VHC side-by-side.

KEYBOARD QUICK REFERENCE
------------------------
  Cmd+,           Settings (font sizes)
  Cmd+F           Toggle full-screen
  Cmd+Shift+C     Show console
  Cmd+H           Hide MacroFlow
  Cmd+Q           Quit
  Cmd+Shift+E     Export Settings…
  Cmd+Shift+I     Import Settings…
  ◀ / ▶  (editor) Step through cells (auto-saves the current cell first)
  Esc             Close editor
  Return          Save (in editor)

REPORTING BUGS
--------------
   1. Open Help → Show Console.
   2. Reproduce the issue.
   3. Click Export… and save the log somewhere you can attach it.
   4. Email the log to chad.littlepage@gmail.com along with a one-line
      description of what you did and what you expected.

SUPPORT
-------
Chad Littlepage — chad.littlepage@gmail.com — 323.974.0444
"""


class _HelpController(NSObject):
    def init(self):
        self = objc.super(_HelpController, self).init()
        if self is not None:
            self.window = None
        return self

    def closeClicked_(self, sender):  # NOQA: N802
        if self.window:
            self.window.close()


def show_help_window() -> None:
    if _RETAINED:
        ctrl, win = _RETAINED[-1]
        win.makeKeyAndOrderFront_(None)
        return

    controller = _HelpController.alloc().init()
    win_w, win_h = 720, 640
    style = (NSWindowStyleMaskTitled
             | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable
             | NSWindowStyleMaskResizable)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, win_w, win_h), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("MacroFlow Manual")
    window.setReleasedWhenClosed_(False)
    window.setMinSize_(NSMakeSize(480, 320))
    window.center()
    controller.window = window
    content = window.contentView()

    BTN_H = 32
    close_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(win_w - 116, 16, 100, BTN_H),
    )
    close_btn.setTitle_("Close")
    close_btn.setBezelStyle_(1)
    close_btn.setKeyEquivalent_("\r")
    close_btn.setTarget_(controller)
    close_btn.setAction_("closeClicked:")
    close_btn.setAutoresizingMask_(1)
    content.addSubview_(close_btn)

    text_y = 16 + BTN_H + 12
    scroll = NSScrollView.alloc().initWithFrame_(
        NSMakeRect(16, text_y, win_w - 32, win_h - text_y - 16),
    )
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)
    scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

    tv = NSTextView.alloc().initWithFrame_(scroll.bounds())
    tv.setEditable_(False)
    tv.setSelectable_(True)
    tv.setRichText_(False)
    tv.setAutoresizingMask_(NSViewWidthSizable)
    tv.setFont_(NSFont.fontWithName_size_("Menlo", 12)
                or NSFont.userFixedPitchFontOfSize_(12))
    tv.setBackgroundColor_(NSColor.textBackgroundColor())
    tv.setTextColor_(NSColor.textColor())
    tv.setString_(_MANUAL)
    scroll.setDocumentView_(tv)
    content.addSubview_(scroll)

    window.makeKeyAndOrderFront_(None)
    if hasattr(NSApp, "activate"):
        NSApp.activate()
    else:
        NSApp.activateIgnoringOtherApps_(True)

    _RETAINED.clear()
    _RETAINED.append((controller, window))
