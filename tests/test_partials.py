import os
import time

from bo3wd.partials import delete_old_partial_folders, find_stale_partial_folders


def _write_file(path, data, modified_at):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (modified_at, modified_at))
    # Directory timestamps also reflect the last entry created/removed in each folder.
    os.utime(path.parent, (modified_at, modified_at))


def test_finds_only_numeric_partial_folders_older_than_seven_days(tmp_path):
    root = tmp_path / "steamapps/workshop/downloads/311210"
    now = time.time()
    stale_file = root / "3296316642" / "chunk.bin"
    _write_file(stale_file, b"old partial", now - 10 * 86400)
    _write_file(root / "123456" / "active.bin", b"recent", now - 2 * 86400)
    _write_file(root / "not-an-id" / "old.bin", b"ignored", now - 30 * 86400)
    protected = root / "999999" / "old.bin"
    _write_file(protected, b"queued partial", now - 30 * 86400)

    stale = find_stale_partial_folders(
        root, older_than_days=7, protected_ids={"999999"}, now=now
    )

    assert [item.workshop_id for item in stale] == ["3296316642"]
    assert stale[0].size_bytes == len(b"old partial")


def test_delete_old_partial_folders_does_not_delete_recent_or_installed_content(tmp_path):
    steam_root = tmp_path / "steamcmd" / "steamapps" / "workshop"
    partial_root = steam_root / "downloads" / "311210"
    content_root = steam_root / "content" / "311210"
    now = time.time()
    old_id = "3296316642"
    fresh_id = "123456789"
    _write_file(partial_root / old_id / "chunk.bin", b"remove", now - 12 * 86400)
    _write_file(partial_root / fresh_id / "chunk.bin", b"keep partial", now - 1 * 86400)
    installed = content_root / old_id / "zone.ff"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(b"installed map")

    deleted, errors = delete_old_partial_folders(
        partial_root, [old_id, fresh_id], older_than_days=7, now=now
    )

    assert [item.workshop_id for item in deleted] == [old_id]
    assert errors == [(fresh_id, "No longer stale, protected, missing, or inaccessible; skipped.")]
    assert not (partial_root / old_id).exists()
    assert (partial_root / fresh_id / "chunk.bin").read_bytes() == b"keep partial"
    assert installed.read_bytes() == b"installed map"


def test_partial_folder_must_be_at_least_one_day_old(tmp_path):
    try:
        find_stale_partial_folders(tmp_path, older_than_days=0)
    except ValueError as exc:
        assert ">= 1" in str(exc)
    else:
        raise AssertionError("zero-day cleanup threshold should be rejected")
