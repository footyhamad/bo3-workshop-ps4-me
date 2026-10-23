# BO3 Workshop Downloader — v2

A clean, standalone Windows-first rewrite of a Black Ops III Steam Workshop downloader. It wraps **your own SteamCMD installation**; it does not download game files from third-party mirrors, bypass Steam authentication, or delete Workshop caches to “fix” a failed transfer.

## What it does

- **Resumes through SteamCMD itself.** On timeouts/network failures, it reruns the same Workshop request so SteamCMD can reuse its partial chunks. The downloader never deletes `steamapps/workshop/downloads/311210/<id>` or downloaded content as part of retries.
- **Bounded retries.** Per-attempt watchdog, capped exponential backoff, and a no-progress guard prevent infinite loops. Authentication and explicit access/item errors stop early with a useful message.
- **Conservative success checks.** A job is only marked complete when SteamCMD reports success for the exact requested ID, exits successfully, and the resulting content directory contains files. An old folder or a successful-looking log line alone is not enough.
- **Silent SteamCMD with live transfer progress.** SteamCMD runs without a separate console window; its output still streams into a per-item log and the app. The UI shows downloaded bytes, total bytes and percentage when Steam's size metadata is available, plus observed transfer speed and an estimated ETA. When total size is unknown, it continues showing bytes downloaded and an indeterminate progress bar. On-disk data is an estimate of transfer progress, not a guaranteed network-byte counter.
- **Version-safe updater.** The **Check / Update** button compares stable `MAJOR.MINOR.PATCH` versions numerically, ignores drafts and prereleases, and refuses downgrades. It downloads only the official GitHub Release executable, verifies its SHA-256 against the release manifest, then uses a hidden Windows helper to back up the old EXE, replace it after shutdown, and restart the app.
- **Queue and history persist.** The UI queue/history is stored in SQLite; preferences are atomically written to JSON. Interrupted jobs are recovered into the queue after an abnormal exit. Closing or stopping the app does not clean up partial data.
- **ID and URL input.** Paste one ID, a Steam Workshop URL, or a newline/comma/semicolon-separated list. Duplicate IDs are removed. A text list can be imported from disk.
- **No third-party runtime dependencies.** The app uses Python's standard library and Tkinter. Tests use pytest; the Windows executable is built with PyInstaller.

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

The UI is the default when run without arguments. The main window has settings, an ID/URL entry box, file import, a persistent queue/history table with downloaded-data totals, a live progress bar, retry/remove controls, a live log pane, and buttons to open the logs or a selected item’s content folder.

## Updating the application

Use **Check / Update** in the packaged Windows EXE. It compares version numbers rather than strings, downloads only a stable published release that includes both `BO3WorkshopDownloader.exe` and `SHA256SUMS.txt`, and refuses to install if the checksum or version check fails. It never overwrites a running executable directly: a hidden helper waits for the app to exit, keeps a versioned backup, installs the staged file, and relaunches the app. Update-helper errors are recorded at `%LOCALAPPDATA%\\BO3WorkshopDownloader\\update.log`.

The updater is available in v2.0.3 and later. Older builds do not contain it, so update to v2.0.3 manually once. The first stable release is published from **Actions → Stable Release → Run workflow**, using version `2.0.3`; the release workflow verifies that this exactly matches `pyproject.toml`, runs tests and lint, builds the EXE, generates the SHA-256 manifest, and publishes both assets. Later releases can be published from matching `vMAJOR.MINOR.PATCH` tags. When run from source with `python -m bo3wd`, the button opens the release page instead of trying to overwrite the Python interpreter.

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

- `src/bo3wd/engine.py` — retry state machine, completion checks and progress telemetry.
- `src/bo3wd/process.py` — streaming subprocess supervision, cancellation and watchdog.
- `src/bo3wd/steamcmd.py` — SteamCMD discovery, saved-account lookup and safe argument construction.
- `src/bo3wd/metadata.py` — public Workshop title/size metadata.
- `src/bo3wd/storage.py` — atomic settings and SQLite queue/history.
- `src/bo3wd/updater.py` — numeric version selection, official release asset checks, SHA-256 verification, and hidden Windows self-update helper.
- `src/bo3wd/gui.py` — background-worker desktop interface and Check / Update button.
- `src/bo3wd/cli.py` — command-line interface and diagnostics.
- `tests/` — offline regression tests; no Steam login or live download required.
