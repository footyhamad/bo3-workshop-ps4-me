"""Immutable models and validated configuration for the downloader core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .identifiers import normalize_workshop_id


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class WorkshopItem:
    workshop_id: str
    title: str = ""
    expected_bytes: int = 0
    updated_at: int | None = None
    url: str = ""
    app_id: str = "311210"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workshop_id", normalize_workshop_id(self.workshop_id))
        if self.expected_bytes < 0:
            raise ValueError("expected_bytes cannot be negative")
        if self.app_id != "311210":
            raise ValueError("This downloader only targets Black Ops III Workshop (AppID 311210).")


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    """Retry policy with bounded attempts, watchdog and stagnant-progress guard."""

    max_attempts: int = 30
    attempt_timeout_seconds: float = 15 * 60
    initial_backoff_seconds: float = 3.0
    max_backoff_seconds: float = 45.0
    max_stagnant_attempts: int = 8
    output_tail_lines: int = 80
    poll_interval_seconds: float = 0.15

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be > 0")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must be >= initial_backoff_seconds")
        if self.max_stagnant_attempts < 1:
            raise ValueError("max_stagnant_attempts must be >= 1")
        if self.output_tail_lines < 1:
            raise ValueError("output_tail_lines must be >= 1")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class DownloadPaths:
    steamcmd: Path
    content_root: Path
    partial_root: Path
    logs_root: Path

    @classmethod
    def from_steamcmd(cls, steamcmd: Path, data_root: Path) -> DownloadPaths:
        executable = Path(steamcmd).expanduser().resolve()
        base = executable.parent
        root = Path(data_root).expanduser().resolve()
        return cls(
            steamcmd=executable,
            content_root=base / "steamapps" / "workshop" / "content" / "311210",
            partial_root=base / "steamapps" / "workshop" / "downloads" / "311210",
            logs_root=root / "logs",
        )


@dataclass(frozen=True, slots=True)
class DownloadEvent:
    workshop_id: str
    status: JobStatus
    attempt: int = 0
    max_attempts: int = 0
    bytes_received: int = 0
    expected_bytes: int = 0
    message: str = ""
    log_path: str = ""
    line: str = ""
    elapsed_seconds: float = 0.0
    speed_bytes_per_second: float = 0.0
    eta_seconds: float | None = None
    metadata: dict[str, str | int] = field(default_factory=dict)

    @property
    def estimated_progress(self) -> float | None:
        if self.expected_bytes <= 0:
            return None
        return min(1.0, self.bytes_received / self.expected_bytes)
