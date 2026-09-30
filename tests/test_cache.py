import json
from pathlib import Path

import bo3ps4
from bo3ps4 import Config, cache_store, cache_valid, delete_cache, progress_event


def make_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "ps4_ip": "127.0.0.1",
        "workdir": str(tmp_path / "work"),
        "steamcmd": str(tmp_path / "steamcmd.exe"),
    }), encoding="utf-8")
    return p


def test_config_defaults_are_backward_compatible(tmp_path):
    bo3ps4.CFG = Config(make_config(tmp_path))
    assert bo3ps4.CFG.keep_cache is True
    assert bo3ps4.CFG.cache_dir == tmp_path / "work" / "cache" / "maps"
    assert bo3ps4.CFG.psslc_path is None
    assert bo3ps4.CFG.strict_mode is False
    assert bo3ps4.CFG.parallelism == 1


def test_cache_store_and_version_validation(tmp_path):
    bo3ps4.CFG = Config(make_config(tmp_path))
    source = tmp_path / "steam-item"
    source.mkdir()
    (source / "zm_test.ff").write_bytes(b"synthetic fastfile")
    details = {"title": "Synthetic", "size": 123, "time_updated": 10}

    cache_store("123", source, details)
    assert cache_valid("123", details)
    assert not cache_valid("123", {"size": 123, "time_updated": 11})


def test_cache_clean_only_targets_map_without_include_zones(tmp_path):
    bo3ps4.CFG = Config(make_config(tmp_path))
    root = bo3ps4.CFG.cache_dir / "123"
    root.mkdir(parents=True)
    (root / "zm_test.ff").write_bytes(b"synthetic")
    (root / ".bo3ps4-cache.json").write_text(
        json.dumps({"schema": 1, "workshop_id": "123"}), encoding="utf-8"
    )
    zones = bo3ps4.CFG.zones
    zones.mkdir(parents=True)
    (zones / "stock.ff").write_bytes(b"zones")

    delete_cache("123", yes=True)
    assert not root.exists()
    assert zones.exists()


def test_progress_event_machine_readable(capsys, tmp_path):
    bo3ps4.CFG = Config(make_config(tmp_path))
    bo3ps4.CFG.progress_mode = "json"
    event = progress_event("123", "convert", "images", 3, 10, "asset", 2.5, 2.8, "testing")
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["map"] == "123"
    assert parsed["stage"] == "convert"
    assert parsed["done"] == 3
    assert parsed["total"] == 10
    assert parsed["speed"] == 2.5
    assert event["message"] == "testing"
