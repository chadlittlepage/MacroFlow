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
MacroFlow is a clickable grid of macro cells. Each cell fires one or
more backend actions in parallel from a single click, hotkey, or
keyboard nav. The two backends today are:

  • Videohub  — recall a saved preset on a Blackmagic Videohub router.
  • DaVinci Resolve — enable / disable a chosen subset of video tracks
                       on the current timeline AND push per-track
                       transforms (Quadrant, Zoom, Position, Rotation,
                       Anchor, Pitch, Yaw, Flip H/V).

Each backend runs on its own worker thread so a stuck Resolve call
never blocks a Videohub recall (or vice-versa).

THE GRID
--------
Default grid: 4×4 (16 cells). Configurable in Settings up to 40×40.

  • Click a cell                Fire its macro AND mark it selected.
  • Right-click a cell          Open the macro editor.
  • Cmd+Click / Ctrl+Click      Open the macro editor.
  • Press a cell's hotkey       Fire that cell from anywhere in the app.
  • Arrow ↑ / ↓ / ← / →         Move the keyboard selection. Left/right
                                wrap to the opposite column on the row.
                                Up/down clamp at the top/bottom row.
  • Return / Enter              Fire the selected cell.
  • Cmd+E                       Edit the selected cell (Edit > Edit Macro…).
  • Cmd+F                       Toggle native macOS full-screen
                                (restores prior size on toggle out).

The selected cell shows a 1px 60%-white outline + 1px black inset.
Selection persists until another cell is selected.

THE TOP STRIP
-------------
A dark #131313 strip across the top contains:

  • LCD message bar            Last action / hover description / errors.
  • Status indicators          DAVINCI RESOLVE (left) + VIDEOHUB (right).
                               VIDEOHUB hides when disabled in Settings.
  • Preset chooser             Snapshots of (rows, cols, all macros).

PRESETS
-------
A preset is a named snapshot of the entire grid (dimensions + every
macro). Use the popup + Save / Delete buttons in the top strip:

  • Save     Names a snapshot of the current grid. Suggests "Preset 1",
             "Preset 2"...; saving over an existing name overwrites it.
  • Recall   Pick a preset from the popup — applies INSTANTLY.
             Resizes the grid if needed, replaces all macros wholesale,
             and TURNS VIDEOHUB ON OR OFF to match the preset's needs:
                preset uses Videohub  → backend ON
                preset has no Videohub → backend OFF
             "uses Videohub" suffix in the popup tells you which is which.
  • Delete   Removes the selected preset (Cmd+Z restores).

THE MACRO EDITOR
----------------
Open with right-click, Cmd+Click, Ctrl+Click, or Cmd+E (after selecting).

The editor is non-modal and stays open across cells. ◀ / ▶ at the
top right step through every cell, auto-saving the current cell first.

Top section
  Label                Free-text name on the cell.
  Color                sRGB color picker. The cell repaints in real time.
                       A non-default color alone is enough to tint the cell.
  Hotkey: [Mod] + [Key]
    Mod                — / Cmd / Ctrl / Opt / Shift.
    Key                a–z, 0–9, F1–F12.
                       Shown on the cell as ⌘1 / ⌃A / ⌥F2 / ⇧B.
                       Hotkey is suppressed while you're typing in any
                       text field (so "1" goes into Position X, not fire).

Videohub section  (header has a per-macro "Enable" checkbox)
  Enable                NEW MACROS DEFAULT TO OFF. Tick to include.
                        When off, Device + Preset show "Disabled" greyed.
  Device                Picks from Videohub Controller's saved devices.
  Preset                Picks from that device's saved presets.

DaVinci Resolve video tracks
  Track list (left pane, draggable divider) lists tracks descending —
  Vn at the top, V1 at the bottom — to match Resolve's timeline.

  Keyboard while focused on the track list:
    Up / Down            Move the row selection.
    Enter / Return       Toggle the selected track's enabled flag.
    Left / Right         Step the selected track's quadrant Q1→Q2→Q3→Q4.

  Per-track detail panel (right pane):
    Enable track         Mirrors the row's leading "✓ ".
    Quadrant             Q1 / Q2 / Q3 / Q4. Picking a quadrant AUTO-SNAPS
                         Position X/Y to (±tl_w/2, ±tl_h/2) for whatever
                         timeline resolution Resolve reports — for a 4K
                         timeline that's (±1920, ±1080). The quadrant is
                         the single source of truth: picking Q4 ALWAYS
                         lands the clip in bottom-right with the canonical
                         offset, then you can fine-tune Position X/Y.
                         Tilt convention is positive = UP (math style),
                         so top row has positive Tilt, bottom row negative.
    Transform fields     Zoom X/Y, Position X/Y, Rotation, Anchor X/Y,
                         Pitch, Yaw. CLICK + DRAG ON ANY FIELD to scrub:
                            • Drag right increases, drag left decreases.
                            • Plain click without drag → enters edit mode.
                            • Double-click → enters edit mode immediately.
                         Per-field sensitivity:
                            Zoom            0.01 / pixel
                            Position/Anchor 1.0 / pixel
                            Rotation/P/Y    0.5 / pixel
    Flip H / Flip V      Flip the track horizontally / vertically.
    Live update Resolve  Default ON. When unchecked, edits in this editor
                         (transform changes, track-enable toggles, quadrant
                         picks, Reset Selected/All) DO NOT push to Resolve.
                         Use this to build a preset against a running
                         project without disturbing it. Saving still
                         persists the macro to macroflow.json.
    Reset Selected       Restore the selected track to the values
                         captured from Resolve when the editor opened.
                         Track ENABLE flag is preserved.
    Reset All Tracks     Same, applied to every track.
    Quad preview (2×2)   Click any quadrant in the preview to set it.

LIVE PREVIEW IN RESOLVE
-----------------------
While the editor is open AND the "Live update Resolve" checkbox is ON
(default), EVERY change pushes to Resolve so you see it on the timeline
immediately:

  • Pick a quadrant (popup, preview click, or Left/Right arrow on the
    track table) → position auto-snaps to the canonical quadrant offset
    and the full transform pushes.
  • Toggle Flip H / Flip V → push.
  • Toggle "Enable track" (or press Enter on the track table) → push.
  • Edit a transform field then Tab / Enter / focus-out → push.
  • Drag-scrub a transform field → field updates live; pushes on mouse up
    (so the Fusion bridge isn't flooded mid-drag).
  • Reset Selected / Reset All Tracks → push the captured values back.

When the "Live update Resolve" checkbox is OFF, the editor still shows
all the values changing in its own UI — the Resolve clip just doesn't
move until you tick the checkbox back on (or Save and fire the macro).

Edits are persisted to macroflow.json on Save and on cell navigation.

QUIT-RESTORE
------------
On launch, MacroFlow snapshots the Resolve project's track enable flags
and per-track transforms. On Cmd+Q / menu Quit, those values are pushed
back, undoing any changes MacroFlow made during the session.

  Caveat: force-quit (kill -9, crash) bypasses the restore hook.
  Caveat: switching Resolve projects mid-session would push the snapshot
          back into the wrong project.

FIRING SEMANTICS
----------------
A macro fires its non-empty actions in parallel:
  • Videohub action skipped if the per-macro Enable is OFF.
  • Videohub action skipped if Settings → Enable Videohub backend is OFF.
  • Resolve action skipped if no tracks are toggled in the editor.
Each backend is bounded by a 5-second timeout. Failures don't block
other backends. The LCD echoes a per-backend success/failure summary.

HOTKEYS
-------
Local NSEvent monitor — only fires while MacroFlow is the focused app
AND no text field has focus. Each macro can require an exact modifier:

  Macro requires Cmd        → only Cmd+key fires it (Ctrl+key won't).
  Macro requires no modifier→ only bare key fires it (Cmd+key won't).

Function keys F1–F12 are matched by keycode (no charactersIgnoring).

SETTINGS  (Cmd+,)
-----------------
  Display / Title / Hotkey font size sliders.
  Reset to Defaults                  Restores 12 / 13 / 26 pt (undoable).
  Grid size                          4×4, 6×6, 8×8, 10×10, 12×12, 20×20,
                                     40×40. Live-resize without restart.
                                     Macros at out-of-bounds coordinates
                                     are kept in storage and reappear if
                                     you grow the grid back. Their hot-
                                     keys won't fire while out of bounds.
  Enable Videohub backend            Master switch. When off:
                                       • VIDEOHUB status indicator hides.
                                       • Status probe is skipped.
                                       • Recall short-circuits to no-op.
                                       • Editor's Videohub fields can
                                         still be configured for "later".
                                     Per-macro Enable checkbox overrides
                                     visibility of the editor's Device /
                                     Preset fields.

  Window & Hotkey Behavior  (ported from Videohub Controller)
  Keep on Top                        Floats the MacroFlow window above
                                     other apps (DaVinci Resolve, etc.).
                                     Uses NSFloatingWindowLevel.
  Global Hotkeys                     Macro hotkeys fire even when MacroFlow
                                     is not the focused app. Requires
                                     Accessibility permission (System
                                     Settings → Privacy & Security →
                                     Accessibility). MacroFlow prompts
                                     the first time you tick it.
                                     macOS routes events to LOCAL or
                                     GLOBAL monitor based on focus, never
                                     both — no double-fire.

STATUS INDICATORS
-----------------
  DAVINCI RESOLVE                    Green when the Resolve scripting
                                     bridge round-trips a GetProjectManager()
                                     call (catches stale handles after
                                     Resolve quits mid-session). Red when
                                     Resolve isn't running or the bridge
                                     can't reach it.
  VIDEOHUB                           Green when Videohub Controller is
                                     in the running-applications list
                                     (NSWorkspace bundle-id check). Doesn't
                                     depend on a router being on the LAN —
                                     mirrors VHC's app-running state.
                                     Hidden when Settings → Enable Videohub
                                     backend is off.
  Both probes re-run every 5 seconds, in worker threads so a slow probe
  can't freeze the main UI.

UNDO / REDO  (Cmd+Z / Cmd+Shift+Z)
-----------------------------------
App-level undo stack (max 50 entries) covers:
  • Settings → Grid size change
  • Settings → Enable Videohub backend toggle
  • Settings → Reset to Defaults (font sizes)
  • Top-bar → Recall preset (full prior grid state)
  • Top-bar → Delete preset (preset is restored)
  • Editor → Clear cell (cleared macro is re-saved)

NSText fields handle their own edit-undo before our Cmd+Z. Within-editor
transient edits (Reset Selected / Reset All Tracks, drag-scrub) are not
undoable — closing the editor without Save naturally undoes them.

CONSOLE  (Help → Show Console, Cmd+Shift+C)
-------------------------------------------
Live tail of every print / traceback the app emits. Use Export… to
write the buffer to a timestamped .txt — attach that file when reporting
a bug. Buffer holds the most recent 10,000 lines.

VIDEOHUB BACKEND
----------------
Reads device + preset list from Videohub Controller's shared config:

   /Users/Shared/Videohub Controller/videohub_controller.json

Recall path: direct TCP/9990 to the router. Falls back to handing the
recall to a running VHC via NSDistributedNotification.

Mock mode (`mock_videohub: true` in macroflow.json) bypasses the network
entirely — useful for editor smoke tests without a router on the LAN.

DAVINCI RESOLVE BACKEND
-----------------------
DaVinciResolveScript.py is loaded from the first match of:

   $RESOLVE_SCRIPT_API/Modules/DaVinciResolveScript.py
   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/
       Scripting/Modules/DaVinciResolveScript.py
   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/
       Modules/Lua/DaVinciResolveScript.py
   ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/
       Modules/Lua/DaVinciResolveScript.py

All Resolve calls go through a worker-thread wrapper (`_run_off_main`)
so a stuck Fusion bridge can't corrupt the Cocoa autorelease pool on
the main thread. 5-second timeout per call.

Per-track transforms are written via `clip.SetProperty()` on the FIRST
clip in each track. Property name mapping:

   zoom_x         → ZoomX
   zoom_y         → ZoomY
   position_x     → Pan
   position_y     → Tilt
   rotation_angle → RotationAngle
   anchor_point_x → AnchorPointX
   anchor_point_y → AnchorPointY
   pitch          → Pitch
   yaw            → Yaw
   flip_h         → FlipX (0/1)
   flip_v         → FlipY (0/1)

STORAGE & PERMISSIONS
---------------------
All persistent state lives in:

   /Users/Shared/MacroFlow/macroflow.json

Multi-user safe: parent dir is forced 0o777, file is forced 0o666.
Atomic writes: each save goes to a temp file in the same directory,
fsync'd, chmod'd, then `os.replace()`'d into place. Concurrent saves
from different users won't corrupt the file. Symlinks at the target
are NOT followed — `os.replace` swaps the path itself.

Load-side symlink protection: MacroFlow refuses to read the config if
the path is a symlink (refuses-to-follow), so a planted symlink can't
trick the running user into reading another file under their privileges.

JSON shape (top-level + per-macro keys, all current):
   {{
     "rows": 4, "cols": 4,
     "videohub_enabled": true,
     "keep_on_top": false,
     "global_hotkeys": false,
     "font_sizes": {{"display": 12, "title": 13, "hotkey": 26}},
     "presets": {{ "Preset 1": {{"rows":4, "cols":4, "macros":{{...}}}} }},
     "macros": {{
       "0,0": {{
         "id": "0,0", "label": "TEST 1", "color": "#a4a833",
         "hotkey": "1", "hotkey_modifier": "Cmd",
         "videohub_enabled": false,
         "videohub": {{"device_id": "...", "preset_name": "1 Preset"}},
         "resolve":  {{
            "tracks": {{"1": true, "3": false}},
            "track_transforms": {{"1": {{"quadrant":"Q2", "zoom_x":1.0, ...}}}},
            "track_names":      {{"1": "Hero Cam"}}
         }}
       }}
     }}
   }}

Track binding: `track_names` records the Resolve track name at SAVE
time. On load, MacroFlow prefers a name match over an idx match — so
inserting / deleting Resolve tracks (which shifts indices) leaves your
saved transforms attached to the correct physical track.

UI COLOR PALETTE  (matches Videohub Controller)
-----------------------------------------------
  Window bg            calibrated (0.17, 0.17, 0.17) → renders ~#4a in
                       DarkAqua. DarkAqua is forced on every NSWindow.
  Top header strip     sRGB #131313.
  Macro cells          sRGB #494949.
  LCD strip            sRGB #1a2117 with warm-yellow text.
  Selection outline    1px 60%-white outer + 1px black inset 1px.

QUADRANT MATH
-------------
The 4-up quadrant offsets in MacroFlow's editor track Resolve's Pan / Tilt
convention:

   Pan:  positive = right, negative = left
   Tilt: positive = up,    negative = down  (math style)

Computed offsets per quadrant (timeline-derived):

   Q1 top-left      = (-tl_w/2, +tl_h/2)
   Q2 top-right     = (+tl_w/2, +tl_h/2)
   Q3 bottom-left   = (-tl_w/2, -tl_h/2)
   Q4 bottom-right  = (+tl_w/2, -tl_h/2)

For a 4K timeline (3840 × 2160) those become (±1920, ±1080). The editor
reads timeline_w / timeline_h via `tl.GetSetting()` at editor-open time
and on cell navigation, so changing project resolution mid-session is
picked up automatically.

KEYBOARD QUICK REFERENCE
------------------------
Main grid window
  Arrow keys           Move selection. Left/right wrap.
  Return / Enter       Fire the selected cell.
  Cmd+E                Edit Macro for the selected cell.
  Cmd+F                Toggle full-screen.
  Cmd+,                Settings.
  Cmd+H                Hide MacroFlow.
  Cmd+Q                Quit (and restore Resolve to launch state).
  Cmd+Z / Cmd+Shift+Z  Undo / Redo.
  Cmd+? (Cmd+Shift+/)  Open this manual.
  Cmd+Shift+C          Show Console.
  Cmd+Shift+E          Export Settings…
  Cmd+Shift+I          Import Settings…
  Letter / digit / Fn  Fire the matching macro hotkey.

Macro editor
  ◀ / ▶                Prev / Next cell (auto-saves first).
  Esc                  Close editor.
  Return               Save the current cell.
  In the track list:
    Up / Down          Walk rows.
    Enter              Toggle the selected track's enabled flag.
    Left / Right       Step the selected track's quadrant.

REPORTING BUGS
--------------
  1. Open Help → Show Console.
  2. Reproduce the issue.
  3. Click Export… and save the log.
  4. Email it to chad.littlepage@gmail.com with a one-line description
     of what you did and what you expected.

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
