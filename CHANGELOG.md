# Changelog

## 2.3.0 — import existing Steam Client Workshop files

- Added an Import Steam Client Copy action for a selected Workshop ID.
- Discovers Steam libraries from Windows registry/default paths and each library's libraryfolders.vdf; supports a manual library-folder scan when automatic discovery misses a custom installation.
- Copies only files already present in the Steam Client's local Workshop content folder; it does not invoke downloads or bypass Steam access/removal restrictions.
- Preserves existing files: identical files are retained, differing collisions are skipped and listed, and new files are copied atomically with live byte progress.
- Adds tests for VDF path discovery, missing items, safe import, duplicate/conflicting files, and destination symlink refusal.

## 2.2.0 — explicit SteamCMD failure detection

- Detects `ERROR! Download item <id> failed (<reason>)` even when SteamCMD exits with code 0.
- Stops retrying non-transient item-level failures, preserves all partial files, and surfaces an actionable explanation in the UI.
- Keeps explicit network/connection failures on the retry path.
- Adds a regression test proving a generic `Failure` response does not spin through dozens of retries.
## 2.1.0 — live byte progress, network meter, and partial cleanup

- Optimized filesystem byte counting with `os.scandir` and increased sampling frequency so whole-KB progress updates more often.
- Added a separate RX/TX meter using system network-interface counters; rates reflect overall machine traffic, not SteamCMD alone.
- Added a preview-and-confirm cleanup for numeric SteamCMD partial folders with no file activity for at least seven days.
- Protects queued/active IDs, rechecks age before removal, and restricts cleanup to the Workshop downloads cache rather than installed content.
- Shows a no-new-disk-bytes timer during downloads to make stalled transfers easier to identify.
- Adds regression tests for network-rate sampling and partial-cache safety.
## 2.0.9 — immediate downloads and prompted updates

- Removed GUI-side Steam/API/Workshop size lookups from the queue-add path; new jobs are queued and started without waiting for a total-size result.
- Changed progress and queue displays to show observed bytes and transfer rate only, without total-size denominators, percentages, or ETA.
- Changed the updater to download and verify a versioned EXE, close the current app, then show a separate Windows prompt asking whether to open the new version; the currently installed EXE is not replaced.
- Added regression tests for size-independent progress and the post-exit update prompt.

## 2.0.8 — resilient size lookup and integrated queue tools

- Added Workshop webpage size parsing when Steam metadata reports no size or omits an item.
- Made unknown-size downloads explicitly non-blocking; byte progress and SteamCMD completion checks continue when percentage/ETA are unavailable.
- Added queue search/status filters, persistent queued-job reordering, an in-app integrity report viewer, and advisory disk-space preflight.
- Migrated existing SQLite history to persisted queue positions without deleting queue/history records or download files.
- Added regression tests for webpage fallback, queue-order migration, filters, disk preflight, and report reading.


## 2.0.7 — right-click text editing menu

- Added Cut, Copy, Paste, and Select All context actions to all editable text fields via the shared entry-field helper.
- Added a read-only-aware context menu to the activity log: Copy and Select All are available, while Cut and Paste stay disabled.
- Preserved native Tk virtual events so clipboard actions continue to use the platform's normal text handling.
- Added regression tests for menu bindings, command availability, selection actions, and read-only widgets.


## 2.0.6 — visible integrity and live data progress

- Moved the live downloaded-data and integrity-status panel above the queue table so it stays visible in shorter windows.
- Increased filesystem progress sampling frequency so the data amount updates during quiet SteamCMD transfers, even when SteamCMD prints no progress lines.
- Added a regression test ensuring periodic telemetry reports partial-file bytes before SteamCMD finishes.
- Keeps SteamCMD's native validation for integrity checks, with per-item statuses and a timestamped JSON report; partial caches are preserved.


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
