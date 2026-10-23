"""Command-line entry points for download, diagnostics and launching the GUI."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from . import APP_NAME, __version__
from .engine import DownloadEngine
from .identifiers import parse_workshop_list
from .metadata import MetadataError, WorkshopMetadataClient
from .models import DownloadPaths, DownloadPolicy, JobStatus, WorkshopItem
from .steamcmd import discover_steamcmd, read_saved_account
from .storage import JobHistory, default_data_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bo3wd", description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    download = commands.add_parser("download", help="download one or more Workshop items")
    download.add_argument("items", nargs="+", help="Workshop IDs or Steam Workshop URLs")
    download.add_argument("--steamcmd", type=Path, help="path to steamcmd.exe")
    download.add_argument("--username", help="Steam account name already logged into SteamCMD")
    download.add_argument("--data-dir", type=Path, default=default_data_dir(), help="settings, history and logs directory")
    download.add_argument("--attempts", type=int, default=30)
    download.add_argument("--timeout-minutes", type=int, default=15)
    download.add_argument("--no-validate", action="store_true", help="skip SteamCMD file validation")
    download.add_argument("--verbose", action="store_true", help="print SteamCMD output while saving full logs")

    doctor = commands.add_parser("doctor", help="check local setup without downloading anything")
    doctor.add_argument("--steamcmd", type=Path, help="path to steamcmd.exe")
    doctor.add_argument("--username", help="Steam account name")
    doctor.add_argument("--data-dir", type=Path, default=default_data_dir())
    return parser


def _get_steamcmd(value: Path | None) -> Path | None:
    selected = value or discover_steamcmd()
    if selected is None:
        return None
    return selected.expanduser().resolve()


def _run_download(args: argparse.Namespace) -> int:
    try:
        raw_ids = parse_workshop_list("\n".join(args.items))
    except ValueError as exc:
        print(f"invalid Workshop item: {exc}", file=sys.stderr)
        return 2
    steamcmd = _get_steamcmd(args.steamcmd)
    if steamcmd is None or not steamcmd.is_file():
        print("SteamCMD not found. Install it or pass --steamcmd PATH.", file=sys.stderr)
        return 2
    username = (args.username or read_saved_account(steamcmd) or "").strip()
    if not username:
        print("SteamCMD account is unknown. Pass --username or log in to SteamCMD once manually.", file=sys.stderr)
        return 2
    if args.attempts < 1 or args.timeout_minutes < 1:
        print("--attempts and --timeout-minutes must be positive.", file=sys.stderr)
        return 2

    details: dict[str, WorkshopItem] = {}
    try:
        for offset in range(0, len(raw_ids), 100):
            details.update(WorkshopMetadataClient().fetch_many(raw_ids[offset:offset + 100]))
    except MetadataError as exc:
        print(f"warning: {exc}; continuing without item titles/sizes", file=sys.stderr)

    try:
        paths = DownloadPaths.from_steamcmd(steamcmd, args.data_dir)
        history = JobHistory(args.data_dir / "history.sqlite3")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"could not prepare downloader state or storage: {exc}", file=sys.stderr)
        return 2
    policy = DownloadPolicy(max_attempts=args.attempts, attempt_timeout_seconds=args.timeout_minutes * 60)
    exit_code = 0

    def show(event) -> None:
        prefix = f"[{event.workshop_id} try {event.attempt}/{event.max_attempts or args.attempts}]"
        if event.line and args.verbose:
            print(f"{prefix} {event.line}", flush=True)
        elif event.status in {JobStatus.PREPARING, JobStatus.RETRY_WAIT, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            size = f" ({event.bytes_received / 1_000_000:.1f} MB retained)" if event.bytes_received else ""
            print(f"{prefix} {event.status.value}: {event.message}{size}", flush=True)

    engine = DownloadEngine(paths, username, policy=policy, validate=not args.no_validate, history=history, event_sink=show)
    for item_id in raw_ids:
        item = details.get(item_id) or WorkshopItem(workshop_id=item_id)
        history.ensure_item(item, paths.logs_root / f"{item_id}.log")
        result = engine.download(item)
        if result.status != JobStatus.COMPLETED:
            exit_code = 1
    print(f"Logs: {paths.logs_root}")
    print(f"SteamCMD content cache: {paths.content_root} (never auto-deleted)")
    print(f"Partial downloads: {paths.partial_root} (never auto-deleted)")
    return exit_code


def _run_doctor(args: argparse.Namespace) -> int:
    steamcmd = _get_steamcmd(args.steamcmd)
    problems = 0
    print(f"{APP_NAME} {__version__}")
    if steamcmd is None or not steamcmd.is_file():
        print("[FAIL] SteamCMD executable not found")
        problems += 1
    else:
        print(f"[ OK ] SteamCMD: {steamcmd}")
        account = (args.username or read_saved_account(steamcmd) or "").strip()
        if account:
            print(f"[ OK ] SteamCMD account name available: {account}")
        else:
            print("[FAIL] No saved account name detected; log in manually using SteamCMD first")
            problems += 1
        paths = DownloadPaths.from_steamcmd(steamcmd, args.data_dir)
        for label, folder in (("content cache", paths.content_root), ("partial cache", paths.partial_root), ("logs", paths.logs_root)):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                print(f"[ OK ] {label}: {folder}")
            except OSError as exc:
                print(f"[FAIL] {label}: {exc}")
                problems += 1
        try:
            free = shutil.disk_usage(paths.steamcmd.parent).free / 2**30
            print(f"[ OK ] Free disk space beside SteamCMD: {free:.1f} GiB")
            if free < 5:
                print("[WARN] Under 5 GiB free; large Workshop items may fail")
        except OSError as exc:
            print(f"[WARN] Cannot measure free space: {exc}")
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        print(f"[ OK ] App data: {args.data_dir}")
    except OSError as exc:
        print(f"[FAIL] App data directory: {exc}")
        problems += 1
    print("No download was started and no files were deleted.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    actual = list(sys.argv[1:] if argv is None else argv)
    if not actual:
        from .gui import launch

        launch()
        return 0
    parser = _parser()
    args = parser.parse_args(actual)
    if args.command == "download":
        return _run_download(args)
    if args.command == "doctor":
        return _run_doctor(args)
    parser.print_help()
    return 0 if args.command is None else 2
