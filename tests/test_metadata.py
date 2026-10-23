import io
import json

import pytest

from bo3wd.metadata import MetadataError, WorkshopMetadataClient


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_metadata_maps_steam_payload_and_ignores_other_app_ids(monkeypatch):
    payload = {
        "response": {
            "publishedfiledetails": [
                {"result": 1, "publishedfileid": "123", "consumer_app_id": 311210, "title": "Example", "file_size": "2048", "time_updated": 1700000000},
                {"result": 1, "publishedfileid": "456", "consumer_app_id": 440, "title": "Other game", "file_size": "99"},
                {"result": 9, "publishedfileid": "789", "consumer_app_id": 311210, "title": "Private"},
            ]
        }
    }
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = request.data.decode()
        seen["timeout"] = timeout
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", fake_urlopen)
    result = WorkshopMetadataClient(timeout_seconds=4).fetch_many(["123", "456", "789"])
    assert list(result) == ["123"]
    assert result["123"].title == "Example"
    assert result["123"].expected_bytes == 2048
    assert result["123"].url.endswith("id=123")
    assert "publishedfileids%5B0%5D=123" in seen["body"]
    assert seen["timeout"] == 4


def test_metadata_rejects_more_than_100_ids():
    client = WorkshopMetadataClient()
    with pytest.raises(ValueError, match="100 IDs"):
        client.fetch_many([str(n) for n in range(1, 102)])


def test_metadata_network_failures_have_clear_error(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", broken)
    with pytest.raises(MetadataError, match="metadata request failed"):
        WorkshopMetadataClient().fetch_many(["123"])


def test_empty_metadata_request_does_not_touch_network(monkeypatch):
    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", lambda *_a, **_k: pytest.fail("unexpected request"))
    assert WorkshopMetadataClient().fetch_many([]) == {}


def test_metadata_normalizes_ids_and_handles_bad_optional_numeric_fields(monkeypatch):
    payload = {
        "response": {
            "publishedfiledetails": [
                {
                    "result": 1,
                    "publishedfileid": "123",
                    "consumer_app_id": "311210",
                    "title": "Example",
                    "file_size": "not-a-number",
                    "time_updated": "invalid",
                }
            ]
        }
    }
    seen = {}

    def fake_urlopen(request, timeout):
        seen["body"] = request.data.decode()
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", fake_urlopen)
    result = WorkshopMetadataClient().fetch_many(["000123", "123"])
    assert list(result) == ["123"]
    assert result["123"].expected_bytes == 0
    assert result["123"].updated_at is None
    assert seen["body"].count("=123") == 1


def test_metadata_rejects_invalid_ids_before_network(monkeypatch):
    monkeypatch.setattr(
        "bo3wd.metadata.urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid IDs must be rejected before network I/O"),
    )
    with pytest.raises(MetadataError, match="Invalid Workshop ID"):
        WorkshopMetadataClient().fetch_many(["../../123"])


def test_metadata_rejects_malformed_response_shapes(monkeypatch):
    monkeypatch.setattr(
        "bo3wd.metadata.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(b'{"response":{"publishedfiledetails":{}}}'),
    )
    with pytest.raises(MetadataError, match="publishedfiledetails list"):
        WorkshopMetadataClient().fetch_many(["123"])
