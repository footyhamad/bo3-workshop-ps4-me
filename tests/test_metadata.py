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
        if request.method == "GET":
            return Response(b"<html><body></body></html>")
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


def test_zero_size_metadata_falls_back_to_workshop_page(monkeypatch):
    page = """
    <html><head><meta property="og:title" content="Death Spiral :: Steam Workshop"></head>
    <body>
      <div class="detailsStatsContainerLeft"><div class="detailsStatLeft">File Size</div><div class="detailsStatLeft">Posted</div></div>
      <div class="detailsStatsContainerRight"><div class="detailsStatRight">19.558 MB</div><div class="detailsStatRight">Oct 1, 2026</div></div>
    </body></html>
    """
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.method, request.full_url))
        if request.method == "POST":
            return Response(json.dumps({
                "response": {"publishedfiledetails": [{
                    "result": 1, "publishedfileid": "3296316642",
                    "consumer_app_id": 311210, "title": "Death Spiral", "file_size": 0,
                }]}
            }).encode())
        return Response(page.encode())

    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", fake_urlopen)
    result = WorkshopMetadataClient(timeout_seconds=3).fetch_many(["3296316642"])
    item = result["3296316642"]
    assert [method for method, _url in seen] == ["POST", "GET"]
    assert item.expected_bytes == int(19.558 * 1024 * 1024)
    assert item.title == "Death Spiral"
    assert item.url.endswith("id=3296316642")


def test_missing_api_item_can_be_recovered_from_public_page(monkeypatch):
    page = """
    <html><head><meta property="og:title" content="Public Map :: Steam Community"></head>
    <body><div class="detailsStatsContainerLeft"><div class="detailsStatLeft">File Size</div></div>
    <div class="detailsStatsContainerRight"><div class="detailsStatRight">1.25 GB</div></div></body></html>
    """

    def fake_urlopen(request, timeout):
        if request.method == "POST":
            return Response(json.dumps({"response": {"publishedfiledetails": []}}).encode())
        return Response(page.encode())

    monkeypatch.setattr("bo3wd.metadata.urllib.request.urlopen", fake_urlopen)
    result = WorkshopMetadataClient().fetch_many(["123456789"])
    assert result["123456789"].title == "Public Map"
    assert result["123456789"].expected_bytes == int(1.25 * 1024**3)


def test_parse_workshop_size_handles_units_and_grouped_separators():
    from bo3wd.metadata import parse_workshop_size

    assert parse_workshop_size("512 KB") == 512 * 1024
    assert parse_workshop_size("1.5 GB") == int(1.5 * 1024**3)
    assert parse_workshop_size("1.234.567 MB") == 1_234_567 * 1024**2
    assert parse_workshop_size("unknown") == 0


def test_api_metadata_without_size_still_returns_a_downloadable_item(monkeypatch):
    payload = {"response": {"publishedfiledetails": [{
        "result": 1, "publishedfileid": "123", "consumer_app_id": 311210,
        "title": "No reported size", "file_size": 0,
    }]}}
    monkeypatch.setattr(
        "bo3wd.metadata.urllib.request.urlopen",
        lambda request, timeout: Response(
            json.dumps(payload).encode()
            if request.method == "POST"
            else b"<html><title>not enough data</title></html>"
        ),
    )
    item = WorkshopMetadataClient().fetch_many(["123"])["123"]
    assert item.expected_bytes == 0
    assert item.title == "No reported size"
