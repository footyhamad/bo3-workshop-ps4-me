"""Version-safe GitHub Releases updater for the packaged Windows application."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY = "footyhamad/bo3-workshop-ps4-me"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
APP_EXE_NAME = "BO3WorkshopDownloader.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
MAX_EXECUTABLE_BYTES = 250 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_VERSION_RE = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?BO3WorkshopDownloader\.exe$")


class UpdateError(RuntimeError):
    """A release could not be safely checked, downloaded, or applied."""


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    current_version: str
    latest_version: str
    tag_name: str
    release_url: str
    executable_url: str
    checksum_url: str
    executable_size: int = 0

    @property
    def update_available(self) -> bool:
        return parse_version(self.latest_version) > parse_version(self.current_version)


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse strict stable MAJOR.MINOR.PATCH versions; never compare them as strings."""
    match = _VERSION_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Invalid stable version {value!r}; expected MAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _request_bytes(url: str, *, timeout: float, max_bytes: int, github_json: bool = False) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_DOWNLOAD_HOSTS:
        raise UpdateError("The update server returned an untrusted download URL.")
    headers = {
        "User-Agent": f"BO3WorkshopDownloader/{APP_EXE_NAME}",
        "Accept": "application/vnd.github+json" if github_json else "application/octet-stream",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or (final.hostname or "").lower() not in _ALLOWED_DOWNLOAD_HOSTS:
                raise UpdateError("The update download redirected to an untrusted host.")
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    if int(declared_size) > max_bytes:
                        raise UpdateError("The update server reported a file larger than the allowed size.")
                except ValueError:
                    raise UpdateError("The update server returned an invalid content length.") from None
            result = response.read(max_bytes + 1)
    except UpdateError:
        raise
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise UpdateError(f"Could not contact GitHub Releases: {exc}") from exc
    if len(result) > max_bytes:
        raise UpdateError("The update download exceeded the allowed size.")
    return result


def _stable_release(release: dict[str, Any]) -> tuple[tuple[int, int, int], str, str, dict[str, dict[str, Any]]] | None:
    if release.get("draft") is True or release.get("prerelease") is True:
        return None
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        return None
    try:
        version_key = parse_version(tag)
    except ValueError:
        return None
    html_url = release.get("html_url")
    parsed_release_url = urllib.parse.urlparse(html_url if isinstance(html_url, str) else "")
    if parsed_release_url.scheme != "https" or parsed_release_url.netloc.lower() != "github.com":
        return None
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    by_name: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        name = asset["name"]
        url = asset.get("browser_download_url")
        size = asset.get("size", 0)
        parsed_asset_url = urllib.parse.urlparse(url if isinstance(url, str) else "")
        if (
            parsed_asset_url.scheme != "https"
            or parsed_asset_url.netloc.lower() != "github.com"
            or not parsed_asset_url.path.startswith(f"/{REPOSITORY}/releases/download/")
        ):
            continue
        by_name[name] = asset
    if APP_EXE_NAME not in by_name or CHECKSUM_NAME not in by_name:
        return None
    try:
        size = max(0, int(by_name[APP_EXE_NAME].get("size") or 0))
    except (TypeError, ValueError, OverflowError):
        size = 0
    if size > MAX_EXECUTABLE_BYTES:
        return None
    return version_key, tag, html_url, by_name


def check_for_update(current_version: str, *, timeout: float = 12.0) -> ReleaseInfo:
    """Select the highest valid stable release by numeric version, not tag text or date."""
    parse_version(current_version)
    payload_bytes = _request_bytes(RELEASES_API, timeout=timeout, max_bytes=5 * 1024 * 1024, github_json=True)
    try:
        releases = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned malformed release metadata.") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub returned an unexpected release-list format.")

    valid: list[tuple[tuple[int, int, int], str, str, dict[str, dict[str, Any]]]] = []
    for entry in releases:
        if isinstance(entry, dict):
            parsed = _stable_release(entry)
            if parsed is not None:
                valid.append(parsed)
    if not valid:
        raise UpdateError(
            "No stable release with a verified executable and SHA256SUMS.txt is published yet."
        )
    version_key, tag, release_url, assets = max(valid, key=lambda entry: entry[0])
    version = ".".join(str(part) for part in version_key)
    return ReleaseInfo(
        current_version=current_version,
        latest_version=version,
        tag_name=tag,
        release_url=release_url,
        executable_url=assets[APP_EXE_NAME]["browser_download_url"],
        checksum_url=assets[CHECKSUM_NAME]["browser_download_url"],
        executable_size=max(0, int(assets[APP_EXE_NAME].get("size") or 0)),
    )


def download_verified_update(
    release: ReleaseInfo,
    destination_dir: Path,
    *,
    target_name: str = APP_EXE_NAME,
    timeout: float = 45.0,
) -> Path:
    """Download a release executable and its checksum, then stage it beside the installed app."""
    if not release.update_available:
        raise UpdateError("Refusing to download an update that is not newer than the installed version.")
    destination = Path(destination_dir).expanduser().resolve()
    if not destination.is_dir():
        raise UpdateError(f"Application directory does not exist: {destination}")
    if release.executable_size > MAX_EXECUTABLE_BYTES:
        raise UpdateError("The release executable exceeds the configured safety limit.")

    checksum_payload = _request_bytes(
        release.checksum_url, timeout=timeout, max_bytes=MAX_CHECKSUM_BYTES
    )
    expected_hash: str | None = None
    try:
        for line in checksum_payload.decode("utf-8").splitlines():
            match = _CHECKSUM_RE.fullmatch(line.strip())
            if match:
                expected_hash = match.group(1).lower()
                break
    except UnicodeDecodeError as exc:
        raise UpdateError("The release checksum manifest is not valid UTF-8.") from exc
    if expected_hash is None:
        raise UpdateError("The release checksum manifest has no entry for the application executable.")

    staged_name = f"{Path(target_name).stem}.update-v{release.latest_version}{Path(target_name).suffix}"
    staged_path = destination / staged_name
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{Path(target_name).stem}.update-", suffix=".part", dir=destination
    )
    hasher = hashlib.sha256()
    total = 0
    try:
        parsed = urllib.parse.urlparse(release.executable_url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not parsed.path.startswith(f"/{REPOSITORY}/releases/download/"):
            raise UpdateError("The release executable URL does not belong to the configured GitHub repository.")
        request = urllib.request.Request(
            release.executable_url,
            headers={"User-Agent": "BO3WorkshopDownloader-Updater", "Accept": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() not in _ALLOWED_DOWNLOAD_HOSTS:
                    raise UpdateError("The executable download redirected to an untrusted host.")
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_EXECUTABLE_BYTES:
                    raise UpdateError("The release executable exceeds the configured safety limit.")
                with os.fdopen(fd, "wb") as output:
                    fd = -1
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_EXECUTABLE_BYTES:
                            raise UpdateError("The release executable exceeded the configured safety limit.")
                        hasher.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
        except UpdateError:
            raise
        except (OSError, ValueError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise UpdateError(f"Could not download the update executable: {exc}") from exc

        if total <= 0:
            raise UpdateError("The release executable download was empty.")
        if release.executable_size and total != release.executable_size:
            raise UpdateError(
                f"Downloaded size mismatch (expected {release.executable_size:,} bytes, received {total:,})."
            )
        actual_hash = hasher.hexdigest()
        if actual_hash != expected_hash:
            raise UpdateError("SHA-256 verification failed. The downloaded update will not be applied.")
        os.replace(temp_name, staged_path)
        return staged_path
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _b64_path(path: Path) -> str:
    return base64.b64encode(str(path.resolve()).encode("utf-8")).decode("ascii")


def _powershell_update_script(
    source: Path, target: Path, backup: Path, log_path: Path, parent_pid: int
) -> str:
    """Build a PowerShell script with paths encoded as data to avoid command injection."""
    decode = "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{0}'))"
    return f"""
$ErrorActionPreference = 'Stop'
$parentProcessId = {int(parent_pid)}
$source = {decode.format(_b64_path(source))}
$target = {decode.format(_b64_path(target))}
$backup = {decode.format(_b64_path(backup))}
$logPath = {decode.format(_b64_path(log_path))}
try {{
    $parent = Get-Process -Id $parentProcessId -ErrorAction SilentlyContinue
    if ($null -ne $parent) {{ $parent.WaitForExit() }}
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {{ throw 'Staged update file is missing.' }}
    if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
    if (Test-Path -LiteralPath $target) {{ Move-Item -LiteralPath $target -Destination $backup -Force }}
    try {{
        Move-Item -LiteralPath $source -Destination $target -Force
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {{ throw 'Updated executable is missing after replacement.' }}
        Start-Process -FilePath $target
    }} catch {{
        if (-not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $backup)) {{
            Move-Item -LiteralPath $backup -Destination $target -Force
        }}
        throw
    }}
}} catch {{
    try {{ Add-Content -LiteralPath $logPath -Value ('Update failed: ' + $_.Exception.Message) -Encoding UTF8 }} catch {{}}
    exit 1
}}
"""


def launch_self_update(staged_exe: Path, target_exe: Path, current_version: str) -> None:
    """Launch a hidden helper that waits for this process, replaces the EXE, and relaunches it."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError("Automatic replacement is supported only by the packaged Windows executable.")
    staged = Path(staged_exe).resolve()
    target = Path(target_exe).resolve()
    if staged.parent != target.parent:
        raise UpdateError("The staged update must be in the same folder as the installed application.")
    if not staged.is_file() or staged.stat().st_size <= 0:
        raise UpdateError("The verified staged executable is missing or empty.")
    if not target.is_file():
        raise UpdateError("Could not locate the running application executable.")
    backup = target.with_name(f"{target.stem}.backup-v{current_version}{target.suffix}")
    log_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BO3WorkshopDownloader" / "update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Fail before closing the app if the installation folder is not writable.
    fd, probe = tempfile.mkstemp(prefix=".bo3wd-write-test-", dir=target.parent)
    os.close(fd)
    os.unlink(probe)

    script = _powershell_update_script(staged, target, backup, log_path, os.getpid())
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-EncodedCommand", encoded_script,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start the update helper: {exc}") from exc
