from bo3wd.models import WorkshopItem
from bo3wd.preflight import assess_disk_space


def test_known_sizes_and_unknown_items_are_reported_without_blocking(tmp_path, monkeypatch):
    fake_usage = type("Usage", (), {"total": 50_000, "used": 10_000, "free": 40_000})()
    monkeypatch.setattr("bo3wd.preflight.shutil.disk_usage", lambda _path: fake_usage)
    assessment = assess_disk_space(
        tmp_path / "steamcmd.exe",
        [WorkshopItem("123", expected_bytes=20_000), WorkshopItem("456", expected_bytes=0)],
        safety_margin_bytes=5_000,
    )
    assert assessment.available_bytes == 40_000
    assert assessment.expected_bytes == 20_000
    assert assessment.unknown_items == 1
    assert assessment.low_space is False


def test_low_disk_space_detects_known_size_plus_safety_margin(tmp_path, monkeypatch):
    fake_usage = type("Usage", (), {"total": 10_000, "used": 7_000, "free": 3_000})()
    monkeypatch.setattr("bo3wd.preflight.shutil.disk_usage", lambda _path: fake_usage)
    assessment = assess_disk_space(
        tmp_path / "steamcmd.exe", [{"expected_bytes": 2_000}], safety_margin_bytes=2_000,
    )
    assert assessment.low_space is True
    assert assessment.unknown_items == 0


def test_unreadable_disk_usage_returns_advisory_error_not_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bo3wd.preflight.shutil.disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("drive unavailable")),
    )
    result = assess_disk_space(tmp_path / "steamcmd.exe", [])
    assert result.available_bytes is None
    assert "drive unavailable" in result.error
