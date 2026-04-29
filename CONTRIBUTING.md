# Contributing to MacroFlow

Thanks for your interest. MacroFlow is **source-available**, not open
source — see [LICENSE](LICENSE) for the exact terms. Pull requests are
welcome but will be merged at the maintainer's discretion, and the
project remains under sole copyright.

## Bug reports

File a [bug report](https://github.com/chadlittlepage/MacroFlow/issues/new?template=bug_report.md)
with:

- macOS version + Apple silicon vs. Intel.
- DaVinci Resolve version (if relevant).
- Videohub model (if relevant).
- Steps to reproduce.
- Console output. **Help → Console → Export…** writes a `.txt` file
  with stdout/stderr captured since launch — please attach it.

## Development setup

```bash
git clone https://github.com/chadlittlepage/MacroFlow.git
cd MacroFlow
python3 -m pip install --upgrade pip
pip3 install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz
pip3 install ruff mypy pytest pytest-cov
PYTHONPATH=src python3 -m macroflow
```

## Pull-request checklist

Before opening a PR:

- [ ] `ruff check src/` passes.
- [ ] `mypy --ignore-missing-imports --no-strict-optional src/macroflow`
      passes.
- [ ] `PYTHONPATH=src pytest tests/ -q` passes.
- [ ] You've manually exercised the feature in the running app — type
      checking and tests don't verify Cocoa UI behavior.

## Style

- Default to **no comments**. Add one only when the *why* is non-obvious.
- Keep PRs focused. One bug fix or one feature per PR.
- Match existing patterns — this codebase mirrors Videohub Controller's
  shape, and consistency between the two is intentional.

## Commit messages

One-line summary in the imperative mood, followed by a blank line and
a body if needed. Reference issues with `#123` where applicable.

## Builds

Local signed + notarized DMG:

```bash
./build_and_sign.sh
```

CI builds an unsigned `.app` on every push to verify py2app still
works; tagged pushes (`v*.*.*`) trigger a signed + notarized release
build via `.github/workflows/release.yml`.
