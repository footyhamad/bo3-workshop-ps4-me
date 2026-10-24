from pathlib import Path

import pytest

from bo3wd.steamclient import (
    SteamClientImportError,
    SteamClientItemNotFound,
    _extract_library_paths,
    discover_steam_libraries,
    import_steam_client_item,
    steam_client_workshop_uri,
)


def _steam_item(library: Path, item_id: str) -> Path:
    return library / "steamapps" / "workshop" / "content" / "311210" / item_id


def test_extracts_current_and_legacy_libraryfolders_paths():
    text = r'''
    "libraryfolders"
    {
      "0" { "path" "C:\\Program Files (x86)\\Steam" }
      "1" "D:\\SteamLibrary"
    }
    '''
    paths = _extract_library_paths(text)
    assert paths == [Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\SteamLibrary")]


def test_discovers_libraries_from_libraryfolders_vdf(tmp_path):
    steam = tmp_path / "Steam"
    library = tmp_path / "Games" / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    (library / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + str(steam).replace("\\", "\\\\") + '" } '
        '"1" { "path" "' + str(library).replace("\\", "\\\\") + '" } }',
        encoding="utf-8",
    )

    found = discover_steam_libraries([steam])
    assert steam.resolve() in found
    assert library.resolve() in found


def test_imports_missing_files_and_preserves_identical_and_conflicting_files(tmp_path):
    item_id = "3530099560"
    library = tmp_path / "SteamLibrary"
    source = _steam_item(library, item_id)
    (source / "nested").mkdir(parents=True)
    (source / "same.bin").write_bytes(b"same")
    (source / "replace-me.bin").write_bytes(b"new version")
    (source / "nested" / "extra.dat").write_bytes(b"imported data")

    content_root = tmp_path / "SteamCMD" / "steamapps" / "workshop" / "content" / "311210"
    destination = content_root / item_id
    destination.mkdir(parents=True)
    (destination / "same.bin").write_bytes(b"same")
    (destination / "replace-me.bin").write_bytes(b"local version")
    progress: list[int] = []

    result = import_steam_client_item(
        item_id, content_root, extra_library_roots=[library], on_progress=progress.append
    )

    assert result.source == source
    assert result.destination == destination
    assert result.files_copied == 1
    assert result.bytes_copied == len(b"imported data")
    assert progress[-1] == len(b"imported data")
    assert result.files_already_present == 1
    assert result.conflicting_files == ("replace-me.bin",)
    assert (destination / "same.bin").read_bytes() == b"same"
    assert (destination / "replace-me.bin").read_bytes() == b"local version"
    assert (destination / "nested" / "extra.dat").read_bytes() == b"imported data"


def test_import_is_idempotent_when_client_and_destination_are_same(tmp_path):
    item_id = "12345"
    library = tmp_path / "SteamLibrary"
    source = _steam_item(library, item_id)
    source.mkdir(parents=True)
    (source / "map.dat").write_bytes(b"map")
    result = import_steam_client_item(
        item_id,
        library / "steamapps" / "workshop" / "content" / "311210",
        extra_library_roots=[library],
    )
    assert result.files_copied == 0
    assert result.files_already_present == 1
    assert result.destination == source


def test_missing_client_copy_reports_search_paths_without_creating_destination(tmp_path):
    library = tmp_path / "SteamLibrary"
    (library / "steamapps").mkdir(parents=True)
    content_root = tmp_path / "SteamCMD" / "content" / "311210"

    with pytest.raises(SteamClientItemNotFound, match="cannot bypass Steam"):
        import_steam_client_item("99999", content_root, extra_library_roots=[library])

    assert not content_root.exists()


def test_import_refuses_destination_symlink(tmp_path):
    item_id = "123"
    library = tmp_path / "SteamLibrary"
    source = _steam_item(library, item_id)
    source.mkdir(parents=True)
    (source / "map.dat").write_bytes(b"map")
    content_root = tmp_path / "content"
    content_root.mkdir()
    target = content_root / item_id
    try:
        target.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This environment does not allow creating symlinks")

    with pytest.raises(SteamClientImportError, match="symlink or junction"):
        import_steam_client_item(item_id, content_root, extra_library_roots=[library])


def test_steam_client_uri_uses_validated_numeric_id():
    assert steam_client_workshop_uri("3530099560") == "steam://url/CommunityFilePage/3530099560"
    assert steam_client_workshop_uri("https://steamcommunity.com/sharedfiles/filedetails/?id=123") == \
        "steam://url/CommunityFilePage/123"
    with pytest.raises(ValueError):
        steam_client_workshop_uri("steam://malicious")
