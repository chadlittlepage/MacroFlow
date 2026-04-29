# Security Policy

## Supported Versions

The latest tagged release is the only version that receives security
updates. MacroFlow is a small single-developer project, so older
releases are unsupported.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :x:                |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security problems.

Email: **chad.littlepage@gmail.com**

Include:

- A description of the issue and the impact you observed.
- Steps to reproduce, or a minimal proof-of-concept.
- The affected MacroFlow version and macOS version.
- Whether the issue requires Accessibility permission, network access,
  Apple events to DaVinci Resolve, or any other special context.

I'll acknowledge receipt within **3 business days** and aim to ship a
fix or mitigation in the next tagged release. If the issue is severe
and exploitable, I'll cut a patch release out-of-band.

## Scope

In scope:

- The MacroFlow `.app` bundle published on the Releases page.
- Source code in this repository.
- Build, sign, and notarize tooling (`build_and_sign.sh`, `setup.py`,
  GitHub Actions workflows).

Out of scope:

- DaVinci Resolve, Blackmagic Videohub, or any third-party software
  MacroFlow integrates with — please report those upstream.
- Issues that require physical access to an unlocked machine.
- Vulnerabilities only reproducible on unsupported macOS versions
  (older than the `LSMinimumSystemVersion` declared in `setup.py`).
