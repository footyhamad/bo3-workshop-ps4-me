import pytest

from bo3wd.models import DownloadEvent, JobStatus, WorkshopItem
from bo3wd.storage import JobHistory, SettingsStore


def test_settings_defaults_and_atomic_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    initial = store.load()
    assert initial["max_attempts"] == 30
    assert initial["validate_downloads"] is True
    initial["steamcmd"] = "B:/scmd/steamcmd.exe"
    store.save(initial)
    assert store.load()["steamcmd"] == "B:/scmd/steamcmd.exe"
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_settings_raise_clear_error(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        SettingsStore(path).load()


def test_history_adds_updates_lists_and_requeues(tmp_path):
    history = JobHistory(tmp_path / "history.sqlite3")
    item = WorkshopItem("3296316642", title="Death Spiral", expected_bytes=1024)
    log = tmp_path / "logs" / "3296316642.log"
    history.ensure_item(item, log)
    row = history.get(item.workshop_id)
    assert row["status"] == JobStatus.QUEUED.value
    assert row["title"] == "Death Spiral"
    assert row["expected_bytes"] == 1024
    assert history.pending_items()[0]["workshop_id"] == item.workshop_id

    history.update(DownloadEvent(item.workshop_id, JobStatus.FAILED, attempt=3, message="network error", log_path=str(log)), title=item.title, url=item.url)
    assert history.get(item.workshop_id)["status"] == JobStatus.FAILED.value
    assert history.pending_items() == []
    assert history.requeue(item.workshop_id) is True
    assert history.get(item.workshop_id)["status"] == JobStatus.QUEUED.value


def test_active_job_cannot_be_requeued_or_deleted_by_clear_finished(tmp_path):
    history = JobHistory(tmp_path / "history.sqlite3")
    item = WorkshopItem("123", title="Example")
    history.ensure_item(item)
    history.update(DownloadEvent("123", JobStatus.DOWNLOADING, attempt=1), title="Example")
    assert history.requeue("123") is False
    history.update(DownloadEvent("123", JobStatus.RETRY_WAIT, attempt=1), title="Example")
    assert history.requeue("123") is False
    assert history.clear_finished() == 0
    assert history.get("123")["status"] == JobStatus.RETRY_WAIT.value


def test_finished_cleanup_does_not_touch_download_directories(tmp_path):
    history = JobHistory(tmp_path / "state" / "history.sqlite3")
    history.ensure_item(WorkshopItem("123"))
    history.update(DownloadEvent("123", JobStatus.COMPLETED, message="done"))
    downloaded_file = tmp_path / "steamapps" / "workshop" / "content" / "123" / "a.dat"
    downloaded_file.parent.mkdir(parents=True)
    downloaded_file.write_bytes(b"keep")
    assert history.clear_finished() == 1
    assert downloaded_file.read_bytes() == b"keep"
    assert history.get("123") is None


def test_recover_interrupted_jobs_requeues_without_touching_partial_files(tmp_path):
    history = JobHistory(tmp_path / "state" / "history.sqlite3")
    logs = tmp_path / "logs"
    for item_id, status in (("101", JobStatus.PREPARING), ("102", JobStatus.DOWNLOADING), ("103", JobStatus.RETRY_WAIT)):
        history.ensure_item(WorkshopItem(item_id, title=f"Item {item_id}"), logs / f"{item_id}.log")
        history.update(DownloadEvent(item_id, status, attempt=2, message="interrupted"))

    steam_partial = tmp_path / "steamapps" / "workshop" / "downloads" / "311210" / "102" / "part.bin"
    steam_partial.parent.mkdir(parents=True)
    steam_partial.write_bytes(b"keep these bytes")

    assert history.recover_interrupted() == 3
    assert [row["workshop_id"] for row in history.pending_items()] == ["101", "102", "103"]
    for item_id in ("101", "102", "103"):
        row = history.get(item_id)
        assert row["status"] == JobStatus.QUEUED.value
        assert "partial data was preserved" in row["message"].lower()
    assert steam_partial.read_bytes() == b"keep these bytes"


def test_resume_old_jobs_requeues_only_failed_and_cancelled_rows(tmp_path):
    history = JobHistory(tmp_path / "history.sqlite3")
    rows = {
        "201": JobStatus.FAILED,
        "202": JobStatus.CANCELLED,
        "203": JobStatus.COMPLETED,
        "204": JobStatus.QUEUED,
    }
    for item_id, status in rows.items():
        history.ensure_item(WorkshopItem(item_id, title=f"Item {item_id}"))
        if status != JobStatus.QUEUED:
            history.update(DownloadEvent(item_id, status, message="old status"))

    partial = tmp_path / "steamapps" / "workshop" / "downloads" / "311210" / "201" / "part.bin"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"resume these bytes")

    assert history.requeue_retryable() == 2
    assert {row["workshop_id"] for row in history.pending_items()} == {"201", "202", "204"}
    assert history.get("203")["status"] == JobStatus.COMPLETED.value
    assert history.get("201")["status"] == JobStatus.QUEUED.value
    assert partial.read_bytes() == b"resume these bytes"
