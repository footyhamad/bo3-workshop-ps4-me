"""Strict parsing for Steam Workshop IDs and URLs."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_ID = re.compile(r"^[0-9]{1,20}$")
_HOSTS = {"steamcommunity.com", "www.steamcommunity.com", "steamcommunity.com.cn"}


def normalize_workshop_id(value: str | int) -> str:
    """Accept a numeric ID or Workshop URL; reject anything ambiguous or unsafe."""
    text = str(value).strip()
    if not text:
        raise ValueError("Workshop ID is empty")
    if _ID.fullmatch(text):
        normalized = str(int(text))
        if normalized == "0":
            raise ValueError("Workshop ID must be a positive integer")
        return normalized

    candidate = text if "://" in text else "https://" + text
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _HOSTS:
        raise ValueError(f"Not a valid Steam Workshop URL or numeric ID: {text!r}")
    if not re.search(r"(?:^|/)sharedfiles/filedetails/?$|(?:^|/)workshop/filedetails/?$", parsed.path):
        raise ValueError(f"URL is not a Steam Workshop item page: {text!r}")
    values = parse_qs(parsed.query).get("id", [])
    if len(values) != 1 or not _ID.fullmatch(values[0]):
        raise ValueError(f"Workshop URL must contain exactly one numeric id parameter: {text!r}")
    return values[0]


def parse_workshop_list(text: str) -> list[str]:
    """Parse IDs/URLs from pasted text, one per line or separated by commas/whitespace."""
    found: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = re.split(r"[\s,;]+", line)
        for token in tokens:
            if not token:
                continue
            item_id = normalize_workshop_id(token)
            if item_id not in seen:
                seen.add(item_id)
                found.append(item_id)
    return found
