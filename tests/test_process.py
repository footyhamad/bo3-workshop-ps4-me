import sys
import threading
import time

from bo3wd.process import ProcessRunner


def test_runner_streams_output_and_persists_log(tmp_path):
    lines = []
    log = tmp_path / "logs" / "process.log"
    result = ProcessRunner(poll_interval=0.02).run(
        [sys.executable, "-c", "print('alpha'); print('beta')"],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=5,
        cancel_event=threading.Event(),
        on_line=lines.append,
    )
    assert result.returncode == 0
    assert result.timed_out is False
    assert lines == ["alpha", "beta"]
    assert "alpha" in log.read_text(encoding="utf-8")
    assert "[PROCESS RESULT] exit=0" in log.read_text(encoding="utf-8")


def test_runner_drains_large_output_before_closing_log(tmp_path):
    log = tmp_path / "large-output.log"
    lines = []
    code = "for i in range(3000): print(f'line-{i}')"
    result = ProcessRunner(poll_interval=0.02, tail_lines=20).run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=5,
        cancel_event=threading.Event(),
        on_line=lines.append,
    )
    content = log.read_text(encoding="utf-8")
    assert result.returncode == 0
    assert len(lines) == 3000
    assert "line-0" in content and "line-2999" in content
    assert len(result.output_tail.splitlines()) == 20


def test_runner_redacts_account_in_persisted_log(tmp_path):
    log = tmp_path / "process.log"
    result = ProcessRunner().run(
        [sys.executable, "-c", "print('logging in my_account')"],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=5,
        cancel_event=threading.Event(),
        redact=("my_account",),
    )
    assert result.returncode == 0
    contents = log.read_text(encoding="utf-8")
    assert "my_account" not in contents
    assert "<redacted-account>" in contents


def test_runner_kills_process_on_watchdog_timeout(tmp_path):
    log = tmp_path / "timeout.log"
    started = time.monotonic()
    result = ProcessRunner(poll_interval=0.02).run(
        [sys.executable, "-c", "import time; print('begin', flush=True); time.sleep(10)"],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=0.2,
        cancel_event=threading.Event(),
    )
    assert result.timed_out is True
    assert result.returncode != 0
    assert time.monotonic() - started < 4
    assert "[WATCHDOG] attempt timed out" in log.read_text(encoding="utf-8")


def test_callback_exception_does_not_orphan_child(tmp_path):
    log = tmp_path / "callback-error.log"

    def broken_callback(_line):
        raise RuntimeError("UI callback broke")

    result = ProcessRunner(poll_interval=0.02).run(
        [sys.executable, "-c", "print('hello'); print('still alive')"],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=5,
        cancel_event=threading.Event(),
        on_line=broken_callback,
    )
    assert result.returncode == 0
    contents = log.read_text(encoding="utf-8")
    assert "[CALLBACK ERROR] RuntimeError: UI callback broke" in contents
    assert "still alive" in contents


def test_runner_supports_cancellation(tmp_path):
    stop = threading.Event()
    log = tmp_path / "cancel.log"
    threading.Timer(0.2, stop.set).start()
    result = ProcessRunner(poll_interval=0.02).run(
        [sys.executable, "-c", "import time; print('begin', flush=True); time.sleep(10)"],
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=5,
        cancel_event=stop,
    )
    assert result.cancelled is True
    assert result.timed_out is False
    assert "cancel requested" in log.read_text(encoding="utf-8")
