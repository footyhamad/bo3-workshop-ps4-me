"""Steam Workshop metadata with a public-page fallback when API size is unavailable."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from . import APP_ID
from .identifiers import normalize_workshop_id
from .models import WorkshopItem

_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"
_USER_AGENT = "Mozilla/5.0 BO3WorkshopDownloader/2.0.8"


class MetadataError(RuntimeError):
    """Steam did not return usable metadata for the requested item."""


def parse_workshop_size(value: str) -> int:
    """Parse a Workshop page's human-readable size; 0 means the total remains unknown."""
    match = re.search(
        r"([\d][\d.,\s]*?)\s*(bytes?|b|kib|kb|mib|mb|gib|gb|tib|tb)\b",
        value, re.IGNORECASE,
    )
    if not match:
        return 0
    number = re.sub(r"\s+", "", match.group(1))
    unit = match.group(2).lower()
    if "." in number and "," in number:
        decimal = "," if number.rfind(",") > number.rfind(".") else "."
        grouping = "." if decimal == "," else ","
        number = number.replace(grouping, "").replace(decimal, ".")
    elif number.count(".") > 1 or number.count(",") > 1:
        separator = "." if "." in number else ","
        pieces = number.split(separator)
        if all(len(piece) == 3 for piece in pieces[1:]):
            number = "".join(pieces)
        else:
            number = separator.join(pieces[:-1]) + "." + pieces[-1]
    else:
        number = number.replace(",", ".")
    try:
        amount = float(number)
    except ValueError:
        return 0
    if amount <= 0:
        return 0
    if unit in {"byte", "bytes", "b"}:
        multiplier = 1
    elif unit in {"kb", "kib"}:
        multiplier = 1024
    elif unit in {"mb", "mib"}:
        multiplier = 1024**2
    elif unit in {"gb", "gib"}:
        multiplier = 1024**3
    else:
        multiplier = 1024**4
    return max(0, int(amount * multiplier))


class _WorkshopPageParser(HTMLParser):
    """Extract public Workshop File Size stats and page title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.left_values: list[str] = []
        self.right_values: list[str] = []
        self._active_kind: str | None = None
        self._active_div_depth = 0
        self._active_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "meta":
            identity = attributes.get("property", "").lower() or attributes.get("name", "").lower()
            if identity in {"og:title", "twitter:title"} and attributes.get("content") and not self.title:
                self.title = attributes["content"].strip()
        if tag.lower() != "div":
            return
        if self._active_kind is not None:
            self._active_div_depth += 1
            return
        classes = set(attributes.get("class", "").split())
        for class_name, kind in (
            ("detailsStatLeft", "left"),
            ("detailsStatRight", "right"),
            ("workshopItemTitle", "title"),
        ):
            if class_name in classes:
                self._active_kind = kind
                self._active_div_depth = 1
                self._active_parts = []
                break

    def handle_data(self, data: str) -> None:
        if self._active_kind is not None:
            self._active_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "div" or self._active_kind is None:
            return
        self._active_div_depth -= 1
        if self._active_div_depth > 0:
            return
        value = " ".join(" ".join(self._active_parts).split())
        if self._active_kind == "left":
            self.left_values.append(value)
        elif self._active_kind == "right":
            self.right_values.append(value)
        elif self._active_kind == "title" and not self.title and value:
            self.title = value
        self._active_kind = None
        self._active_parts = []

    def size_bytes(self) -> int:
        for index, label in enumerate(self.left_values):
            if label.strip().casefold() == "file size" and index < len(self.right_values):
                parsed = parse_workshop_size(self.right_values[index])
                if parsed:
                    return parsed
        return 0


@dataclass(frozen=True, slots=True)
class WorkshopMetadataClient:
    timeout_seconds: float = 12.0

    def fetch_page(self, workshop_id: str) -> WorkshopItem | None:
        """Try the public Workshop page when the metadata API omits a usable size."""
        item_id = normalize_workshop_id(workshop_id)
        page_url = _PAGE.format(workshop_id=item_id)
        request = urllib.request.Request(
            page_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (OSError, TimeoutError, urllib.error.URLError, UnicodeDecodeError):
            return None
        parser = _WorkshopPageParser()
        try:
            parser.feed(html)
            parser.close()
        except (ValueError, AssertionError):
            return None
        title = parser.title.strip()
        for suffix in (" :: Steam Community", " :: Steam Workshop"):
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
        size_bytes = parser.size_bytes()
        if not title and not size_bytes:
            return None
        return WorkshopItem(
            workshop_id=item_id, title=title or item_id,
            expected_bytes=size_bytes, url=page_url, app_id=APP_ID,
        )

    def fetch_many(self, workshop_ids: list[str]) -> dict[str, WorkshopItem]:
        ids: list[str] = []
        seen: set[str] = set()
        for raw_id in workshop_ids:
            try:
                item_id = normalize_workshop_id(raw_id)
            except ValueError as exc:
                raise MetadataError(f"Invalid Workshop ID in metadata request: {raw_id!r}") from exc
            if item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
        if not ids:
            return {}
        if len(ids) > 100:
            raise ValueError("Steam's public details endpoint accepts at most 100 IDs per request")

        form: dict[str, str] = {"itemcount": str(len(ids))}
        form.update({f"publishedfileids[{index}]": item_id for index, item_id in enumerate(ids)})
        request = urllib.request.Request(
            _API,
            data=urllib.parse.urlencode(form).encode("ascii"),
            headers={"User-Agent": _USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        result: dict[str, WorkshopItem] = {}
        api_error: Exception | None = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
                raise MetadataError("Steam returned a malformed Workshop metadata response")
            entries = payload["response"].get("publishedfiledetails", [])
            if not isinstance(entries, list):
                raise MetadataError("Steam returned a malformed publishedfiledetails list")
            requested = set(ids)
            do_not_page_fallback: set[str] = set()
            for item in entries:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("publishedfileid", ""))
                if item_id not in requested:
                    continue
                # Do not re-query items explicitly identified as invalid or belonging to another game.
                if item.get("result") != 1:
                    do_not_page_fallback.add(item_id)
                    continue
                app_id = str(item.get("consumer_app_id", ""))
                if app_id and app_id != APP_ID:
                    do_not_page_fallback.add(item_id)
                    continue
                try:
                    size_bytes = max(0, int(item.get("file_size") or 0))
                except (TypeError, ValueError, OverflowError):
                    size_bytes = 0
                try:
                    updated_at = int(item["time_updated"]) if item.get("time_updated") else None
                except (TypeError, ValueError, OverflowError):
                    updated_at = None
                result[item_id] = WorkshopItem(
                    workshop_id=item_id, title=str(item.get("title") or item_id),
                    expected_bytes=size_bytes, updated_at=updated_at,
                    url=_PAGE.format(workshop_id=item_id), app_id=APP_ID,
                )
        except (
            OSError, TimeoutError, UnicodeDecodeError, urllib.error.URLError,
            json.JSONDecodeError, MetadataError, TypeError,
        ) as exc:
            api_error = exc

        # Unknown size limits only percentage/ETA. It must never prevent starting a download.
        fallback_ids = [
            item_id for item_id in ids
            if item_id not in do_not_page_fallback
            and (item_id not in result or result[item_id].expected_bytes <= 0)
        ]
        for item_id in fallback_ids:
            page_item = self.fetch_page(item_id)
            if page_item is None:
                continue
            existing = result.get(item_id)
            if existing is None:
                result[item_id] = page_item
            else:
                result[item_id] = WorkshopItem(
                    workshop_id=item_id,
                    title=existing.title if existing.title and existing.title != item_id else page_item.title,
                    expected_bytes=existing.expected_bytes or page_item.expected_bytes,
                    updated_at=existing.updated_at, url=existing.url or page_item.url, app_id=APP_ID,
                )
        if result:
            return result
        if api_error is not None:
            raise MetadataError(
                f"Steam Workshop metadata request failed ({api_error}); the public page fallback "
                f"did not return usable metadata for {len(ids)} item(s)."
            ) from api_error
        return {}
