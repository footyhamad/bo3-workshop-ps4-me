"""Advisory disk-space checks that never block unknown-size Workshop items."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DiskSpaceAssessment:
    path: Path
    available_bytes: int | None
    expected_bytes: int
    unknown_items: int
    safety_margin_bytes: int
    error: str = ""

    @property
    def low_space(self) -> bool:
        if self.available_bytes is None:
            return True
        return self.available_bytes < self.expected_bytes + self.safety_margin_bytes


def assess_disk_space(
    steamcmd_path: Path,
    items: Iterable[Any],
    *,
    safety_margin_bytes: int = 512 * _MIB,
) -> DiskSpaceAssessment:
    """Estimate required space from known sizes; unknown-size items stay downloadable."""
    path = Path(steamcmd_path).expanduser()
    try:
        path = path.resolve(strict=False)
        drive_path = path.parent if path.suffix else path
        usage = shutil.disk_usage(drive_path)
    except (OSError, ValueError) as exc:
        return DiskSpaceAssessment(path, None, 0, 0, max(0, safety_margin_bytes), str(exc))
    expected_total = 0
    unknown = 0
    for item in items:
        raw = item.get("expected_bytes", 0) if isinstance(item, dict) else getattr(item, "expected_bytes", 0)
        try:
            expected = max(0, int(raw or 0))
        except (TypeError, ValueError, OverflowError):
            expected = 0
        if expected <= 0:
            unknown += 1
        else:
            expected_total += expected
    return DiskSpaceAssessment(
        path=drive_path,
        available_bytes=max(0, int(usage.free)),
        expected_bytes=expected_total,
        unknown_items=unknown,
        safety_margin_bytes=max(0, int(safety_margin_bytes)),
    )
