from bo3wd.queueview import matches_queue_row


def test_queue_filter_matches_id_title_and_status_independently():
    row = {
        "workshop_id": "3296316642",
        "title": "Death Spiral",
        "status": "failed",
        "message": "No progress on last attempt",
    }
    assert matches_queue_row(row, "death")
    assert matches_queue_row(row, "329631 failed", "Needs attention")
    assert matches_queue_row(row, "", "Needs attention")
    assert not matches_queue_row(row, "spider", "All statuses")
    assert not matches_queue_row(row, "", "Completed")


def test_queue_filter_active_groups_all_active_statuses():
    for status in ("preparing", "downloading", "retry_wait"):
        assert matches_queue_row({"status": status}, "", "Active")
    assert not matches_queue_row({"status": "queued"}, "", "Active")
