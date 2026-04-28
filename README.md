# MacroFlow

A grid of macro buttons that automate other apps. Each cell fires a macro
that can simultaneously:

- Recall a Videohub preset (via Videohub Controller's saved configs)
- Enable/disable specific DaVinci Resolve video tracks
- (Round 2) Trigger automatically when Resolve hits a programmed timecode

> **License notice.** MacroFlow is **source-available**, not open source.
> The code is published for transparency and bug reporting. You may read,
> clone, and run it locally for personal evaluation. Redistribution,
> modification, derivative works, or commercial use require prior written
> permission. See [LICENSE](LICENSE) for the exact terms.

## Status

**Round 1 (manual macro grid) — in progress.**
Round 2 (timeline-synced cue engine) — planned.

## How it works

1. **Author Videohub presets** in [Videohub Controller](https://github.com/chadlittlepage/VideohubController).
   MacroFlow reads them straight from the shared config at
   `/Users/Shared/Videohub Controller/videohub_controller.json`.
2. **Open MacroFlow.** A 4x4 grid of empty cells appears.
3. **Right-click (or double-click) a cell** to open its editor.
   - Pick a Videohub device + preset.
   - Toggle which Resolve video tracks should be ON, OFF, or untouched.
   - Give the cell a label.
4. **Click a cell** to fire its macro. Both backends run in parallel.

## Requirements

| Requirement | Minimum |
|---|---|
| macOS | 14.0 (Sonoma) or later |
| Python | 3.12+ (development only; not needed for the signed .app) |
| DaVinci Resolve | Studio or free, running on the same Mac |
| Videohub Controller | Optional, used to author presets MacroFlow consumes |

## Development

```bash
cd MacroFlow
pip3 install -e .
macroflow
```

## Build

```bash
./build_and_sign.sh
```

Output: `dist/MacroFlow.dmg` (signed, notarized, stapled).

## File locations

| Path | Contents |
|---|---|
| `/Users/Shared/MacroFlow/macroflow.json` | Macro grid config |
| `/Users/Shared/Videohub Controller/videohub_controller.json` | Videohub presets (read-only from MacroFlow) |

## Project structure

```
MacroFlow/
  src/macroflow/
    __init__.py
    app.py                  Main Cocoa grid window + AppController
    macro.py                Macro / MacroGrid / MacroStore data model
    macro_editor.py         Per-cell editor window
    backends/
      videohub.py           Reads Videohub Controller config, drives TCP recall
      resolve.py            DaVinciResolveScript wrapper (track enable, TC)
  app_entry.py              py2app entry point
  setup.py                  py2app config
  pyproject.toml
  entitlements.plist
  build_and_sign.sh         Sign + notarize + DMG
  dmg_settings.py
```

## Author

**Chad Littlepage**
chad.littlepage@gmail.com
323.974.0444

## License

Source-Available, all rights reserved. Read [LICENSE](LICENSE) before
forking, redistributing, or using in any commercial product.
