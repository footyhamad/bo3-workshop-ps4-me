# Changelog

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
