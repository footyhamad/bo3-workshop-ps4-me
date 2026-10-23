from bo3wd.steamcmd import build_download_command, discover_steamcmd, read_saved_account


def test_command_is_argument_array_and_keeps_app_id_fixed(tmp_path):
    path = tmp_path / "steamcmd.exe"
    command = build_download_command(path, "some account", "3296316642")
    assert command == [
        str(path), "+login", "some account", "+workshop_download_item", "311210", "3296316642", "validate", "+quit"
    ]
    assert build_download_command(path, "account", "123", validate=False)[-1] == "+quit"
    assert "validate" not in build_download_command(path, "account", "123", validate=False)


def test_command_rejects_empty_username_and_bad_id(tmp_path):
    try:
        build_download_command(tmp_path / "steamcmd.exe", " ", "123")
    except ValueError as exc:
        assert "account" in str(exc)
    else:
        raise AssertionError("empty account should be rejected")
    try:
        build_download_command(tmp_path / "steamcmd.exe", "account", "../123")
    except ValueError as exc:
        assert "Workshop" in str(exc)
    else:
        raise AssertionError("invalid item ID should be rejected")


def test_read_saved_account_reads_name_but_never_a_password(tmp_path):
    executable = tmp_path / "steamcmd.exe"
    executable.write_bytes(b"fake")
    config = tmp_path / "config" / "config.vdf"
    config.parent.mkdir()
    config.write_text('"Accounts" { "my_steam_user" { "SteamID" "123" } }', encoding="utf-8")
    assert read_saved_account(executable) == "my_steam_user"


def test_saved_account_missing_or_unreadable_returns_none(tmp_path):
    assert read_saved_account(tmp_path / "missing.exe") is None
    executable = tmp_path / "steamcmd.exe"
    executable.touch()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.vdf").write_text('"Accounts" { }', encoding="utf-8")
    assert read_saved_account(executable) is None


def test_discover_steamcmd_returns_none_without_known_executable(monkeypatch, tmp_path):
    monkeypatch.setattr("bo3wd.steamcmd.shutil.which", lambda _name: None)
    monkeypatch.setattr("bo3wd.steamcmd.Path.home", lambda: tmp_path)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert discover_steamcmd() is None
