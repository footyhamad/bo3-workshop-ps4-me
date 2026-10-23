import io
import subprocess

import pytest

from workshop_downloader import DownloadError, DownloadPolicy, SteamWorkshopDownloader


class FakeProcess:
    def __init__(self, output="", returncode=0, stuck=False):
        self.stdout = io.StringIO(output)
        self._returncode = None if stuck else returncode
        self.kill_count = 0

    def poll(self):
        return self._returncode

    def kill(self):
        self.kill_count += 1
        self._returncode = -9

    def wait(self, timeout=None):
        if self._returncode is None:
            raise subprocess.TimeoutExpired("fake-steamcmd", timeout)
        return self._returncode


def make_downloader(tmp_path, scripts, *, policy=None, sleep=None):
    steamcmd = tmp_path / "steamcmd.exe"
    steamcmd.write_bytes(b"synthetic executable marker")
    content = tmp_path / "steamapps" / "workshop" / "content" / "311210"
    partial = tmp_path / "steamapps" / "workshop" / "downloads" / "311210"
    log = tmp_path / "download.log"
    calls = []
    emitted = []
    sleep_calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        spec = scripts.pop(0)
        item_id = command[command.index("+workshop_download_item") + 2]
        if spec.get("partial_bytes") is not None:
            folder = partial / item_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "chunk.tmp").write_bytes(b"x" * spec["partial_bytes"])
        if spec.get("map_file"):
            folder = content / item_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "zm_test.ff").write_bytes(b"valid-enough-for-unit-test")
        return FakeProcess(
            output=spec.get("output", ""),
            returncode=spec.get("returncode", 0),
            stuck=spec.get("stuck", False),
        )

    downloader = SteamWorkshopDownloader(
        steamcmd=steamcmd,
        username="test-account",
        app_id="311210",
        content_root=content,
        partial_root=partial,
        log_path=log,
        policy=policy or DownloadPolicy(
            max_attempts=5,
            attempt_timeout_seconds=2,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            max_stagnant_attempts=4,
        ),
        popen=fake_popen,
        sleep=(sleep or (lambda seconds: sleep_calls.append(seconds))),
        emit=emitted.append,
    )
    return downloader, calls, emitted, sleep_calls, content, partial, log


def test_success_requires_marker_and_fastfile(tmp_path):
    scripts = [{"output": "Success. Downloaded item 123 to content folder\n", "map_file": True}]
    d, calls, emitted, _, content, _, log = make_downloader(tmp_path, scripts)

    assert d.download("123") == content / "123"
    assert len(calls) == 1
    assert calls[0][calls[0].index("+workshop_download_item") + 1] == "311210"
    assert "validate" in calls[0]
    assert "test-account" not in log.read_text(encoding="utf-8")
    assert any("Download verified" in line for line in emitted)


def test_success_marker_without_fastfile_is_rejected(tmp_path):
    d, calls, _, _, _, _, _ = make_downloader(
        tmp_path, [{"output": "Success. Downloaded item 123\n"}]
    )
    with pytest.raises(DownloadError, match="no .ff map fastfile"):
        d.download("123")
    assert len(calls) == 1


def test_internal_timeout_retries_and_preserves_partial_data(tmp_path):
    scripts = [
        {"output": "ERROR! Timeout downloading item 123\n", "returncode": 1, "partial_bytes": 1024},
        {"output": "Success. Downloaded item 123\n", "map_file": True},
    ]
    d, calls, emitted, _, _, partial, log = make_downloader(tmp_path, scripts)

    result = d.download("123")
    assert result.name == "123"
    assert len(calls) == 2
    assert (partial / "123" / "chunk.tmp").read_bytes() == b"x" * 1024
    assert "retrying to resume" in "\n".join(emitted).lower()
    assert "Timeout downloading item 123" in log.read_text(encoding="utf-8")


def test_external_hang_is_killed_and_retried(tmp_path):
    scripts = [
        {"output": "", "stuck": True},
        {"output": "Success. Downloaded item 123\n", "map_file": True},
    ]
    d, calls, _, _, _, _, _ = make_downloader(
        tmp_path,
        scripts,
        policy=DownloadPolicy(
            max_attempts=3,
            attempt_timeout_seconds=0.02,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            max_stagnant_attempts=3,
        ),
    )
    assert d.download("123").name == "123"
    assert len(calls) == 2


def test_login_prompt_stops_without_retries(tmp_path):
    scripts = [{"output": "Cached credentials not found. Please enter password:\n"}]
    d, calls, _, _, _, partial, _ = make_downloader(tmp_path, scripts)

    with pytest.raises(DownloadError, match="interactive authentication"):
        d.download("123")
    assert len(calls) == 1
    assert not (partial / "123").exists()


def test_permanent_error_is_not_retried(tmp_path):
    scripts = [{"output": "ERROR! Access denied for this item\n", "returncode": 1}]
    d, calls, _, _, _, _, _ = make_downloader(tmp_path, scripts)

    with pytest.raises(DownloadError, match="Access denied"):
        d.download("123")
    assert len(calls) == 1


def test_stagnant_timeout_stops_and_keeps_partial_bytes(tmp_path):
    scripts = [
        {"output": "ERROR! Timeout downloading item 123\n", "returncode": 1, "partial_bytes": 512},
        {"output": "ERROR! Timeout downloading item 123\n", "returncode": 1},
        {"output": "ERROR! Timeout downloading item 123\n", "returncode": 1},
    ]
    d, calls, _, _, _, partial, _ = make_downloader(
        tmp_path,
        scripts,
        policy=DownloadPolicy(
            max_attempts=8,
            attempt_timeout_seconds=2,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            max_stagnant_attempts=2,
        ),
    )
    with pytest.raises(DownloadError, match="failed to increase partial data"):
        d.download("123")
    assert len(calls) == 2
    assert (partial / "123" / "chunk.tmp").read_bytes() == b"x" * 512


@pytest.mark.parametrize("item_id", ["", "../123", "-1", "abc", "1/2", "123456789012345678901"])
def test_invalid_workshop_ids_are_rejected_before_running_steamcmd(tmp_path, item_id):
    d, calls, _, _, _, _, _ = make_downloader(tmp_path, [])
    with pytest.raises(DownloadError, match="invalid Workshop item ID"):
        d.download(item_id)
    assert calls == []


def test_empty_account_is_rejected_without_process_launch(tmp_path):
    d, calls, _, _, _, _, _ = make_downloader(tmp_path, [])
    d.username = ""
    with pytest.raises(DownloadError, match="no saved SteamCMD account"):
        d.download("123")
    assert calls == []


def test_missing_steamcmd_path_is_a_clear_error(tmp_path):
    d, calls, _, _, _, _, _ = make_downloader(tmp_path, [])
    d.steamcmd = tmp_path / "missing.exe"
    with pytest.raises(DownloadError, match="SteamCMD executable not found"):
        d.download("123")
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"attempt_timeout_seconds": 0},
        {"initial_backoff_seconds": -1},
        {"initial_backoff_seconds": 5, "max_backoff_seconds": 4},
        {"max_stagnant_attempts": 0},
    ],
)
def test_invalid_retry_policy_fails_fast(kwargs):
    with pytest.raises(ValueError):
        DownloadPolicy(**kwargs)
