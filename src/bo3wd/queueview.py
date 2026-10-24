"""Pure queue filtering helpers shared by the GUI and regression tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ACTIVE_STATUSES = {"preparing", "downloading", "retry_wait"}
ATTENTION_STATUSES = {"failed", "cancelled"}
STATUS_FILTERS = ("All statuses", "Queued", "Active", "Completed", "Needs attention")


def matches_queue_row(row: Mapping[str, Any], query: str = "", status_filter: str = "All statuses") -> bool:
    """Return whether a queue row matches the text and status filters."""
    status = str(row.get("status") or "queued").strip().lower().replace(" ", "_")
    selected = str(status_filter or "All statuses").strip().casefold()
    if selected == "queued" and status != "queued":
        return False
    if selected == "active" and status not in ACTIVE_STATUSES:
        return False
    if selected == "completed" and status != "completed":
        return False
    if selected in {"needs attention", "needs_attention"} and status not in ATTENTION_STATUSES:
        return False
    haystack = " ".join(
        str(row.get(key) or "") for key in ("workshop_id", "title", "status", "message", "url")
    ).casefold()
    terms = [part.casefold() for part in str(query or "").split() if part.strip()]
    return all(term in haystack for term in terms)
