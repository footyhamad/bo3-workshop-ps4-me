"""Resilient SteamCMD Workshop downloads with safe resume and durable logs.

SteamCMD can time out after several minutes while retaining chunks that the next
invocation resumes. This module provides bounded, observable retries without
deleting the partial-download directory. It has no third-party dependencies.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


class DownloadError(RuntimeError):
    """A permanent or exhausted Workshop download failure."""


@dataclass(frozen=True)
class DownloadPolicy:
    max_attempts: int = 30
    attempt_timeout_seconds: float = 15 * 60
    initial_backoff_seconds: float = 3.0
    max_backoff_seconds: float = 45.0
    max_stagnant_attempts: int = 8
    log_tail_lines: int = 16

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be positive")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds cannot be lower than initial_backoff_seconds")
        if self.max_stagnant_attempts < 1:
            raise ValueError("max_stagnant_attempts must be at least 1")
        if self.log_tail_lines < 1:
            raise ValueError("log_tail_lines must be at least 1")


@dataclass(frozen=True)
class AttemptResult:
    returncode: int
    output_tail: str
    timed_out: bool
    auth_required: bool
    success_item_ids: tuple[str, ...]


_AUTH_PROMPT = re.compile(
    r"cached credentials not found|invalid password|password\s*:|"
    r"steam guard code|two.factor code|authenticator code|"
    r"please enter.{0,40}(?:password|code)|not logged in",
    re.IGNORECASE,
)
_TRANSIENT_FAILURE = re.compile(
    r"\btimeout\b|timed out|connection (?:reset|closed|refused|lost)|"
    r"failed to download item|error downloading|network|no connection|"
    r"temporarily unavailable|rate.?limit|http\s*5\d\d|server unavailable",
    re.IGNORECASE,
)
_SUCCESS = re.compile(r"success\.\s+downloaded item\s+(\d+)\b", re.IGNORECASE)


class SteamWorkshopDownloader:
    """SteamCMD runner with retries, live logs, bounded timeouts, and safe resume."""

    def __init__(
        self,
        *,
        steamcmd: Path,
        username: str,
        app_id: str,
        content_root: Path,
        partial_root: Path,
        log_path: Path,
        policy: DownloadPolicy | None = None,
        popen: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.steamcmd = Path(steamcmd)
        self.username = str(username).strip()
        self.app_id = str(app_id)
        self.content_root = Path(content_root)
        self.partial_root = Path(partial_root)
        self.log_path = Path(log_path)
        self.policy = policy or DownloadPolicy()
        self._popen = popen or subprocess.Popen
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._emit = emit or print

    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        if not root.is_dir():
            return 0
        try:
            for path in root.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        return total

    @staticmethod
    def _has_fastfile(root: Path) -> bool:
        if not root.is_dir():
            return False
        try:
            return any(path.is_file() for path in root.rglob("*.ff"))
        except OSError:
            return False

    def _command(self, item_id: str) -> list[str]:
        return [
            str(self.steamcmd), "+login", self.username,
            "+workshop_download_item", self.app_id, item_id, "validate", "+quit",
        ]

    def _run_attempt(self, item_id: str, attempt: int) -> AttemptResult:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._command(item_id)
        timed_out = False
        auth_required = False
        output_lines: deque[str] = deque(maxlen=self.policy.log_tail_lines)
        success_item_ids: set[str] = set()
        events: queue.Queue[tuple[str, str | None]] = queue.Queue()

        with self.log_path.open("a", encoding="utf-8", errors="replace", newline="\n") as log:
            log.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"SteamCMD attempt {attempt}/{self.policy.max_attempts}: "
                f"+login <saved-account> +workshop_download_item {self.app_id} {item_id} validate +quit\n"
            )
            log.flush()
            try:
                proc = self._popen(
                    cmd, cwd=str(self.steamcmd.parent), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as exc:
                raise DownloadError(f"cannot start SteamCMD at {self.steamcmd}: {exc}") from exc

            if proc.stdout is None:
                proc.kill()
                raise DownloadError("SteamCMD stdout pipe could not be created")

            def pump_output() -> None:
                try:
                    for raw_line in proc.stdout:
                        events.put(("line", raw_line))
                except (OSError, ValueError):
                    pass
                finally:
                    events.put(("eof", None))

            reader = threading.Thread(target=pump_output, name="steamcmd-output", daemon=True)
            reader.start()
            deadline = self._clock() + self.policy.attempt_timeout_seconds
            killed_at: float | None = None
            eof = False

            while True:
                now = self._clock()
                if not timed_out and proc.poll() is None and now >= deadline:
                    timed_out = True
                    killed_at = now
                    log.write(
                        f"[WATCHDOG] Attempt exceeded {self.policy.attempt_timeout_seconds:.1f}s; "
                        "terminating SteamCMD. Partial files are preserved.\n"
                    )
                    log.flush()
                    proc.kill()

                wait_for = 0.1 if timed_out else min(0.1, max(0.01, deadline - now))
                try:
                    kind, payload = events.get(timeout=wait_for)
                except queue.Empty:
                    kind, payload = "empty", None

                if kind == "line" and payload is not None:
                    line = payload.replace(self.username, "<saved-account>") if self.username else payload
                    output_lines.append(line.rstrip("\r\n"))
                    success_item_ids.update(_SUCCESS.findall(line))
                    log.write(line)
                    log.flush()
                    if _AUTH_PROMPT.search(line):
                        auth_required = True
                        if proc.poll() is None:
                            log.write(
                                "[AUTH] SteamCMD requested interactive authentication. "
                                "Run steamcmd +login <account> +quit manually once.\n"
                            )
                            log.flush()
                            proc.kill()
                            killed_at = self._clock()
                elif kind == "eof":
                    eof = True

                if (
                    (timed_out or auth_required)
                    and proc.poll() is None
                    and killed_at is not None
                    and self._clock() - killed_at >= 2.0
                ):
                    proc.kill()
                    killed_at = self._clock()

                if proc.poll() is not None and eof and events.empty():
                    break

            try:
                returncode = int(proc.wait(timeout=5))
            except subprocess.TimeoutExpired:
                proc.kill()
                returncode = int(proc.wait(timeout=5))
            reader.join(timeout=1)
            log.write(
                f"[ATTEMPT RESULT] exit={returncode}, timed_out={timed_out}, "
                f"auth_required={auth_required}\n"
            )
            log.flush()

        return AttemptResult(
            returncode, "\n".join(output_lines), timed_out, auth_required,
            tuple(sorted(success_item_ids)),
        )

    @staticmethod
    def _failure_detail(output: str, returncode: int, item_id: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        relevant = [
            line for line in lines
            if re.search(r"error|failed|timeout|timed out|denied|invalid|not found", line, re.I)
        ]
        detail = " | ".join((relevant or lines)[-4:])
        if not detail:
            detail = "SteamCMD exited without its Workshop success marker or a useful error message."
        return f"Workshop item {item_id} failed (exit {returncode}): {detail[:1200]}"

    def download(self, item_id: str) -> Path:
        item_id = str(item_id).strip()
        if not re.fullmatch(r"\d{1,20}", item_id):
            raise DownloadError(f"invalid Workshop item ID: {item_id!r}")
        if not self.username:
            raise DownloadError(
                "no saved SteamCMD account was found; run steamcmd +login <account> +quit once by hand"
            )
        if not self.steamcmd.is_file():
            raise DownloadError(f"SteamCMD executable not found: {self.steamcmd}")

        content = self.content_root / item_id
        partial = self.partial_root / item_id
        stagnant_attempts = 0

        for attempt in range(1, self.policy.max_attempts + 1):
            before_bytes = self._tree_size(partial)
            self._emit(
                f"  SteamCMD {attempt}/{self.policy.max_attempts}: "
                f"{before_bytes / 2**20:.1f} MiB of partial data retained"
            )
            result = self._run_attempt(item_id, attempt)

            if result.auth_required:
                raise DownloadError(
                    "SteamCMD needs interactive authentication. Run "
                    "steamcmd +login <account> +quit manually once, then retry; "
                    f"the partial download remains at {partial}."
                )

            success = item_id in result.success_item_ids
            if success:
                if not self._has_fastfile(content):
                    raise DownloadError(
                        f"SteamCMD reported success for {item_id}, but no .ff map fastfile exists in "
                        f"{content}. The item may not be a Zombies map, or SteamCMD's content is incomplete. "
                        f"See {self.log_path}."
                    )
                self._emit(f"  Download verified: Workshop item {item_id} -> {content}")
                return content

            after_bytes = self._tree_size(partial)
            transient = result.timed_out or bool(_TRANSIENT_FAILURE.search(result.output_tail))
            if not transient:
                raise DownloadError(
                    self._failure_detail(result.output_tail, result.returncode, item_id)
                    + f". Full log: {self.log_path}. Partial data was preserved at {partial}."
                )

            if after_bytes > before_bytes:
                stagnant_attempts = 0
                self._emit(
                    f"  Transfer made progress: {after_bytes / 2**20:.1f} MiB partial data; "
                    "retrying to resume the remaining chunks."
                )
            else:
                stagnant_attempts += 1
                self._emit(
                    f"  No new partial bytes in this attempt "
                    f"({stagnant_attempts}/{self.policy.max_stagnant_attempts} stagnant attempts)."
                )
                if stagnant_attempts >= self.policy.max_stagnant_attempts:
                    raise DownloadError(
                        f"SteamCMD failed to increase partial data for {stagnant_attempts} consecutive attempts. "
                        f"Stopped instead of looping forever. Existing files were preserved at {partial}. "
                        f"Inspect {self.log_path}, then retry after fixing the network/login issue."
                    )

            if attempt == self.policy.max_attempts:
                break
            delay = min(
                self.policy.max_backoff_seconds,
                self.policy.initial_backoff_seconds * (2 ** min(attempt - 1, 20)),
            )
            self._emit(
                f"  Retryable SteamCMD failure (exit {result.returncode}); "
                f"waiting {delay:.0f}s before resuming. Log: {self.log_path}"
            )
            self._sleep(delay)

        raise DownloadError(
            f"SteamCMD exhausted {self.policy.max_attempts} attempts for Workshop item {item_id}. "
            f"Partial data was preserved at {partial}. Full log: {self.log_path}."
        )
