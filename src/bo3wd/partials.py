"""Safe inspection and cleanup of stale SteamCMD Workshop partial downloads."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_STALE_DAYS = 7


@dataclass(frozen=True, slots=True)
class PartialFolder:
    workshop_id: str
    path: Path
    size_bytes: int
    last_modified: float


def _folder_stats(folder: Path) -> tuple[int, float]:
    """Return total regular-file bytes and the newest timestamp without following symlinks."""
    total = 0
    newest = folder.stat(follow_symlinks=False).st_mtime
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                newest = max(newest, info.st_mtime)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += max(0, int(info.st_size))
    return total, newest


def find_stale_partial_folders(
    partial_root: Path,
    *,
    older_than_days: int = DEFAULT_STALE_DAYS,
    protected_ids: Iterable[str] = (),
    now: float | None = None,
) -> list[PartialFolder]:
    """Find numeric-ID partial folders with no file activity for the configured age."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    root = Path(partial_root).expanduser().resolve()
    if not root.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - older_than_days * 86400
    protected = {str(value) for value in protected_ids}
    stale: list[PartialFolder] = []
    try:
        children = list(os.scandir(root))
    except OSError:
        return []
    for entry in children:
        name = entry.name
        if (
            name in protected
            or not name.isascii()
            or not name.isdigit()
            or len(name) > 20
            or entry.is_symlink()
            or not entry.is_dir(follow_symlinks=False)
        ):
            continue
        folder = Path(entry.path).resolve()
        # Only consider an immediate child of the validated SteamCMD partial root.
        if folder.parent != root:
            continue
        try:
            size, newest = _folder_stats(folder)
        except OSError:
            continue
        if newest <= cutoff:
            stale.append(PartialFolder(name, folder, size, newest))
    return sorted(stale, key=lambda item: (item.last_modified, item.workshop_id))


def delete_old_partial_folders(
    partial_root: Path,
    workshop_ids: Iterable[str],
    *,
    older_than_days: int = DEFAULT_STALE_DAYS,
    protected_ids: Iterable[str] = (),
    now: float | None = None,
) -> tuple[list[PartialFolder], list[tuple[str, str]]]:
    """Delete only IDs still stale at deletion time; never touches installed Workshop content."""
    requested = {str(value) for value in workshop_ids}
    if not requested:
        return [], []
    stale = find_stale_partial_folders(
        partial_root,
        older_than_days=older_than_days,
        protected_ids=protected_ids,
        now=now,
    )
    deleted: list[PartialFolder] = []
    errors: list[tuple[str, str]] = []
    for folder in stale:
        if folder.workshop_id not in requested:
            continue
        # Re-check immediately before removing, and reject a replaced symlink.
        if folder.path.is_symlink() or folder.path.parent.resolve() != Path(partial_root).expanduser().resolve():
            errors.append((folder.workshop_id, "Folder path changed during cleanup; skipped."))
            continue
        try:
            shutil.rmtree(folder.path)
            deleted.append(folder)
        except OSError as exc:
            errors.append((folder.workshop_id, str(exc)))
    missing = requested - {item.workshop_id for item in stale}
    for workshop_id in sorted(missing):
        errors.append((workshop_id, "No longer stale, protected, missing, or inaccessible; skipped."))
    return deleted, errors
