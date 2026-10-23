"""Subprocess runner that streams logs, enforces a watchdog and supports cancellation."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    output_tail: str
    timed_out: bool = False
    cancelled: bool = False


def _windows_creation_flags() -> int:
    """Return flags that group the child process without creating a console window."""
    process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return process_group | no_window


class ProcessRunner:
    """Run a child without a shell, stream its output, and bound shutdown time."""

    def __init__(self, *, poll_interval: float = 0.1, tail_lines: int = 80):
        if poll_interval <= 0 or tail_lines < 1:
            raise ValueError("poll_interval must be positive and tail_lines must be >= 1")
        self.poll_interval = poll_interval
        self.tail_lines = tail_lines

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
        redact: Sequence[str] = (),
        on_line: Callable[[str], None] | None = None,
        on_tick: Callable[[], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessResult:
        if not command:
            raise ValueError("command cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        event_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        tail: deque[str] = deque(maxlen=self.tail_lines)
        kwargs: dict[str, object] = {
            "cwd": str(cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": env,
        }
        if os.name == "nt":
            # SteamCMD is a console executable. Without CREATE_NO_WINDOW it opens a
            # separate black console when started from the windowed GUI. stdout and
            # stderr remain piped, so live output still reaches the in-app log/progress.
            kwargs["creationflags"] = _windows_creation_flags()
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = startupinfo
        try:
            proc = subprocess.Popen(list(command), **kwargs)
        except OSError as exc:
            raise RuntimeError(f"Could not start process {command[0]!r}: {exc}") from exc
        if proc.stdout is None:
            proc.kill()
            raise RuntimeError("Process stdout pipe was not created")

        def pump() -> None:
            try:
                for raw_line in proc.stdout:
                    event_queue.put(("line", raw_line))
            except (OSError, ValueError):
                pass
            finally:
                event_queue.put(("eof", None))

        reader = threading.Thread(target=pump, name="bo3wd-process-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        cancelled = False
        eof = False
        killed_at: float | None = None
        process_exited_at: float | None = None
        patterns = tuple(value for value in redact if value and len(value) >= 3)
        last_tick = 0.0
        returncode = -1

        try:
            with log_path.open("a", encoding="utf-8", errors="replace", newline="\n") as log:
                log.write("\n" + "=" * 72 + "\n")
                log.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  process start\n")
                # Never write argv: it contains the Steam username.
                log.write(f"working directory: {cwd}\n")
                log.flush()
                while True:
                    now = time.monotonic()
                    if on_tick is not None and now - last_tick >= 0.75:
                        last_tick = now
                        try:
                            on_tick()
                        except Exception:
                            # Telemetry must never prevent the child from being supervised.
                            pass

                    if proc.poll() is None and not timed_out and not cancelled:
                        if cancel_event.is_set():
                            cancelled = True
                        elif now >= deadline:
                            timed_out = True
                        if timed_out or cancelled:
                            killed_at = now
                            reason = "cancel requested" if cancelled else "attempt timed out"
                            log.write(f"[WATCHDOG] {reason}; terminating child.\n")
                            log.flush()
                            try:
                                proc.kill()
                            except OSError:
                                pass

                    try:
                        kind, payload = event_queue.get(timeout=self.poll_interval)
                    except queue.Empty:
                        kind, payload = "empty", None

                    if kind == "line" and payload is not None:
                        line = payload
                        for secret in patterns:
                            line = line.replace(secret, "<redacted-account>")
                        clean = line.rstrip("\r\n")
                        tail.append(clean)
                        log.write(line)
                        log.flush()
                        if on_line is not None:
                            try:
                                on_line(clean)
                            except Exception as exc:
                                # A UI/progress callback bug must not strand a live SteamCMD process.
                                log.write(f"[CALLBACK ERROR] {type(exc).__name__}: {exc}\n")
                                log.flush()
                    elif kind == "eof":
                        eof = True

                    now = time.monotonic()
                    process_status = proc.poll()
                    if process_status is not None:
                        if process_exited_at is None:
                            process_exited_at = now
                        if eof and event_queue.empty():
                            break
                        # A grandchild can inherit stdout and keep the reader blocked after SteamCMD exits.
                        if event_queue.empty() and now - process_exited_at >= 1.5:
                            log.write("[WATCHDOG] Output pipe did not close after child exit; ending log drain.\n")
                            log.flush()
                            break
                    elif (timed_out or cancelled) and killed_at is not None and now - killed_at >= 2.0:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        killed_at = now

                try:
                    returncode = int(proc.wait(timeout=3))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    returncode = int(proc.wait(timeout=3))
                log.write(f"\n[PROCESS RESULT] exit={returncode}, timed_out={timed_out}, cancelled={cancelled}\n")
                log.flush()
        except Exception:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass
            reader.join(timeout=1)
        return ProcessResult(returncode, "\n".join(tail), timed_out, cancelled)
