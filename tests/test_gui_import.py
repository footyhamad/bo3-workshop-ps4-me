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
