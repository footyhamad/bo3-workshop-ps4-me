import time

from bo3wd.engine import DownloadEngine
from bo3wd.models import DownloadPaths, DownloadPolicy, JobStatus
from bo3wd.process import ProcessResult


class Runner:
    def __init__(self):
        self.calls = 0

    def run(self, command, **kwargs):
        self.calls += 1
        item_id = command[command.index("+workshop_download_item") + 2]
        log_path = kwargs["log_path"]
        if self.calls == 1:
            line = f"ERROR! Timeout downloading item {item_id}"
            kwargs["on_line"](line)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(line + "\n", encoding="utf-8")
            return ProcessResult(1, line, timed_out=True)
        folder = kwargs["cwd"] / "steamapps/workshop/content/311210" / item_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "file.dat").write_bytes(b"ok")
        line = f"Success. Downloaded item {item_id} to content folder"
        kwargs["on_line"](line)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(line + "\n")
        return ProcessResult(0, line)


def test_engine_retries_after_timeout_without_deleting_partial(tmp_path):
    exe = tmp_path / "steamcmd.exe"
    exe.write_bytes(b"test")
    paths = DownloadPaths(exe, exe.parent / "steamapps/workshop/content/311210",
        exe.parent / "steamapps/workshop/downloads/311210", tmp_path / "logs")
    part = paths.partial_root / "123" / "chunk.dat"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"keep")
    runner = Runner()
    engine = DownloadEngine(paths, "account", policy=DownloadPolicy(max_attempts=3,
        attempt_timeout_seconds=1, initial_backoff_seconds=0, max_backoff_seconds=0,
        max_stagnant_attempts=3), runner=runner)
    result = engine.download("123")
    assert result.status == JobStatus.COMPLETED
    assert result.attempt == 2
    assert runner.calls == 2
    assert part.read_bytes() == b"keep"


def test_live_telemetry_reports_partial_file_bytes_before_steamcmd_finishes(tmp_path):
    exe = tmp_path / "steamcmd.exe"
    exe.write_bytes(b"test")
    paths = DownloadPaths.from_steamcmd(exe, tmp_path / "appdata")
    events = []

    class QuietProgressRunner:
        def run(self, command, **kwargs):
            item_id = command[command.index("+workshop_download_item") + 2]
            partial = kwargs["cwd"] / "steamapps/workshop/downloads/311210" / item_id / "chunk.bin"
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(b"partial download bytes")
            # Simulate SteamCMD doing disk I/O without printing progress lines.
            time.sleep(0.85)
            kwargs["on_tick"]()

            content = kwargs["cwd"] / "steamapps/workshop/content/311210" / item_id
            content.mkdir(parents=True, exist_ok=True)
            (content / "file.dat").write_bytes(b"complete")
            line = f"Success. Downloaded item {item_id} to content folder"
            kwargs["on_line"](line)
            return ProcessResult(0, line)

    engine = DownloadEngine(
        paths,
        "account",
        policy=DownloadPolicy(
            max_attempts=1,
            attempt_timeout_seconds=10,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            max_stagnant_attempts=1,
        ),
        runner=QuietProgressRunner(),
        event_sink=events.append,
    )
    result = engine.download("3296316642")

    assert result.status == JobStatus.COMPLETED
    telemetry = [
        event for event in events
        if getattr(event, "message", "") == "Transfer telemetry"
    ]
    assert telemetry, "the UI needs periodic progress events even when SteamCMD prints no lines"
    assert telemetry[0].bytes_received >= len(b"partial download bytes")
