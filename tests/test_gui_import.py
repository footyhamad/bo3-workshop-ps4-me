import pytest


def test_gui_module_import_does_not_create_window_or_make_network_request():
    pytest.importorskip("tkinter")
    module = __import__("bo3wd.gui", fromlist=["DownloaderApp"])
    assert hasattr(module, "DownloaderApp")
    assert callable(module.launch)


def test_label_allows_custom_colors_without_duplicate_tk_options(monkeypatch):
    import bo3wd.gui as gui

    captured = {}

    def fake_label(parent, **kwargs):
        captured["parent"] = parent
        captured.update(kwargs)
        return captured

    monkeypatch.setattr(gui.tk, "Label", fake_label)
    parent = object()
    result = gui.DownloaderApp._label(None, parent, "custom label", fg="#929baa")

    assert result["parent"] is parent
    assert captured["text"] == "custom label"
    assert captured["fg"] == "#929baa"
    assert captured["bg"] == "#111318"
    assert captured["font"] == ("Segoe UI", 9)

def test_download_progress_displays_data_and_percentage_without_creating_window():
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    from bo3wd.gui import DownloaderApp
    from bo3wd.models import DownloadEvent, JobStatus

    class FakeProgress:
        def __init__(self):
            self.values = {}
            self.started = False
            self.stopped = False

        def configure(self, **kwargs):
            self.values.update(kwargs)

        def start(self, _interval):
            self.started = True
            self.stopped = False

        def stop(self):
            self.stopped = True
            self.started = False

    class FakeText:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    bar = FakeProgress()
    label = FakeText()
    app = SimpleNamespace(
        history=SimpleNamespace(get=lambda _item_id: {}),
        download_progress=bar,
        progress_text_var=label,
        progress_is_indeterminate=False,
        _format_bytes=DownloaderApp._format_bytes,
    )
    event = DownloadEvent(
        workshop_id="123",
        status=JobStatus.DOWNLOADING,
        bytes_received=1_000_000,
        expected_bytes=4_000_000,
        speed_bytes_per_second=500_000,
    )

    DownloaderApp._update_download_progress(app, event)

    assert bar.values["mode"] == "determinate"
    assert bar.values["value"] == 25
    assert "25.0%" in label.value
    assert "1.0 MB / 4.0 MB" in label.value
    assert "500.0 KB/s" in label.value


def test_download_progress_shows_received_bytes_when_total_size_is_unknown():
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    from bo3wd.gui import DownloaderApp
    from bo3wd.models import DownloadEvent, JobStatus

    class FakeProgress:
        def __init__(self):
            self.values = {}
            self.started = False

        def configure(self, **kwargs):
            self.values.update(kwargs)

        def start(self, _interval):
            self.started = True

        def stop(self):
            self.started = False

    class FakeText:
        value = ""

        def set(self, value):
            self.value = value

    bar = FakeProgress()
    label = FakeText()
    app = SimpleNamespace(
        history=SimpleNamespace(get=lambda _item_id: {}),
        download_progress=bar,
        progress_text_var=label,
        progress_is_indeterminate=False,
        _format_bytes=DownloaderApp._format_bytes,
    )
    event = DownloadEvent(
        workshop_id="123",
        status=JobStatus.DOWNLOADING,
        bytes_received=2_500_000,
        expected_bytes=0,
        speed_bytes_per_second=250_000,
    )

    DownloaderApp._update_download_progress(app, event)

    assert bar.values["mode"] == "indeterminate"
    assert bar.started is True
    assert "2.5 MB downloaded" in label.value
    assert "250.0 KB/s" in label.value
