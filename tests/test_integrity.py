import json
import os
import time

import pytest

from bo3wd.integrity import IntegrityCheckError, discover_integrity_targets, write_integrity_report
from bo3wd.models import DownloadPaths


def make_paths(tmp_path):
    steamcmd = tmp_path / "steamcmd" / "steamcmd.exe"
    steamcmd.parent.mkdir(parents=True)
    steamcmd.write_bytes(b"placeholder")
    paths = DownloadPaths.from_steamcmd(steamcmd, tmp_path / "appdata")
    paths.content_root.mkdir(parents=True)
    return paths


def test_discovers_installed_content_and_uses_saved_metadata(tmp_path):
    paths = make_paths(tmp_path)
    (paths.content_root / "3296316642").mkdir()
    (paths.content_root / "123456789").mkdir()
    (paths.content_root / "not-an-id").mkdir()

    targets = discover_integrity_targets(
        paths,
        [
            {"workshop_id": "3296316642", "title": "Death Spiral", "expected_bytes": 456, "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=3296316642"},
        ],
    )

    assert [item.workshop_id for item in targets] == ["123456789", "3296316642"]
    death_spiral = next(item for item in targets if item.workshop_id == "3296316642")
    assert death_spiral.title == "Death Spiral"
    assert death_spiral.expected_bytes == 456


def test_selected_integrity_check_only_returns_selected_installed_folder(tmp_path):
    paths = make_paths(tmp_path)
    (paths.content_root / "3296316642").mkdir()
    (paths.content_root / "123456789").mkdir()

    targets = discover_integrity_targets(paths, [], selected_id="3296316642")

    assert len(targets) == 1
    assert targets[0].workshop_id == "3296316642"


def test_refuses_partial_only_or_missing_folder_as_installed_content(tmp_path):
    paths = make_paths(tmp_path)
    paths.partial_root.mkdir(parents=True)
    (paths.partial_root / "3296316642").mkdir()
    with pytest.raises(IntegrityCheckError, match="No installed Workshop content"):
        discover_integrity_targets(paths, [])

    with pytest.raises(IntegrityCheckError, match="No installed Workshop content folder"):
        discover_integrity_targets(paths, [], selected_id="3296316642")


def test_report_is_atomic_and_records_cautions_and_results(tmp_path):
    started = time.time() - 1
    report = write_integrity_report(
        tmp_path,
        started_at=started,
        finished_at=time.time(),
        results=[
            {"workshop_id": "3296316642", "status": "completed", "message": "Download complete and verified"},
            {"workshop_id": "123456789", "status": "failed", "message": "SteamCMD error"},
            {"workshop_id": "555555555", "status": "cancelled", "message": "User stopped validation"},
        ],
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert report.is_file()
    assert payload["summary"] == {
        "requested": 3,
        "verified": 1,
        "failed": 1,
        "cancelled": 1,
        "not_completed": 0,
    }
    assert "not a read-only scan" in payload["verification"]
    assert payload["results"][0]["workshop_id"] == "3296316642"
    assert not list(report.parent.glob("*.tmp"))
