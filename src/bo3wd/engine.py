"""Resumable, non-destructive Workshop download engine."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

from . import APP_ID
from .identifiers import normalize_workshop_id
from .models import DownloadEvent, DownloadPaths, DownloadPolicy, JobStatus, WorkshopItem
from .process import ProcessRunner
from .steamcmd import build_download_command
from .storage import JobHistory

_SUCCESS = re.compile(r"success\.\s+downloaded item\s+(\d+)\b", re.IGNORECASE)
_AUTH = re.compile(
    r"cached credentials not found|login failure|invalid password|steam guard|"
    r"authenticator code|password\s*:|please enter.{0,40}(?:password|code)|not logged in|two.factor code",
    re.IGNORECASE,
)
_PERMANENT = re.compile(
    r"access denied|not subscribed|no subscription|item not found|file not found|"
    r"invalid workshop item|does not exist|no matching item|permission denied",
    re.IGNORECASE,
)
_TRANSIENT = re.compile(
    r"timeout|timed out|connection (?:reset|closed|refused|lost)|network|"
    r"failed to connect|failed to download|error downloading|server unavailable|"
    r"temporarily unavailable|rate.?limit|http\s*5\d\d|content server",
    re.IGNORECASE,
)


class DownloadFailure(RuntimeError):
    """Download failed permanently or exhausted its retry budget."""


class DownloadCancelled(RuntimeError):
    """User cancelled the active download."""


def tree_size(root: Path) -> int:
    """Sum file sizes under a directory while tolerating concurrent file updates."""
    total = 0
    try:
        if not root.is_dir():
            return 0
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


class DownloadEngine:
    """Serial SteamCMD download engine; never removes content or partial-download files."""

    def __init__(
        self,
        paths: DownloadPaths,
        username: str,
        *,
        policy: DownloadPolicy | None = None,
        validate: bool = True,
        runner: ProcessRunner | None = None,
        history: JobHistory | None = None,
        event_sink: Callable[[DownloadEvent], None] | None = None,
        wait: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        self.paths = paths
        self.username = username.strip()
        self.policy = policy or DownloadPolicy()
        self.validate = validate
        self.runner = runner or ProcessRunner(
            poll_interval=self.policy.poll_interval_seconds,
            tail_lines=self.policy.output_tail_lines,
        )
        self.history = history
        self.event_sink = event_sink
        self._wait = wait or (lambda cancel, seconds: cancel.wait(seconds))

    def _emit(self, item: WorkshopItem, event: DownloadEvent) -> None:
        state_error: str | None = None
        if self.history is not None:
            try:
                self.history.update(event, title=item.title, url=item.url)
            except Exception as exc:
                # Persistence failures should be visible in diagnostics, but must not orphan SteamCMD.
                state_error = f"[STATE WARNING] history update failed: {type(exc).__name__}: {exc}"
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                pass
        if state_error and event.log_path:
            try:
                with Path(event.log_path).open("a", encoding="utf-8") as log:
                    log.write(state_error + "\n")
            except OSError:
                pass

    def _current_bytes(self, item_id: str) -> int:
        return tree_size(self.paths.partial_root / item_id) + tree_size(self.paths.content_root / item_id)

    def _event(
        self,
        item: WorkshopItem,
        status: JobStatus,
        *,
        attempt: int,
        message: str,
        log_path: Path,
        started: float,
        line: str = "",
        bytes_received: int = 0,
        speed_bytes_per_second: float = 0.0,
        eta_seconds: float | None = None,
    ) -> DownloadEvent:
        return DownloadEvent(
            workshop_id=item.workshop_id,
            status=status,
            attempt=attempt,
            max_attempts=self.policy.max_attempts,
            bytes_received=bytes_received,
            expected_bytes=item.expected_bytes,
            message=message,
            log_path=str(log_path),
            line=line,
            elapsed_seconds=max(0.0, time.monotonic() - started),
            speed_bytes_per_second=speed_bytes_per_second,
            eta_seconds=eta_seconds,
            metadata={"title": item.title, "app_id": APP_ID},
        )

    def download(self, item: WorkshopItem | str, cancel_event: threading.Event | None = None) -> DownloadEvent:
        """Download one item, retrying transient failures and retaining all partial data."""
        if isinstance(item, str):
            item = WorkshopItem(workshop_id=normalize_workshop_id(item))
        else:
            item = WorkshopItem(
                workshop_id=normalize_workshop_id(item.workshop_id),
                title=item.title,
                expected_bytes=item.expected_bytes,
                updated_at=item.updated_at,
                url=item.url,
                app_id=item.app_id,
            )
        cancel = cancel_event or threading.Event()
        log_path = self.paths.logs_root / f"{item.workshop_id}.log"
        started = time.monotonic()
        item_id = item.workshop_id

        current_speed = 0.0
        current_eta: float | None = None
        current_sample_bytes = self._current_bytes(item_id)
        current_sample_at = time.monotonic()

        def emit(status: JobStatus, attempt: int, message: str, *, line: str = "", bytes_received: int | None = None) -> DownloadEvent:
            event = self._event(
                item, status, attempt=attempt, message=message, log_path=log_path, started=started,
                line=line, bytes_received=self._current_bytes(item_id) if bytes_received is None else bytes_received,
                speed_bytes_per_second=current_speed, eta_seconds=current_eta,  # noqa: B023
            )
            self._emit(item, event)
            return event

        emit(JobStatus.PREPARING, 0, "Checking SteamCMD and the saved login…")
        if not self.paths.steamcmd.is_file():
            return emit(JobStatus.FAILED, 0, f"SteamCMD executable not found: {self.paths.steamcmd}")
        if not self.username:
            return emit(JobStatus.FAILED, 0, "Steam account name is missing. Log in to SteamCMD manually once, then set the account name.")
        try:
            self.paths.logs_root.mkdir(parents=True, exist_ok=True)
            self.paths.content_root.mkdir(parents=True, exist_ok=True)
            self.paths.partial_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return emit(JobStatus.FAILED, 0, f"Cannot prepare Workshop directories: {exc}")

        stagnant_attempts = 0
        last_observed_size = self._current_bytes(item_id)
        for attempt in range(1, self.policy.max_attempts + 1):
            if cancel.is_set():
                return emit(JobStatus.CANCELLED, attempt - 1, "Cancelled. Partial data was preserved.")
            marker_seen = False
            auth_seen = False
            permanent_seen = False
            current_sample_bytes = self._current_bytes(item_id)
            current_sample_at = time.monotonic()
            current_speed = 0.0
            current_eta = None
            recent_lines: list[str] = []
            last_ui_telemetry_at = 0.0
            last_ui_line_at = 0.0
            last_line_byte_count = current_sample_bytes

            def update_progress_sample() -> None:
                nonlocal current_speed, current_eta, current_sample_bytes, current_sample_at, last_line_byte_count
                now = time.monotonic()
                elapsed_sample = now - current_sample_at
                # Recursive tree scans can be expensive for Workshop items with thousands of files.
                # Sample at most every 1.5s, not for every line SteamCMD prints.
                if elapsed_sample < 1.5:
                    return
                observed_bytes = self._current_bytes(item_id)
                delta_bytes = max(0, observed_bytes - current_sample_bytes)
                if delta_bytes > 0 and elapsed_sample > 0:
                    measured = delta_bytes / elapsed_sample
                    current_speed = measured if current_speed <= 0 else current_speed * 0.45 + measured * 0.55
                current_sample_bytes = observed_bytes
                last_line_byte_count = observed_bytes
                current_sample_at = now
                if item.expected_bytes > 0 and observed_bytes >= item.expected_bytes:
                    current_eta = 0.0
                elif item.expected_bytes > 0 and current_speed > 0:
                    current_eta = (item.expected_bytes - observed_bytes) / current_speed
                else:
                    current_eta = None

            def sample_progress() -> None:
                nonlocal last_ui_telemetry_at
                update_progress_sample()
                now = time.monotonic()
                if self.event_sink is not None and now - last_ui_telemetry_at >= 0.75:
                    last_ui_telemetry_at = now
                    telemetry = self._event(
                        item, JobStatus.DOWNLOADING, attempt=attempt, message="Transfer telemetry",  # noqa: B023
                        log_path=log_path, started=started, bytes_received=last_line_byte_count,  # noqa: B023
                        speed_bytes_per_second=current_speed, eta_seconds=current_eta,
                    )
                    try:
                        self.event_sink(telemetry)
                    except Exception:
                        pass

            emit(
                JobStatus.DOWNLOADING,
                attempt,
                f"Starting SteamCMD attempt {attempt}/{self.policy.max_attempts}; partial data will be retained.",
            )

            def handle_line(line: str) -> None:
                nonlocal marker_seen, auth_seen, permanent_seen, last_ui_line_at
                recent_lines.append(line)  # noqa: B023
                if len(recent_lines) > 80:  # noqa: B023
                    del recent_lines[:20]  # noqa: B023
                if any(int(match) == int(item_id) for match in _SUCCESS.findall(line)):
                    marker_seen = True
                if _AUTH.search(line):
                    auth_seen = True
                if _PERMANENT.search(line):
                    permanent_seen = True
                update_progress_sample()
                now = time.monotonic()
                if self.event_sink is not None and now - last_ui_line_at >= 0.15:
                    last_ui_line_at = now
                    event = self._event(
                        item, JobStatus.DOWNLOADING, attempt=attempt, message=line[:500],  # noqa: B023
                        log_path=log_path, started=started, line=line,
                        bytes_received=last_line_byte_count,
                        speed_bytes_per_second=current_speed, eta_seconds=current_eta,
                    )
                    try:
                        self.event_sink(event)
                    except Exception:
                        pass


            command = build_download_command(
                self.paths.steamcmd, self.username, item_id, validate=self.validate
            )
            try:
                result = self.runner.run(
                    command,
                    cwd=self.paths.steamcmd.parent,
                    log_path=log_path,
                    timeout_seconds=self.policy.attempt_timeout_seconds,
                    cancel_event=cancel,
                    redact=(self.username,),
                    on_line=handle_line,
                    on_tick=sample_progress,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                return emit(JobStatus.FAILED, attempt, f"Could not execute SteamCMD: {exc}")

            current_size = self._current_bytes(item_id)
            if result.cancelled or cancel.is_set():
                return emit(JobStatus.CANCELLED, attempt, "Cancelled. Partial data was preserved.", bytes_received=current_size)
            if auth_seen:
                return emit(
                    JobStatus.FAILED,
                    attempt,
                    f"SteamCMD needs interactive authentication. Run `{self.paths.steamcmd} +login <account> +quit` manually once, then retry. Existing partial data is preserved.",
                    bytes_received=current_size,
                )

            content_dir = self.paths.content_root / item_id
            has_content = content_dir.is_dir() and any(p.is_file() for p in content_dir.rglob("*"))
            if marker_seen and result.returncode == 0 and has_content:
                return emit(
                    JobStatus.COMPLETED,
                    attempt,
                    f"Download complete and verified: {content_dir}",
                    bytes_received=current_size,
                )

            output = "\n".join(recent_lines) + "\n" + result.output_tail
            if permanent_seen or _PERMANENT.search(output):
                detail = next((line for line in reversed(recent_lines) if _PERMANENT.search(line)), "Steam rejected this item.")
                return emit(JobStatus.FAILED, attempt, f"Permanent Workshop error: {detail[:500]}", bytes_received=current_size)

            grew = current_size > last_observed_size
            if grew:
                stagnant_attempts = 0
            else:
                stagnant_attempts += 1
            last_observed_size = max(last_observed_size, current_size)

            if stagnant_attempts >= self.policy.max_stagnant_attempts:
                return emit(
                    JobStatus.FAILED,
                    attempt,
                    f"No additional bytes were observed for {stagnant_attempts} consecutive attempts. Stopped instead of looping forever. Partial data remains in {self.paths.partial_root / item_id}. Log: {log_path}",
                    bytes_received=current_size,
                )

            if attempt >= self.policy.max_attempts:
                break

            if result.timed_out:
                reason = f"SteamCMD exceeded the {self.policy.attempt_timeout_seconds / 60:g}-minute watchdog."
            elif _TRANSIENT.search(output) or result.returncode != 0:
                reason = f"SteamCMD failed transiently (exit {result.returncode})."
            elif marker_seen and not has_content:
                reason = "SteamCMD reported success but the content folder is empty."
            else:
                reason = "SteamCMD exited without a verified success marker."
            backoff = min(
                self.policy.max_backoff_seconds,
                self.policy.initial_backoff_seconds * (2 ** min(attempt - 1, 20)),
            )
            message = f"{reason} {'Partial data grew; resuming.' if grew else 'No new bytes yet; retrying carefully.'}"
            emit(JobStatus.RETRY_WAIT, attempt, f"{message} Retry in {backoff:g}s; no files will be deleted.", bytes_received=current_size)
            if backoff and self._wait(cancel, backoff):
                return emit(JobStatus.CANCELLED, attempt, "Cancelled during retry wait. Partial data was preserved.", bytes_received=current_size)

        return emit(
            JobStatus.FAILED,
            self.policy.max_attempts,
            f"Exhausted {self.policy.max_attempts} attempts. Partial data was preserved. Inspect {log_path}.",
            bytes_received=self._current_bytes(item_id),
        )
