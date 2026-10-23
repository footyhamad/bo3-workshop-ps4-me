"""Public Steam Workshop metadata lookup; no API key or third-party dependency."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import APP_ID
from .identifiers import normalize_workshop_id
from .models import WorkshopItem

_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"


class MetadataError(RuntimeError):
    """Steam did not return usable metadata for the requested item."""


@dataclass(frozen=True, slots=True)
class WorkshopMetadataClient:
    timeout_seconds: float = 12.0

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
            headers={"User-Agent": "BO3WorkshopDownloader/2.0", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except (OSError, TimeoutError, UnicodeDecodeError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise MetadataError(f"Steam Workshop metadata request failed: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise MetadataError("Steam returned a malformed Workshop metadata response")
        entries = payload["response"].get("publishedfiledetails", [])
        if not isinstance(entries, list):
            raise MetadataError("Steam returned a malformed publishedfiledetails list")
        requested = set(ids)
        result: dict[str, WorkshopItem] = {}
        for item in entries:
            if not isinstance(item, dict) or item.get("result") != 1:
                continue
            item_id = str(item.get("publishedfileid", ""))
            if item_id not in requested:
                continue
            app_id = str(item.get("consumer_app_id", ""))
            if app_id and app_id != APP_ID:
                continue
            try:
                expected_bytes = max(0, int(item.get("file_size") or 0))
            except (TypeError, ValueError, OverflowError):
                expected_bytes = 0
            try:
                updated_at = int(item["time_updated"]) if item.get("time_updated") else None
            except (TypeError, ValueError, OverflowError):
                updated_at = None
            result[item_id] = WorkshopItem(
                workshop_id=item_id,
                title=str(item.get("title") or item_id),
                expected_bytes=expected_bytes,
                updated_at=updated_at,
                url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}",
                app_id=APP_ID,
            )
        return result
