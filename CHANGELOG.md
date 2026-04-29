# Changelog

All notable changes to MacroFlow are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `SECURITY.md` disclosure policy.
- `CONTRIBUTING.md`, issue templates, and a pull-request template.
- `CHANGELOG.md` (this file).
- Coverage reporting via `pytest-cov` + Codecov integration in CI.
- SHA-256 checksum file alongside the DMG on each tagged release.

## [0.1.1] — 2026-04-28

### Added
- Cmd+F (View → Toggle Full Screen) actually fires now — added
  `NSWindowCollectionBehaviorFullScreenPrimary` to the main window.

### Changed
- LCD strip height now scales with the Display font size, keeping
  the text vertically centered. The status row, preset row, and grid
  push down rather than overlap.
- Display + Title font sliders extended to 40 pt (were 22 / 24).
- DMG installer uses the shared Videohub Controller background asset.
- About window honors the Videohub Controller about-background.
- README adds CI / CodeQL / release badges and embeds three screenshots.

### Fixed
- Mypy type error on `_selected_fire_key` (annotated as
  `tuple[int, int] | None`) that was failing CI.

## [0.1.0] — 2026-04-28

### Added
- Initial public source-available release.
- Clickable macro grid (4×4 through 40×40, live-resizable).
- Per-macro DaVinci Resolve quadrant selection with full per-track
  transforms (Pan, Tilt, Zoom, Pitch, Yaw, RotationAngle, FlipX/Y).
- 2×2 quad preview with click-and-drag scrubbing.
- Per-macro Videohub preset recall via Videohub Controller bridge or
  direct TCP/9990 protocol.
- Hotkey support with modifier dropdown (— / Cmd / Ctrl / Opt / Shift)
  and global hotkey mode (requires Accessibility permission).
- Preset row (Save / Recall / Delete) for snapshotting the entire grid.
- Settings window: font sliders, grid size, Videohub master switch,
  Keep on Top, Global Hotkeys.
- Help → Manual + Console with stdout/stderr capture.
- Multi-user-safe atomic writes with symlink protection.
- Truthful status indicators (Resolve via `GetProjectManager()`
  round-trip; Videohub via NSWorkspace bundle-id probe).
- App-level Cmd+Z / Cmd+Shift+Z undo/redo.
- Quit-restore for Resolve project state.
- Signed + notarized DMG distribution.
- CI on macOS-14 (ruff, mypy, import smoke, pytest, py2app dry build).
- CodeQL, Dependabot, tag-driven release workflow.

[Unreleased]: https://github.com/chadlittlepage/MacroFlow/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/chadlittlepage/MacroFlow/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/chadlittlepage/MacroFlow/releases/tag/v0.1.0
