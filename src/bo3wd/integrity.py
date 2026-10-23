"""Safe selection and reporting helpers for SteamCMD Workshop integrity checks."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .identifiers import normalize_workshop_id
from .models import DownloadPaths, WorkshopItem


class IntegrityCheckError(RuntimeError):
    """The requested integrity check cannot be started safely."""


def discover_integrity_targets(
    paths: DownloadPaths,
    jobs: Iterable[dict[str, Any]],
    *,
    selected_id: str | None = None,
) -> list[WorkshopItem]:
    """Select existing installed Workshop folders. Never scans or changes partial-cache files."""
    content_root = Path(paths.content_root)
    try:
        content_root_resolved = content_root.resolve(strict=False)
        if selected_id is not None:
            item_id = normalize_workshop_id(selected_id)
            candidate = content_root / item_id
            if candidate.is_symlink() or not candidate.is_dir():
                raise IntegrityCheckError(
                    f"No installed Workshop content folder exists for {item_id}. "
                    "Resume/download the item first; partial-cache files are not treated as complete."
                )
            if candidate.resolve(strict=False).parent != content_root_resolved:
                raise IntegrityCheckError("The selected Workshop folder resolves outside the content directory.")
            found_ids = [item_id]
        else:
            found_ids = []
            if content_root.is_dir():
                for child in content_root.iterdir():
                    if (
                        child.is_dir()
                        and not child.is_symlink()
                        and child.name.isascii()
                        and child.name.isdigit()
                        and 1 <= len(child.name) <= 20
                        and child.resolve(strict=False).parent == content_root_resolved
                    ):
                        try:
                            item_id = normalize_workshop_id(child.name)
                        except ValueError:
                            continue
                        found_ids.append(item_id)
            found_ids = sorted(set(found_ids))
    except IntegrityCheckError:
        raise
    except OSError as exc:
        raise IntegrityCheckError(f"Cannot inspect the Workshop content directory {content_root}: {exc}") from exc

    if not found_ids:
        raise IntegrityCheckError(
            f"No installed Workshop content folders were found at {content_root}. "
            "Download or recover an item first."
        )

    rows: dict[str, dict[str, Any]] = {}
    for row in jobs:
        raw_id = row.get("workshop_id")
        if raw_id is None:
            continue
        try:
            row_id = normalize_workshop_id(str(raw_id))
        except ValueError:
            continue
        rows[row_id] = row

    result: list[WorkshopItem] = []
    for item_id in found_ids:
        row = rows.get(item_id, {})
        try:
            expected = max(0, int(row.get("expected_bytes") or 0))
        except (TypeError, ValueError, OverflowError):
            expected = 0
        result.append(
            WorkshopItem(
                workshop_id=item_id,
                title=str(row.get("title") or item_id),
                expected_bytes=expected,
                url=str(row.get("url") or f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}"),
            )
        )
    return result


def write_integrity_report(
    data_dir: Path,
    *,
    started_at: float,
    finished_at: float,
    results: list[dict[str, Any]],
) -> Path:
    """Atomically persist a JSON result report without exposing Steam credentials."""
    report_dir = Path(data_dir) / "integrity-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(finished_at, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"integrity-check-{stamp}-{time.time_ns() % 1_000_000:06d}.json"
    completed = sum(str(item.get("status", "")).lower() == "completed" for item in results)
    failed = sum(str(item.get("status", "")).lower() == "failed" for item in results)
    cancelled = sum(str(item.get("status", "")).lower() == "cancelled" for item in results)
    payload = {
        "schema_version": 1,
        "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
        "finished_at": datetime.fromtimestamp(finished_at, tz=timezone.utc).isoformat(),
        "verification": (
            "SteamCMD workshop_download_item validate. This checks against Steam's available Workshop "
            "data and may download/repair installed files. It is not a read-only scan."
        ),
        "summary": {
            "requested": len(results),
            "verified": completed,
            "failed": failed,
            "cancelled": cancelled,
            "not_completed": len(results) - completed - failed - cancelled,
        },
        "results": results,
    }
    fd, temp_name = tempfile.mkstemp(prefix=".integrity-check-", suffix=".tmp", dir=report_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, report_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return report_path
