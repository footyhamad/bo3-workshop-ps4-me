# BO3 Workshop Downloader — v2

A clean, standalone Windows-first rewrite of a Black Ops III Steam Workshop downloader. It wraps **your own SteamCMD installation**; it does not download game files from third-party mirrors, bypass Steam authentication, or delete Workshop caches to “fix” a failed transfer.

## What it does

- **Resumes through SteamCMD itself.** On timeouts/network failures, it reruns the same Workshop request so SteamCMD can reuse its partial chunks. The downloader never deletes `steamapps/workshop/downloads/311210/<id>` or downloaded content as part of retries.
- **Bounded retries.** Per-attempt watchdog, capped exponential backoff, and a no-progress guard prevent infinite loops. Authentication and explicit access/item errors stop early with a useful message.
- **Conservative success checks.** A job is only marked complete when SteamCMD reports success for the exact requested ID, exits successfully, and the resulting content directory contains files. An old folder or a successful-looking log line alone is not enough.
- **Live KB-by-KB progress.** The UI shows whole kilobytes downloaded and download-write rate as files grow. A faster `scandir`-based sampler refreshes frequently without requiring Steam's total size, percentage, or ETA. If disk bytes stop changing for five seconds, the live line says so instead of silently appearing frozen.
- **Visible queue recovery controls.** The redesigned dashboard has separate Start / Resume Queue, Resume Selected, Resume Old Jobs, Recover Partial Files, and Add + Start actions. It shows queued, active, completed, and failed/cancelled counts. Recover Partial Files scans SteamCMD's Workshop partial cache for numeric IDs, adds them back to history, and never deletes the existing files.
- **Import from Steam Client.** Select a queue item and use Import Steam Client Copy to search local Steam libraries (including paths from `libraryfolders.vdf`) for files the official client has already downloaded. It can scan a custom library folder if automatic discovery misses it. It copies only existing local files into the SteamCMD content folder, keeps identical files, skips conflicting files, and never overwrites existing content. It does not trigger a Steam download or bypass Steam access/removal restrictions, and imported files are not marked as SteamCMD-verified.
- **Explicit Workshop integrity validation.** The Check File Integrity action runs SteamCMD's native Workshop validation on either the selected installed item or every installed content folder. It records pass/fail/cancelled results and paths in a JSON report under `%LOCALAPPDATA%\\BO3WorkshopDownloader\\integrity-reports`. SteamCMD validation can download or repair changed/missing files, so the app asks before starting; it does not delete the partial-download cache.
- **Version-safe updater.** The **Check / Update** button compares stable `MAJOR.MINOR.PATCH` versions numerically, ignores drafts and prereleases, and refuses downgrades. It downloads only the official GitHub Release executable and verifies its SHA-256 against the release manifest. The current app then closes; a hidden helper waits for shutdown and prompts whether to open the new versioned EXE. The existing EXE is left untouched.
- **Queue and history persist.** The UI queue/history is stored in SQLite; preferences are atomically written to JSON. Interrupted jobs are recovered into the queue after an abnormal exit. Closing or stopping the app does not clean up partial data.
- **ID and URL input.** Paste one ID, a Steam Workshop URL, or a newline/comma/semicolon-separated list. Duplicate IDs are removed. A text list can be imported from disk.
- **Right-click editing menu.** Every editable text field has a native Cut / Copy / Paste / Select All context menu on right-click, including SteamCMD setup, account name, retry/timeout values, and Workshop IDs. The activity log also supports right-click Copy / Select All while keeping editing actions disabled.
- **Immediate downloads without size lookup.** The GUI does not request Steam/API/page size metadata when adding items. New jobs start with an unknown total, and the downloader shows observed bytes and rate without reporting a total size, percentage, or ETA. SteamCMD's success marker, process exit code, and nonempty output files—not size reporting—determine completion.
- **Search, filter, reorder, and report viewer.** Search the queue by ID/title/status/message, filter visible rows without changing stored jobs, move queued items up/down with persistent ordering, and view prior integrity JSON reports inside the app.
- **Disk-space preflight.** Before downloads or integrity repair, any previously saved size estimates are compared with free space on the SteamCMD drive and a safety reserve. New downloads do not fetch size metadata, so their totals are unknown; unknown-size jobs are excluded from the estimate and never automatically blocked.
- **Network speed and safe partial cleanup.** A separate RX/TX meter samples system network-interface counters (it includes traffic from other apps). The cleanup action previews numeric Workshop partial folders with no file activity for at least seven days, excludes queued/active jobs, rechecks age before removal, and never removes installed content.

## Requirements

- Windows 10/11 (primary target), Python 3.11+ for running from source.
- SteamCMD installed locally and initialized once with the Steam account that owns/has access to the item.
- Black Ops III Workshop AppID `311210`; this application intentionally does not accept a different game AppID.
- Enough free disk space for the Workshop item. Large items may require many gigabytes.

## Install and run from source

Open PowerShell in the repository folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m bo3wd
```

Select your actual SteamCMD executable, for example `B:\scmd\steamcmd.exe`, and enter the Steam account name used for the saved SteamCMD login. If you have not initialized SteamCMD yet, run `steamcmd.exe +login <account> +quit` manually once and complete Steam Guard there. The app disables stdin for managed attempts so an unexpected password prompt cannot hang a background worker.

The UI is the default when run without arguments. Use **Add + Start** to add an item and run the queue, **Start / Resume Queue** to run queued items, **Resume Selected** to retry the highlighted entry, and **Resume Old Jobs** to requeue failed/cancelled history. To import an item that the official Steam client has already downloaded, select that ID and click **Import Steam Client Copy**; if no installed copy is found automatically, choose the Steam library folder to scan. This only copies existing local files and does not bypass Steam availability or access restrictions. **Recover Partial Files** finds IDs in SteamCMD's existing partial cache without changing cache files. **Delete Old Partial Data** scans for numeric Workshop partial-cache folders with no file activity for seven days, previews the candidate IDs and sizes, and asks before deleting; queued items and installed content are preserved. **Check File Integrity** validates the selected installed item, or all installed items when no row is selected. **Integrity Reports** opens prior reports. Queue search/filter/reordering remain available. Adding new downloads does not query Steam or webpage size metadata; jobs show KB progress and transfer rates as data is observed. The network meter reports total system RX/TX, not SteamCMD-only traffic.

## Updating the application

Use **Check for Updates** in the packaged Windows EXE. It compares version numbers, downloads a stable GitHub Release, and verifies the executable SHA-256 against `SHA256SUMS.txt`. After the verified versioned EXE is staged, the current app closes and a separate Windows prompt asks whether to open the new version. The original EXE is not replaced; continue launching the versioned EXE after accepting an update.

Version 2.2.0 improves SteamCMD failure handling: explicit item-level failure output is reported immediately even when SteamCMD exits with code 0, while explicit connection errors remain retryable. The release workflow validates versions, runs tests and lint, builds the standalone EXE, and publishes the binary plus checksum manifest.

## CLI

```powershell
# Check paths and saved account detection; this does not start a download
python -m bo3wd doctor --steamcmd B:\scmd\steamcmd.exe --username YOUR_STEAM_ACCOUNT

# Download one or more items
python -m bo3wd download 3296316642 1234567890 `
  --steamcmd B:\scmd\steamcmd.exe `
  --username YOUR_STEAM_ACCOUNT

# Import from a text file using the GUI, or pass URLs directly to the CLI
python -m bo3wd download "https://steamcommunity.com/sharedfiles/filedetails/?id=3296316642" `
  --steamcmd B:\scmd\steamcmd.exe --username YOUR_STEAM_ACCOUNT
```

`bo3wd download --help` lists retry, watchdog, validation, verbosity and data-directory options. The CLI returns a nonzero exit code if any requested item fails.

## Recovery behavior

- An internal SteamCMD timeout causes a fresh SteamCMD invocation for the **same item**, not deletion of the partial directory.
- A hung SteamCMD process is terminated by a watchdog. This does not remove or truncate the downloader's existing partial files.
- Recognized authentication prompts stop immediately. Complete login/Steam Guard interactively once, then retry the item.
- Explicit access-denied, missing-item and subscription errors stop immediately; the downloader does not attempt to bypass ownership or permissions.
- An explicit `ERROR! Download item <id> failed (Failure)` now stops that job immediately even if SteamCMD reports exit code 0; explicit connection/time-out failures remain retryable. This prevents a long retry loop when SteamCMD has already declared the item failed.
- Repeated attempts with no observed growth stop after the configured stagnant-attempt budget. The existing partial and completed content remain available for another manual retry. A process crash leaves job state recoverable on the next app launch.
- `Stop current attempt` terminates the current SteamCMD process. Items not yet started remain queued. Use **Retry selected** to queue a cancelled/failed/completed row again; this only changes history/queue state.

## Data locations

By default on Windows:

- Settings: `%LOCALAPPDATA%\BO3WorkshopDownloader\settings.json`
- Persistent queue/history: `%LOCALAPPDATA%\BO3WorkshopDownloader\history.sqlite3`
- Logs: `%LOCALAPPDATA%\BO3WorkshopDownloader\logs\<workshop-id>.log`
- SteamCMD content: `<SteamCMD folder>\steamapps\workshop\content\311210\<id>`
- SteamCMD partial data: `<SteamCMD folder>\steamapps\workshop\downloads\311210\<id>`

The SteamCMD paths are discovered relative to the executable you selected, including non-`C:` drives like `B:\scmd`. Cache files are intentionally not placed inside the app's own data folder.

## Tests and debugging

```powershell
python -m compileall -q src tests
python -m pytest -q
ruff check src tests
```

The test suite uses synthetic SteamCMD runs and temporary directories. It covers ID parsing, URL validation, settings/database persistence, streaming logs, process watchdog/cancellation, authentication failures, permanent errors, success-marker verification, partial-data preservation, backoff/stagnation behavior and metadata response parsing. CI also builds the Windows executable.

**What is not claimed:** automated tests cannot prove a live Steam download will succeed on your network or verify your individual Workshop item's contents. After CI passes, do one real download and inspect its generated per-item log. This repository intentionally includes no game files or downloaded Workshop content.

## Build a Windows executable

```powershell
python -m pip install -e ".[build]"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name BO3WorkshopDownloader --paths src scripts\launcher.py
```

Output: `dist\BO3WorkshopDownloader.exe`.

## Project structure

- `src/bo3wd/engine.py` — retry state machine, efficient partial/content byte sampling, completion checks and progress telemetry.
- `src/bo3wd/process.py` — streaming subprocess supervision, cancellation and watchdog.
- `src/bo3wd/steamcmd.py` — SteamCMD discovery, saved-account lookup and safe argument construction.
- `src/bo3wd/metadata.py` — optional parser retained for tests/tools; the GUI does not use it to gate downloads.
- `src/bo3wd/storage.py` — atomic settings and SQLite queue/history.
- `src/bo3wd/updater.py` — numeric version selection, official release asset checks, SHA-256 verification, and a post-exit prompt to open the staged executable.
- `src/bo3wd/gui.py` — background-worker desktop interface and Check / Update button.
- `src/bo3wd/cli.py` — command-line interface and diagnostics.
- `tests/` — offline regression tests; no Steam login or live download required.
