"""SteamCMD discovery and safe command construction."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import APP_ID
from .identifiers import normalize_workshop_id


def discover_steamcmd() -> Path | None:
    """Locate a SteamCMD executable without assuming a drive letter."""
    executable = "steamcmd.exe" if os.name == "nt" else "steamcmd"
    from_path = shutil.which(executable) or shutil.which("steamcmd")
    candidates: list[Path] = []
    if from_path:
        candidates.append(Path(from_path))
    home = Path.home()
    candidates.extend([home / "steamcmd" / executable, Path("C:/steamcmd/steamcmd.exe")])
    for env_name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value) / "SteamCMD" / "steamcmd.exe")
            candidates.append(Path(value) / "Steam" / "steamapps" / "common" / "SteamCMD" / "steamcmd.exe")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def build_download_command(steamcmd: Path, username: str, workshop_id: str, *, validate: bool = True) -> list[str]:
    """Build argv for subprocess without shell interpolation."""
    item_id = normalize_workshop_id(workshop_id)
    user = username.strip()
    if not user:
        raise ValueError("Steam account name is required. Log in to SteamCMD once first.")
    if any(ord(char) < 32 for char in user):
        raise ValueError("Steam account name contains unsupported control characters")
    command = [str(Path(steamcmd)), "+login", user, "+workshop_download_item", APP_ID, item_id]
    if validate:
        command.append("validate")
    command.append("+quit")
    return command


def read_saved_account(steamcmd: Path) -> str | None:
    """Read the first saved SteamCMD account name, if present; never reads credentials."""
    config = Path(steamcmd).parent / "config" / "config.vdf"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'"Accounts"\s*\{\s*"([^"\\]+)"', text, re.IGNORECASE)
    return match.group(1) if match else None
