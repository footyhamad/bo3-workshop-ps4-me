"""Find and safely import Workshop files already downloaded by the Steam Client."""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import APP_ID
from .identifiers import normalize_workshop_id


def steam_client_workshop_uri(workshop_id: str | int) -> str:
    """Build the official Steam Client deep-link for a numeric Workshop item ID."""
    item_id = normalize_workshop_id(workshop_id)
    return f"steam://url/CommunityFilePage/{item_id}"

_PATH_VALUE = re.compile(r'"path"\s*"((?:\\.|[^"\\])*)"', re.IGNORECASE)
_OLD_LIBRARY_VALUE = re.compile(r'"\d+"\s*"((?:\\.|[^"\\])*)"')


class SteamClientItemNotFound(FileNotFoundError):
    """No local Steam Client Workshop copy exists for an item."""

    def __init__(self, workshop_id: str, searched_libraries: Iterable[Path]):
        self.workshop_id = workshop_id
        self.searched_libraries = tuple(Path(path) for path in searched_libraries)
        searched = ", ".join(str(path) for path in self.searched_libraries) or "no Steam libraries detected"
        super().__init__(
            f"Workshop item {workshop_id} was not found in the detected Steam Client Workshop folders. "
            "This action imports only files already downloaded by Steam; it cannot bypass Steam's "
            f"availability or access restrictions. Libraries checked: {searched}"
        )


class SteamClientImportError(RuntimeError):
    """A local Steam Client copy could not be imported safely."""


@dataclass(frozen=True, slots=True)
class SteamClientItemSource:
    library_root: Path
    content_path: Path
    file_count: int
    bytes_total: int
    newest_file_mtime: float


@dataclass(frozen=True, slots=True)
class SteamClientImportResult:
    workshop_id: str
    source: Path
    destination: Path
    files_copied: int
    files_already_present: int
    conflicting_files: tuple[str, ...]
    bytes_copied: int


def _decode_vdf_path(value: str) -> str:
    """Decode the backslash escaping used by Steam's VDF files."""
    return value.replace(r"\\", "\\").replace(r"\"", '"')


def _extract_library_paths(text: str) -> list[Path]:
    """Read current path fields and the older numbered Steam library format."""
    values: list[str] = []
    for pattern in (_PATH_VALUE, _OLD_LIBRARY_VALUE):
        values.extend(_decode_vdf_path(value) for value in pattern.findall(text))
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        candidate = Path(value).expanduser()
        key = os.path.normcase(str(candidate)).casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _registry_steam_roots() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots: list[Path] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in (r"Software\Valve\Steam", r"Software\WOW6432Node\Valve\Steam"):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                        except OSError:
                            continue
                        if isinstance(value, str) and value.strip():
                            roots.append(Path(value.strip()))
            except OSError:
                continue
    return roots


def _default_steam_roots() -> list[Path]:
    home = Path.home()
    roots = [
        *_registry_steam_roots(),
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".local" / "share" / "Steam",
        home / "Library" / "Application Support" / "Steam",
    ]
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            base = Path(value)
            roots.extend((base / "Steam", base / "steam"))
    return roots


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def discover_steam_libraries(extra_roots: Iterable[str | Path] = ()) -> list[Path]:
    """Discover Steam libraries from common roots, registry entries, and libraryfolders.vdf."""
    pending = [*_default_steam_roots(), *(Path(root).expanduser() for root in extra_roots)]
    libraries: list[Path] = []
    seen: set[str] = set()
    while pending:
        candidate = pending.pop(0)
        try:
            # Steam commonly exposes a symlinked root on Linux; resolve it for read-only discovery.
            if not candidate.is_dir():
                continue
            root = candidate.resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(root)).casefold()
        if key in seen:
            continue
        seen.add(key)
        libraries.append(root)
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        pending.extend(_extract_library_paths(text))
    return libraries


def _regular_files(root: Path) -> list[tuple[Path, int, float]]:
    """Enumerate regular files without following symlinks or Windows junctions."""
    if _is_link_or_junction(root) or not root.is_dir():
        return []
    found: list[tuple[Path, int, float]] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_symlink() or _is_link_or_junction(path):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        found.append((path, max(0, int(info.st_size)), float(info.st_mtime)))
                except OSError:
                    continue
    return found


def _sources_in_libraries(item_id: str, libraries: Iterable[Path]) -> list[SteamClientItemSource]:
    sources: list[SteamClientItemSource] = []
    for library in libraries:
        content_path = library / "steamapps" / "workshop" / "content" / APP_ID / item_id
        if _is_link_or_junction(content_path):
            continue
        files = _regular_files(content_path)
        if files:
            sources.append(SteamClientItemSource(
                library_root=library,
                content_path=content_path,
                file_count=len(files),
                bytes_total=sum(size for _, size, _ in files),
                newest_file_mtime=max(mtime for _, _, mtime in files),
            ))
    return sorted(
        sources,
        key=lambda item: (
            item.bytes_total, item.file_count, item.newest_file_mtime,
            str(item.content_path).casefold(),
        ),
        reverse=True,
    )


def find_steam_client_item_sources(
    workshop_id: str | int,
    *,
    extra_library_roots: Iterable[str | Path] = (),
) -> list[SteamClientItemSource]:
    """Find non-empty local Steam Client copies, preferring the largest one."""
    item_id = normalize_workshop_id(workshop_id)
    libraries = discover_steam_libraries(extra_library_roots)
    return _sources_in_libraries(item_id, libraries)


def _ensure_real_directory(path: Path) -> None:
    if _is_link_or_junction(path):
        raise SteamClientImportError(f"Refusing to write through a symlink or junction: {path}")
    if path.exists() and not path.is_dir():
        raise SteamClientImportError(f"Expected a directory but found a file: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(path) or not path.is_dir():
        raise SteamClientImportError(f"Could not safely prepare directory: {path}")


def _copy_new_file(
    source: Path,
    destination: Path,
    on_bytes: Callable[[int], None] | None = None,
) -> int:
    """Copy atomically and report byte progress; interrupted copies never become final files."""
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.bo3wd-import-", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    written = 0
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            while True:
                chunk = incoming.read(4 * 1024 * 1024)
                if not chunk:
                    break
                outgoing.write(chunk)
                written += len(chunk)
                if on_bytes is not None:
                    on_bytes(written)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        shutil.copystat(source, temporary, follow_symlinks=False)
        # Hard-linking publishes the completed temporary file atomically and fails if the
        # destination already exists; it never silently replaces another file.
        os.link(temporary, destination)
        temporary.unlink()
        return written
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def import_steam_client_item(
    workshop_id: str | int,
    destination_content_root: str | Path,
    *,
    extra_library_roots: Iterable[str | Path] = (),
    on_progress: Callable[[int], None] | None = None,
) -> SteamClientImportResult:
    """Import a local client copy without overwriting content or bypassing Steam access checks."""
    item_id = normalize_workshop_id(workshop_id)
    libraries = discover_steam_libraries(extra_library_roots)
    sources = _sources_in_libraries(item_id, libraries)
    if not sources:
        raise SteamClientItemNotFound(item_id, libraries)

    source = sources[0].content_path
    root_input = Path(destination_content_root).expanduser()
    if _is_link_or_junction(root_input):
        raise SteamClientImportError(f"Refusing to use a symlink or junction as destination root: {root_input}")
    root = root_input.resolve()
    if root.exists() and not root.is_dir():
        raise SteamClientImportError(f"Destination content root is not a directory: {root}")
    destination = root / item_id
    if _is_link_or_junction(destination):
        raise SteamClientImportError(f"Refusing to write through a symlink or junction: {destination}")
    if destination.exists() and not destination.is_dir():
        raise SteamClientImportError(f"Destination item path is not a directory: {destination}")

    source_files = _regular_files(source)
    if not source_files:
        raise SteamClientItemNotFound(item_id, libraries)
    if source.resolve() == destination.resolve(strict=False):
        return SteamClientImportResult(item_id, source, destination, 0, len(source_files), (), 0)

    _ensure_real_directory(root)
    _ensure_real_directory(destination)

    copied = 0
    identical = 0
    bytes_copied = 0
    conflicts: list[str] = []
    for src, _size, _mtime in source_files:
        relative = src.relative_to(source)
        target = destination / relative
        current = destination
        unsafe_parent = False
        for part in relative.parts[:-1]:
            current = current / part
            try:
                if _is_link_or_junction(current):
                    unsafe_parent = True
                    break
                if current.exists():
                    if not current.is_dir():
                        unsafe_parent = True
                        break
                else:
                    current.mkdir()
            except OSError as exc:
                raise SteamClientImportError(
                    f"Import stopped after copying {copied} file(s); those files were preserved. {exc}"
                ) from exc
        if unsafe_parent:
            conflicts.append(relative.as_posix())
            continue

        try:
            if _is_link_or_junction(target):
                conflicts.append(relative.as_posix())
                continue
            if target.exists():
                if target.is_file() and filecmp.cmp(src, target, shallow=False):
                    identical += 1
                else:
                    conflicts.append(relative.as_posix())
                continue

            def report_file_progress(current_file_bytes: int, base_bytes: int = bytes_copied) -> None:
                if on_progress is not None:
                    on_progress(base_bytes + current_file_bytes)

            file_bytes = _copy_new_file(src, target, report_file_progress)
            copied += 1
            bytes_copied += file_bytes
        except OSError as exc:
            raise SteamClientImportError(
                f"Import stopped after copying {copied} file(s); those files were preserved. {exc}"
            ) from exc

    return SteamClientImportResult(
        workshop_id=item_id,
        source=source,
        destination=destination,
        files_copied=copied,
        files_already_present=identical,
        conflicting_files=tuple(conflicts),
        bytes_copied=bytes_copied,
    )
