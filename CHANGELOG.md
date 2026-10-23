# Changelog

## 2.0.5 — SteamCMD Workshop integrity checking

- Added a Check File Integrity action for the selected installed Workshop item or all installed content folders.
- Runs SteamCMD's native Workshop `validate` operation in a background worker with bounded single-attempt checks and supports cancellation.
- Writes timestamped JSON reports with per-item status, logs, content paths, and a summary; no partial-cache cleanup is performed.
- Added tests for integrity target selection, report writing, and validation-mode execution.


## 2.0.4 — visible update and queue recovery dashboard

- Moved the update action into the application header so it cannot be pushed off-screen by queue controls.
- Redesigned the main dashboard with live queue counts, clearer sections, status colors, and a responsive two-row controls layout.
- Added Add + Start, Resume Selected, Resume Old Jobs, and Recover Partial Files actions; Enter adds and starts, double-click resumes a selected row.
- Added a safe partial-cache recovery scan that restores numeric Workshop IDs from SteamCMD's existing cache without deleting or overwriting those files.
- Improved empty-queue and missing-SteamCMD messages, and added regression tests for queue summaries, history recovery, and partial-cache discovery.


## 2.0.3 — version-safe self-updater

- Added a dedicated Check / Update button that queries stable GitHub Releases without blocking the UI.
- Compares strict numeric MAJOR.MINOR.PATCH versions and never downgrades a newer installed version.
- Downloads only official release assets and verifies the executable SHA-256 against the release manifest before staging.
- Uses a hidden Windows helper to wait for shutdown, back up the old executable, replace it, and restart; records helper failures in the app-data update log.
- Added a stable-release workflow that enforces exact tag/package version matching and publishes the EXE plus checksum manifest.


## 2.0.2 — silent install and live progress

- Suppressed the separate SteamCMD console window while keeping stdout/stderr captured for logs.
- Added a visible in-app progress bar with transferred data, total size/percentage when known, transfer rate, and ETA.
- Made progress explicitly show transferred bytes when Steam has not reported the item's total size.
- Added regression checks for hidden Windows console flags and live progress formatting.


## 2.0.1 — GUI startup fix

- Fixed label styling so per-label color overrides do not pass duplicate Tkinter options.
- Added a regression test for custom label colors.
- Re-ran compile checks and the full test suite: 60 tests passed.


## 2.0.0 — standalone rewrite

- Replaced monolithic downloader flow with testable SteamCMD, metadata, process, persistence, engine, CLI and GUI modules.
- Added per-attempt watchdogs, cancellation, streamed durable logs, retry/backoff policy and a no-progress guard.
- Made download completion depend on an item-specific SteamCMD success marker, a zero exit code and nonempty output content.
- Preserved SteamCMD partial/content data by default; no cache cleanup is coupled to retry/failure paths.
- Added persistent queue/history, settings validation, URL/ID batch parsing, progress telemetry and Windows packaging CI.
- Added offline tests for failure modes and data-preservation invariants.
