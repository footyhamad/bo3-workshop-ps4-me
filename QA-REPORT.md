# QA Report — BO3 Workshop Downloader 2.0.1

## Automated checks run locally

Environment: CPython 3.13.5 on Linux.

- `python -m compileall -q src tests scripts` — passed.
- `python -W error::ResourceWarning -m pytest -q` — **60 passed**.
- `python -m pip wheel --no-deps --no-build-isolation .` — passed for v2.0.0 before the GUI-only 2.0.1 version bump; the v2.0.1 wheel is rebuilt as part of release packaging.
- `python -m bo3wd --version`, `python -m bo3wd --help`, and both command help pages — previously passed for v2.0.0; version metadata was bumped to v2.0.1 for this fix.
- A subprocess-level integration test uses a simulated SteamCMD child: the first attempt times out, the next succeeds, the partial file remains, successful completion is checked, and the Steam account name is redacted in the log.
- Regression test verifies `_label()` accepts caller-supplied Tk color overrides without raising a duplicate-keyword `TypeError`.

## Not claimed as locally verified

- A real download from Steam Workshop was not run; that requires the user's SteamCMD setup, account entitlement, and network.
- The full Tk window was not visually exercised in this headless Linux environment. The triggering `_label()` behavior is regression-tested, and the Windows Actions job packages the actual app.
- A Windows `.exe` was not built locally. The GitHub Actions workflow contains a Windows packaging job; it must run in the newly created GitHub repository after the project is pushed.
- Ruff is not installed in this container, so lint has not been run locally; CI runs Ruff.

Partial-download and content folders are never removed by retry, cancel, error, queue recovery, or history cleanup paths. `Clear finished` removes only SQLite history records.
