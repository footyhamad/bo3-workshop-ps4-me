from bo3wd.cli import main


def test_doctor_checks_paths_without_running_steamcmd(tmp_path, capsys):
    steamcmd = tmp_path / "scmd" / "steamcmd.exe"
    steamcmd.parent.mkdir()
    steamcmd.write_bytes(b"test executable only")
    code = main(["doctor", "--steamcmd", str(steamcmd), "--username", "test-user", "--data-dir", str(tmp_path / "appdata")])
    out = capsys.readouterr().out
    assert code == 0
    assert "SteamCMD:" in out
    assert "account name available" in out
    assert "No download was started" in out
    assert (steamcmd.parent / "steamapps" / "workshop" / "downloads" / "311210").is_dir()


def test_download_rejects_bad_ids_before_touching_steamcmd(capsys):
    code = main(["download", "../123"])
    out = capsys.readouterr()
    assert code == 2
    assert "invalid Workshop item" in out.err


def test_download_requires_actual_steamcmd(tmp_path, capsys):
    code = main(["download", "123", "--steamcmd", str(tmp_path / "missing.exe"), "--username", "test-user"])
    out = capsys.readouterr()
    assert code == 2
    assert "SteamCMD not found" in out.err
