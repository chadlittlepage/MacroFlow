# MacroFlow

[![CI](https://github.com/chadlittlepage/MacroFlow/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/chadlittlepage/MacroFlow/actions/workflows/ci.yml)
[![CodeQL](https://github.com/chadlittlepage/MacroFlow/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/chadlittlepage/MacroFlow/actions/workflows/codeql.yml)
[![codecov](https://codecov.io/gh/chadlittlepage/MacroFlow/branch/main/graph/badge.svg)](https://codecov.io/gh/chadlittlepage/MacroFlow)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230?logo=ruff)](https://github.com/astral-sh/ruff)
[![Latest release](https://img.shields.io/github/v/release/chadlittlepage/MacroFlow?label=release)](https://github.com/chadlittlepage/MacroFlow/releases/latest)
[![License: Source-Available](https://img.shields.io/badge/license-source--available-orange)](LICENSE)
[![macOS](https://img.shields.io/badge/macOS-14%2B-000000?logo=apple&logoColor=white)](https://www.apple.com/macos/)

**A clickable grid of macro buttons that automate DaVinci Resolve and
Blackmagic Videohub from one place.** Pick a Resolve quadrant for any
video track, recall a Videohub preset, fire from a hotkey or a click —
backends run in parallel, with live feedback on the timeline as you edit.

![MacroFlow main window](docs/screenshots/main-window.png)

> **License notice.** MacroFlow is **source-available**, not open source.
> The code is published for transparency, portfolio review, and bug
> reporting. You may read, clone, and run it locally for personal
> evaluation. Redistribution, modification, derivative works, and
> commercial use require prior written permission. See [LICENSE](LICENSE)
> for the exact terms. **Support is best-effort.**

---

## Download

The latest signed + notarized `.dmg` is on the
[Releases page](https://github.com/chadlittlepage/MacroFlow/releases/latest).
Drag `MacroFlow.app` to `/Applications` (or `~/Applications`) and launch
— Gatekeeper accepts it directly.

| Requirement | Minimum |
|---|---|
| macOS | 14.0 (Sonoma) or later — tested on 14 + 15 (Sequoia) |
| DaVinci Resolve | Studio or free, running on the same Mac |
| Videohub Controller | Optional, used to author presets MacroFlow recalls |
| Python | Only for development; the signed `.app` ships its own |

---

## Overview

MacroFlow is a clickable grid of macro cells. Each cell fires one or
more backend actions in parallel from a single click, hotkey, or
keyboard nav.

- **Videohub** — recall a saved preset on a Blackmagic Videohub router.
- **DaVinci Resolve** — enable / disable a chosen subset of video tracks
  on the current timeline AND push per-track transforms (Quadrant, Zoom,
  Position, Rotation, Anchor, Pitch, Yaw, Flip H/V).

Each backend runs on its own worker thread, so a stuck Resolve call
never blocks a Videohub recall (or vice-versa).

## The Grid

Default grid: **4×4 (16 cells)**. Configurable in Settings up to
**40×40 (1600 cells)**.

| Action | Effect |
|---|---|
| **Click a cell** | Fire its macro AND mark it selected |
| **Right-click a cell** | Open the macro editor |
| **Cmd+Click / Ctrl+Click** | Open the macro editor |
| **Press a cell's hotkey** | Fire that cell from anywhere in the app |
| **Arrow ↑ / ↓ / ← / →** | Move the keyboard selection (left/right wrap) |
| **Return / Enter** | Fire the selected cell |
| **Cmd+E** | Edit the selected cell (Edit → Edit Macro…) |
| **Cmd+F** | Toggle native macOS full-screen |

The selected cell shows a **1px 60%-white outline + 1px black inset**.
Selection persists until another cell is selected.

### The top strip

A dark `#131313` strip across the top contains:

- **LCD message bar** — last action / hover description / errors.
- **Status indicators** — DAVINCI RESOLVE (left) + VIDEOHUB (right).
  VIDEOHUB hides when disabled in Settings.
- **Preset chooser** — snapshots of (rows, cols, all macros).

### Presets

A preset is a named snapshot of the entire grid (dimensions + every
macro). Use the popup + Save / Delete buttons in the top strip:

- **Save** — names a snapshot of the current grid. Suggests `Preset 1`,
  `Preset 2`…; saving over an existing name overwrites it.
- **Recall** — pick a preset from the popup → applies **instantly**.
  Resizes the grid if needed, replaces all macros wholesale, and turns
  Videohub ON or OFF to match the preset's needs (the popup labels presets
  that contain Videohub actions with `• uses Videohub`).
- **Delete** — removes the selected preset (Cmd+Z restores).

---

## The Macro Editor

Open with right-click, Cmd+Click, Ctrl+Click, or Cmd+E (after selecting).
The editor is non-modal and stays open across cells. ◀ / ▶ at the top
right step through every cell, auto-saving the current cell first.

![MacroFlow Edit Macro window](docs/screenshots/edit-macro.png)

### Top section

| Field | What it does |
|---|---|
| **Label** | Free-text name shown on the cell |
| **Color** | sRGB color picker. The cell repaints in real time. A non-default color alone is enough to tint the cell. |
| **Hotkey** | Modifier dropdown (— / Cmd / Ctrl / Opt / Shift) + key dropdown (a–z, 0–9, F1–F12). Shown on the cell as ⌘1, ⌃A, ⌥F2, ⇧B. Hotkeys are suppressed while you're typing in any text field. |

### Videohub section

The header has a per-macro **Enable** checkbox. New macros default to OFF.
When unchecked, Device + Preset show "Disabled" and grey out. Tick it to
include Videohub in this macro:

- **Device** — picks from Videohub Controller's saved devices.
- **Preset** — picks from that device's saved presets.

### DaVinci Resolve video tracks

Track list (left pane, draggable divider) lists tracks descending —
`Vn` at the top, `V1` at the bottom — to match Resolve's timeline.

**Keyboard while focused on the track list:**

| Key | Effect |
|---|---|
| ↑ / ↓ | Move the row selection |
| Enter / Return | Toggle the selected track's enabled flag |
| ← / → | Step the selected track's quadrant Q1 → Q2 → Q3 → Q4 |

**Per-track detail panel (right pane):**

| Control | Effect |
|---|---|
| **Enable track** | Mirrors the row's leading "✓ " |
| **Quadrant** | Q1 / Q2 / Q3 / Q4. Picking a quadrant auto-snaps Position X/Y to (±tl_w/2, ±tl_h/2) for the active timeline — for a 4K timeline that's `(±1920, ±1080)`. The quadrant is the single source of truth. |
| **Transform fields** | Zoom X/Y, Position X/Y, Rotation, Anchor X/Y, Pitch, Yaw. **Click + drag to scrub** any field — drag right increases, drag left decreases. Plain click without drag → enters edit mode. Double-click → enters edit mode immediately. |
| **Flip H / Flip V** | Flip the track horizontally / vertically |
| **Live update Resolve** | Default ON. When unchecked, edits in the editor stay in the editor and don't push to Resolve. Useful for building a preset against a running project without disturbing it. |
| **Reset Selected** | Restore the selected track to the values captured from Resolve when the editor opened |
| **Reset All Tracks** | Same, applied to every track |
| **Quad preview (2×2)** | Click any quadrant in the preview to set it |

**Drag-scrub sensitivity:**

| Field | Per pixel |
|---|---|
| Zoom X/Y | 0.01 |
| Position X/Y, Anchor X/Y | 1.0 |
| Rotation, Pitch, Yaw | 0.5 |

### Live preview in Resolve

While the editor is open AND **Live update Resolve** is on (default),
EVERY change pushes to Resolve so you see it on the timeline immediately:

- Pick a quadrant (popup, preview click, or ←/→ on the track table) →
  position auto-snaps and the full transform pushes
- Toggle Flip H / Flip V → push
- Toggle Enable track (or Enter on the track table) → push
- Edit a transform field then Tab / Enter / focus-out → push
- Drag-scrub a transform field → field updates live; pushes on mouse-up
  (so the Fusion bridge isn't flooded mid-drag)
- Reset Selected / Reset All Tracks → pushes the captured values back

Edits are persisted to `macroflow.json` on Save and on cell navigation.

### Quit-restore

On launch, MacroFlow snapshots the Resolve project's track enable flags
and per-track transforms. On Cmd+Q / menu Quit, those values are pushed
back, undoing any changes MacroFlow made during the session. Force-quit
(`kill -9`, crash) bypasses the restore hook.

---

## Settings

![MacroFlow Settings window](docs/screenshots/settings.png)

`Cmd+,` opens Settings:

- **Display / Title / Hotkey** font size sliders. Display and Title go
  up to 40 pt; Hotkey goes up to 60 pt. Drag to live-adjust the grid;
  saves are debounced 250 ms after the last drag tick. The LCD strip
  auto-grows so the display font stays vertically centered, and the
  status row + grid push down rather than overlap.
- **Reset to Defaults** — restores 12 / 13 / 26 pt (undoable).
- **Grid size** — 4×4, 6×6, 8×8, 10×10, 12×12, 20×20, 40×40. Live-resize
  without restart. Macros at out-of-bounds coordinates are kept in
  storage and reappear if you grow the grid back.
- **Enable Videohub backend** — master switch. When off: VIDEOHUB status
  indicator hides, the status probe is skipped, and recall short-circuits
  to no-op. The editor's Videohub fields can still be configured for
  later use.
- **Window & Hotkey Behavior** (ported from Videohub Controller):
  - **Keep on Top** — floats the MacroFlow window above other apps
    (DaVinci Resolve, etc.) using `NSFloatingWindowLevel`.
  - **Global Hotkeys** — macro hotkeys fire even when MacroFlow is not
    the focused app. Requires Accessibility permission (System Settings →
    Privacy & Security → Accessibility). MacroFlow prompts the first
    time you tick it.

---

## Status indicators

| Indicator | What "green" actually means |
|---|---|
| **DAVINCI RESOLVE** | The Resolve scripting bridge round-trips a `GetProjectManager()` call (catches stale handles after Resolve quits mid-session). |
| **VIDEOHUB** | Videohub Controller is in the running-applications list (NSWorkspace bundle-id check). Doesn't depend on a router being on the LAN. |

Both probes re-run every 5 seconds, in worker threads so a slow probe
can't freeze the main UI.

## Hotkeys

Local NSEvent monitor — only fires while MacroFlow is the focused app
AND no text field has focus. Each macro can require an exact modifier:

- Macro requires **Cmd** → only `Cmd+key` fires it (`Ctrl+key` won't).
- Macro requires **no modifier** → only bare key fires it (`Cmd+key` won't).

Function keys F1–F12 are matched by keycode (no character resolution).

When **Global Hotkeys** is on, the same matching applies app-wide.
macOS routes events to LOCAL or GLOBAL monitor based on focus, never
both — no double-fire.

## Undo / Redo (Cmd+Z / Cmd+Shift+Z)

App-level undo stack (max 50 entries) covers:

- Settings → Grid size change
- Settings → Enable Videohub backend toggle
- Settings → Reset to Defaults (font sizes)
- Top-bar → Recall preset (full prior grid state)
- Top-bar → Delete preset
- Editor → Clear cell

NSText fields handle their own edit-undo before our Cmd+Z. Within-editor
transient edits (Reset Selected/All, drag-scrub) are not undoable —
closing the editor without Save naturally undoes them.

## Console (Help → Show Console, Cmd+Shift+C)

Live tail of every print / traceback the app emits. Use **Export…** to
write the buffer to a timestamped `.txt` — attach that file when
reporting a bug. Buffer holds the most recent 10,000 lines.

The full manual is also available in-app at **Help → MacroFlow Manual
(Cmd+?)**.

---

## File locations

| Path | Contents |
|---|---|
| `/Users/Shared/MacroFlow/macroflow.json` | Macro grid config (multi-user shared) |
| `/Users/Shared/Videohub Controller/videohub_controller.json` | Videohub presets (read-only from MacroFlow) |
| `/Library/Application Support/Blackmagic Design/.../DaVinciResolveScript.py` | Resolve scripting bridge (system-installed) |

The macro file is multi-user safe: parent dir is forced 0o777, file is
forced 0o666, writes are atomic via temp-file + `os.replace()`. Symlinks
at the target are NOT followed.

## JSON shape (`macroflow.json`)

```json
{
  "rows": 4, "cols": 4,
  "videohub_enabled": true,
  "keep_on_top": false,
  "global_hotkeys": false,
  "font_sizes": {"display": 12, "title": 13, "hotkey": 26},
  "presets": { "Preset 1": {"rows": 4, "cols": 4, "macros": {…}} },
  "macros": {
    "0,0": {
      "id": "0,0", "label": "TEST 1", "color": "#a4a833",
      "hotkey": "1", "hotkey_modifier": "Cmd",
      "videohub_enabled": false,
      "videohub": {"device_id": "…", "preset_name": "1 Preset"},
      "resolve": {
        "tracks": {"1": true, "3": false},
        "track_transforms": {"1": {"quadrant": "Q2", "zoom_x": 1.0, …}},
        "track_names":      {"1": "Hero Cam"}
      }
    }
  }
}
```

`track_names` records the Resolve track name at save time. On load, the
editor prefers a name match over an idx match — so inserting / deleting
Resolve tracks (which shifts indices) leaves your saved transforms
attached to the correct physical track.

---

## Development

```bash
git clone https://github.com/chadlittlepage/MacroFlow.git
cd MacroFlow
pip3 install -e .
PYTHONPATH=src python3 -m macroflow
```

## Build (signed + notarized)

```bash
./build_and_sign.sh
```

Output: `dist/MacroFlow.dmg` (signed, notarized, stapled).

The script auto-detects your Developer ID, builds via py2app, signs
with hardened runtime + entitlements, notarizes via the
`chads-davinci-notary` keychain profile (configurable via
`NOTARY_PROFILE` env var), staples, and packages a DMG.

## Project structure

```
MacroFlow/
├── src/macroflow/
│   ├── app.py                Main Cocoa grid window + AppController
│   ├── macro.py              Macro / MacroGrid / MacroStore data model
│   ├── macro_editor.py       Per-cell editor (track list, transforms, quad preview)
│   ├── settings_window.py    Settings dialog (sliders + grid size + behavior toggles)
│   ├── about_window.py       About panel
│   ├── help_window.py        In-app manual
│   ├── console_window.py     Live stdout/stderr capture + export
│   ├── log_capture.py        Stdout/stderr tee → ring buffer + observers
│   ├── quad_preview.py       2×2 quad-view monitor (clickable)
│   └── backends/
│       ├── videohub.py       Reads VHC config, drives TCP recall
│       ├── resolve.py        DaVinciResolveScript wrapper
│       └── local_dimming.py  LocalDimmingSim Fusion-macro toggle
├── tests/                    pytest suite
├── docs/screenshots/         README assets
├── assets/AppIcon.iconset/   App icon source PNGs
├── assets/AppIcon.icns       Compiled app icon
├── app_entry.py              py2app entry point
├── setup.py                  py2app config
├── pyproject.toml            project metadata + lint config
├── entitlements.plist        Hardened-runtime entitlements
├── build_and_sign.sh         Build + sign + notarize + DMG
└── dmg_settings.py           dmgbuild config
```

---

## Reporting bugs

1. Open Help → Show Console.
2. Reproduce the issue.
3. Click Export… and save the log.
4. Open an issue on GitHub and attach the log, with a one-line
   description of what you did and what you expected.

## Author

**Chad Littlepage**
[chad.littlepage@gmail.com](mailto:chad.littlepage@gmail.com) · 323.974.0444

## License

Source-Available, all rights reserved. Read [LICENSE](LICENSE) before
forking, redistributing, or using in any commercial product.
