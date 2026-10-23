import hashlib
import json
from io import BytesIO

import pytest

from bo3wd.updater import (
    APP_EXE_NAME,
    CHECKSUM_NAME,
    ReleaseInfo,
    UpdateError,
    _powershell_update_script,
    check_for_update,
    download_verified_update,
    parse_version,
)


class FakeResponse(BytesIO):
    def __init__(self, body, url, headers=None):
        super().__init__(body)
        self._url = url
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def geturl(self):
        return self._url


def asset(tag, name, body=b"x"):
    return {
        "name": name,
        "size": len(body),
        "browser_download_url": (
            f"https://github.com/footyhamad/bo3-workshop-ps4-me/releases/download/{tag}/{name}"
        ),
    }


def release(tag, *, prerelease=False, draft=False, exe_body=b"test-executable", checksum=True):
    digest = hashlib.sha256(exe_body).hexdigest()
    assets = [asset(tag, APP_EXE_NAME, exe_body)]
    if checksum:
        assets.append(asset(tag, CHECKSUM_NAME, f"{digest}  {APP_EXE_NAME}\n".encode()))
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/footyhamad/bo3-workshop-ps4-me/releases/tag/{tag}",
        "assets": assets,
    }


def test_semver_is_numeric_and_strict():
    assert parse_version("2.10.0") > parse_version("2.9.99")
    assert parse_version("v3.0.0") > parse_version("2.99.99")
    with pytest.raises(ValueError):
        parse_version("v2.0")
    with pytest.raises(ValueError):
        parse_version("v2.0.3-rc1")


def test_check_chooses_highest_stable_semver_not_release_order(monkeypatch):
    releases = [
        release("v2.9.0"),
        release("v2.10.0"),
        release("v99.0.0-rc1", prerelease=True),
        release("v100.0.0", draft=True),
        release("v2.8.0"),
    ]
    monkeypatch.setattr(
        "bo3wd.updater.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps(releases).encode(), "https://api.github.com/repos/releases"
        ),
    )

    latest = check_for_update("2.9.1")

    assert latest.latest_version == "2.10.0"
    assert latest.tag_name == "v2.10.0"
    assert latest.update_available is True


def test_check_never_offers_downgrade(monkeypatch):
    releases = [release("v2.9.0"), release("v2.8.0")]
    monkeypatch.setattr(
        "bo3wd.updater.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps(releases).encode(), "https://api.github.com/repos/releases"
        ),
    )

    latest = check_for_update("2.10.0")

    assert latest.latest_version == "2.9.0"
    assert latest.update_available is False


def test_check_skips_release_without_both_executable_and_checksum(monkeypatch):
    releases = [release("v2.10.0", checksum=False), release("v2.9.0")]
    monkeypatch.setattr(
        "bo3wd.updater.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps(releases).encode(), "https://api.github.com/repos/releases"
        ),
    )

    latest = check_for_update("2.8.0")
    assert latest.latest_version == "2.9.0"


def test_check_errors_if_no_complete_stable_release(monkeypatch):
    releases = [release("v2.10.0", prerelease=True), release("v3.0.0", checksum=False)]
    monkeypatch.setattr(
        "bo3wd.updater.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps(releases).encode(), "https://api.github.com/repos/releases"
        ),
    )

    with pytest.raises(UpdateError, match="No stable release"):
        check_for_update("2.9.0")


def test_download_verifies_checksum_and_stages_next_to_application(tmp_path, monkeypatch):
    payload = b"official new executable bytes"
    digest = hashlib.sha256(payload).hexdigest().encode()
    checksum_body = digest + b"  BO3WorkshopDownloader.exe\n"
    info = ReleaseInfo(
        current_version="2.9.0",
        latest_version="2.10.0",
        tag_name="v2.10.0",
        release_url="https://github.com/footyhamad/bo3-workshop-ps4-me/releases/tag/v2.10.0",
        executable_url=asset("v2.10.0", APP_EXE_NAME)["browser_download_url"],
        checksum_url=asset("v2.10.0", CHECKSUM_NAME)["browser_download_url"],
        executable_size=len(payload),
    )
    def fake_urlopen(request, timeout):
        url = request.full_url
        if url.endswith(CHECKSUM_NAME):
            return FakeResponse(checksum_body, "https://objects.githubusercontent.com/checksum")
        if url.endswith(APP_EXE_NAME):
            return FakeResponse(payload, "https://objects.githubusercontent.com/executable",
                                {"Content-Length": str(len(payload))})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("bo3wd.updater.urllib.request.urlopen", fake_urlopen)
    target_dir = tmp_path / "application folder"
    target_dir.mkdir()
    staged = download_verified_update(info, target_dir)

    assert staged.parent == target_dir
    assert staged.is_file()
    assert staged.read_bytes() == payload
    assert staged.name == "BO3WorkshopDownloader.update-v2.10.0.exe"
    assert not list(target_dir.glob("*.part"))


def test_download_rejects_wrong_checksum_and_removes_partial_file(tmp_path, monkeypatch):
    payload = b"tampered executable"
    checksum_body = (hashlib.sha256(b"different bytes").hexdigest() + "  BO3WorkshopDownloader.exe\n").encode()
    info = ReleaseInfo(
        current_version="2.0.2",
        latest_version="2.0.3",
        tag_name="v2.0.3",
        release_url="https://github.com/footyhamad/bo3-workshop-ps4-me/releases/tag/v2.0.3",
        executable_url=asset("v2.0.3", APP_EXE_NAME, payload)["browser_download_url"],
        checksum_url=asset("v2.0.3", CHECKSUM_NAME)["browser_download_url"],
        executable_size=len(payload),
    )

    def fake_urlopen(request, timeout):
        if request.full_url.endswith(CHECKSUM_NAME):
            return FakeResponse(checksum_body, "https://objects.githubusercontent.com/checksum")
        return FakeResponse(payload, "https://objects.githubusercontent.com/executable")

    monkeypatch.setattr("bo3wd.updater.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(UpdateError, match="SHA-256"):
        download_verified_update(info, tmp_path)
    assert not list(tmp_path.glob("*.exe"))
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_same_or_older_version_before_network(tmp_path, monkeypatch):
    info = ReleaseInfo(
        current_version="2.10.0",
        latest_version="2.9.0",
        tag_name="v2.9.0",
        release_url="https://github.com/footyhamad/bo3-workshop-ps4-me/releases/tag/v2.9.0",
        executable_url="https://github.com/footyhamad/bo3-workshop-ps4-me/releases/download/v2.9.0/BO3WorkshopDownloader.exe",
        checksum_url="https://github.com/footyhamad/bo3-workshop-ps4-me/releases/download/v2.9.0/SHA256SUMS.txt",
    )
    monkeypatch.setattr("bo3wd.updater.urllib.request.urlopen", lambda *_a, **_k: pytest.fail("no request expected"))
    with pytest.raises(UpdateError, match="not newer"):
        download_verified_update(info, tmp_path)


def test_release_metadata_rejects_untrusted_download_url(monkeypatch):
    malicious = release("v3.0.0")
    malicious["assets"][0]["browser_download_url"] = "https://example.com/BO3WorkshopDownloader.exe"
    monkeypatch.setattr(
        "bo3wd.updater.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            json.dumps([malicious]).encode(), "https://api.github.com/repos/releases"
        ),
    )
    with pytest.raises(UpdateError, match="No stable release"):
        check_for_update("2.0.0")


def test_powershell_paths_are_base64_data_not_interpolated_commands(tmp_path):
    source = tmp_path / "stage; do-not-run.exe"
    target = tmp_path / "app 'quoted'.exe"
    backup = tmp_path / "backup.exe"
    log = tmp_path / "update.log"
    script = _powershell_update_script(source, target, backup, log, 1234)
    assert str(source) not in script
    assert str(target) not in script
    assert "$parentProcessId = 1234" in script
    assert "WaitForExit" in script
    assert "Move-Item" in script
    assert "Start-Process" in script
