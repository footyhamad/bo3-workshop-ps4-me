import json
from pathlib import Path

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

def test_download_progress_uses_live_bytes_without_size_reporting():
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
        _format_rate=DownloaderApp._format_rate,
    )
    event = DownloadEvent(
        workshop_id="123",
        status=JobStatus.DOWNLOADING,
        bytes_received=1_000_000,
        expected_bytes=4_000_000,
        speed_bytes_per_second=500_000,
    )

    DownloaderApp._update_download_progress(app, event)

    assert bar.values["mode"] == "indeterminate"
    assert bar.started is True
    assert "976 KB downloaded" in label.value
    assert "488 KB/s" in label.value
    assert "4.0 MB" not in label.value
    assert "%" not in label.value


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
        _format_rate=DownloaderApp._format_rate,
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
    assert "2,441 KB downloaded" in label.value
    assert "244 KB/s" in label.value


def test_queue_summary_counts_queued_active_completed_and_attention():
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    from bo3wd.gui import DownloaderApp

    rows = {
        "101": ("101", "Queued item", "Queued"),
        "102": ("102", "Working item", "Downloading"),
        "103": ("103", "Retrying item", "Retry Wait"),
        "104": ("104", "Finished item", "Completed"),
        "105": ("105", "Failed item", "Failed"),
        "106": ("106", "Stopped item", "Cancelled"),
    }

    class FakeTable:
        def get_children(self, _parent=""):
            return tuple(rows)

        def item(self, item_id, _kind):
            return rows[item_id]

    class FakeVariable:
        def __init__(self):
            self.value = "0"

        def set(self, value):
            self.value = value

    app = SimpleNamespace(
        table=FakeTable(),
        summary_vars={key: FakeVariable() for key in ("queued", "active", "completed", "attention")},
    )

    DownloaderApp._refresh_queue_summary(app)

    assert {key: variable.value for key, variable in app.summary_vars.items()} == {
        "queued": "1",
        "active": "2",
        "completed": "1",
        "attention": "2",
    }


def test_recover_partial_downloads_imports_numeric_cache_folders_without_deleting_files(tmp_path, monkeypatch):
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    import bo3wd.gui as gui

    steamcmd = tmp_path / "scmd" / "steamcmd.exe"
    steamcmd.parent.mkdir(parents=True)
    steamcmd.write_bytes(b"fake test executable")
    partial_root = steamcmd.parent / "steamapps" / "workshop" / "downloads" / "311210"
    partial = partial_root / "3296316642"
    partial.mkdir(parents=True)
    marker = partial / "partial.bin"
    marker.write_bytes(b"keep this partial data")
    (partial_root / "README.txt").write_text("not an item id", encoding="utf-8")

    added = []
    class FakeStatus:
        value = ""

        def set(self, value):
            self.value = value

    status = FakeStatus()

    class FakeHistory:
        def ensure_item(self, item, log_path=None):
            added.append((item.workshop_id, log_path))

    app = SimpleNamespace(
        steamcmd_var=SimpleNamespace(get=lambda: str(steamcmd)),
        history=FakeHistory(),
        data_dir=tmp_path / "appdata",
        _load_history=lambda: None,
        status_var=status,
        _start_queue=lambda: pytest.fail("declined automatic start should not start queue"),
    )
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    gui.DownloaderApp._recover_partial_downloads(app)

    assert [entry[0] for entry in added] == ["3296316642"]
    assert "Recovered 1 partial" in status.value
    assert marker.read_bytes() == b"keep this partial data"


def test_integrity_worker_forces_steamcmd_validation_and_writes_a_report(tmp_path, monkeypatch):
    pytest.importorskip("tkinter")
    import queue
    import threading
    from types import SimpleNamespace

    import bo3wd.gui as gui
    from bo3wd.models import DownloadEvent, DownloadPaths, DownloadPolicy, JobStatus, WorkshopItem

    steamcmd = tmp_path / "steamcmd" / "steamcmd.exe"
    steamcmd.parent.mkdir(parents=True)
    steamcmd.write_bytes(b"placeholder")
    paths = DownloadPaths.from_steamcmd(steamcmd, tmp_path / "appdata")
    events = queue.Queue()
    captured = {}

    class FakeEngine:
        def __init__(self, paths_arg, username, *, policy, validate, history, event_sink):
            captured["paths"] = paths_arg
            captured["username"] = username
            captured["policy"] = policy
            captured["validate"] = validate
            captured["history"] = history
            captured["event_sink"] = event_sink

        def download(self, item, cancel_event):
            assert not cancel_event.is_set()
            return DownloadEvent(
                workshop_id=item.workshop_id,
                status=JobStatus.COMPLETED,
                message="Download complete and verified",
                log_path=str(tmp_path / "item.log"),
            )

    history = object()
    monkeypatch.setattr(gui, "DownloadEngine", FakeEngine)
    app = SimpleNamespace(
        settings={"steam_user": "test-account"},
        history=history,
        events=events,
        stop_event=threading.Event(),
        current_id=None,
        data_dir=tmp_path / "appdata",
    )
    policy = DownloadPolicy(
        max_attempts=1, attempt_timeout_seconds=60,
        initial_backoff_seconds=0, max_backoff_seconds=0, max_stagnant_attempts=1,
    )

    gui.DownloaderApp._run_integrity_check(
        app, [WorkshopItem("3296316642", title="Death Spiral")], paths, policy
    )

    assert captured["validate"] is True
    assert captured["policy"].max_attempts == 1
    assert captured["username"] == "test-account"
    event = events.get_nowait()
    assert event[0] == "message"
    done = events.get_nowait()
    assert done[0] == "integrity_done"
    summary = done[1]
    assert not summary["report_error"]
    assert summary["results"][0]["status"] == "completed"
    assert summary["results"][0]["workshop_id"] == "3296316642"
    report = json.loads(Path(summary["report_path"]).read_text(encoding="utf-8"))
    assert report["summary"]["verified"] == 1


def test_text_entry_context_menu_offers_edit_actions_and_preserves_shortcuts(monkeypatch):
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    import bo3wd.gui as gui

    class FakeMenu:
        def __init__(self, owner, **_kwargs):
            self.owner = owner
            self.commands = {}
            self.states = {}
            owner.menus.append(self)

        def add_command(self, *, label, command):
            self.commands[label] = command

        def add_separator(self):
            return None

        def entryconfigure(self, label, *, state):
            self.states[label] = state

        def tk_popup(self, x, y):
            self.popup_position = (x, y)

        def grab_release(self):
            self.released = True

    class FakeEntry:
        def __init__(self):
            self.bindings = {}
            self.menus = []
            self.events = []
            self.state = "normal"
            self.text = "Workshop URL"
            self.has_selection = True
            self.focused = False
            self.caret = None

        def bind(self, sequence, callback, add=None):
            self.bindings[sequence] = callback
            assert add == "+"

        def focus_set(self):
            self.focused = True

        def selection_present(self):
            return self.has_selection

        def cget(self, name):
            assert name == "state"
            return self.state

        def clipboard_get(self):
            return "clipboard text"

        def get(self):
            return self.text

        def index(self, location):
            assert location.startswith("@")
            return 3

        def icursor(self, position):
            self.caret = position

        def selection_range(self, first, last):
            self.selection = (first, last)
            self.has_selection = True

        def event_generate(self, virtual_event):
            self.events.append(virtual_event)

    monkeypatch.setattr(gui.tk, "Menu", FakeMenu)
    app = SimpleNamespace(
        _text_context_action=lambda widget, action: gui.DownloaderApp._text_context_action(
            app, widget, action
        )
    )
    widget = FakeEntry()
    gui.DownloaderApp._attach_text_context_menu(app, widget)

    assert "<Button-3>" in widget.bindings
    popup_event = SimpleNamespace(x=12, y=4, x_root=312, y_root=404)
    assert widget.bindings["<Button-3>"](popup_event) == "break"

    menu = widget.menus[0]
    assert set(menu.commands) == {"Cut", "Copy", "Paste", "Select All"}
    assert menu.states == {
        "Cut": "normal",
        "Copy": "normal",
        "Paste": "normal",
        "Select All": "normal",
    }
    assert menu.popup_position == (312, 404)
    assert menu.released is True
    assert widget.focused is True

    menu.commands["Cut"]()
    menu.commands["Copy"]()
    menu.commands["Paste"]()
    menu.commands["Select All"]()
    assert widget.events == ["<<Cut>>", "<<Copy>>", "<<Paste>>"]
    assert widget.selection == (0, gui.tk.END)
    assert widget.caret == gui.tk.END

    # With no selection, Cut and Copy disable, while Paste remains available.
    widget.has_selection = False
    widget.bindings["<Button-3>"](popup_event)
    assert menu.states["Cut"] == "disabled"
    assert menu.states["Copy"] == "disabled"
    assert menu.states["Paste"] == "normal"


def test_read_only_text_context_menu_disables_cut_and_paste(monkeypatch):
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    import bo3wd.gui as gui

    class FakeMenu:
        def __init__(self, owner, **_kwargs):
            self.owner = owner
            self.commands = {}
            self.states = {}
            owner.menus.append(self)

        def add_command(self, *, label, command):
            self.commands[label] = command

        def add_separator(self):
            return None

        def entryconfigure(self, label, *, state):
            self.states[label] = state

        def tk_popup(self, *_coords):
            return None

        def grab_release(self):
            return None

    class FakeText:
        def __init__(self):
            self.bindings = {}
            self.menus = []
            self.state = "disabled"

        def bind(self, sequence, callback, add=None):
            self.bindings[sequence] = callback

        def focus_set(self):
            return None

        def index(self, _location):
            return "1.0"

        def tag_ranges(self, _tag):
            return ()

        def mark_set(self, *_args):
            return None

        def compare(self, *_args):
            return False

        def cget(self, _name):
            return self.state

        def clipboard_get(self):
            return "log text"

        def get(self, *_args):
            return "read-only logs"

    monkeypatch.setattr(gui.tk, "Menu", FakeMenu)
    app = SimpleNamespace(
        _text_context_action=lambda widget, action: gui.DownloaderApp._text_context_action(
            app, widget, action
        )
    )
    widget = FakeText()
    gui.DownloaderApp._attach_text_context_menu(app, widget)
    widget.bindings["<Button-3>"](SimpleNamespace(x=1, y=1, x_root=10, y_root=10))

    states = widget.menus[0].states
    assert states["Cut"] == "disabled"
    assert states["Copy"] == "disabled"
    assert states["Paste"] == "disabled"
    assert states["Select All"] == "normal"


def test_queue_search_and_status_filter_keep_persisted_rows_and_global_summary(monkeypatch):
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    import bo3wd.gui as gui

    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeTable:
        def __init__(self):
            self.rows = {}

        def get_children(self, _parent=""):
            return tuple(self.rows)

        def exists(self, item_id):
            return item_id in self.rows

        def delete(self, item_id):
            self.rows.pop(item_id, None)

        def insert(self, _parent, _index, *, iid, values, tags=()):
            self.rows[iid] = {"values": tuple(values), "tags": tuple(tags)}

        def item(self, item_id, option=None, **kwargs):
            if kwargs:
                self.rows[item_id].update(kwargs)
            if option:
                return self.rows[item_id].get(option)
            return self.rows[item_id]

        def move(self, item_id, _parent, index):
            items = list(self.rows.items())
            selected = next(pair for pair in items if pair[0] == item_id)
            items.remove(selected)
            items.insert(index, selected)
            self.rows = dict(items)

    app = SimpleNamespace(
        queue_rows={
            "3296316642": {
                "workshop_id": "3296316642", "title": "Death Spiral",
                "status": "queued", "attempt": 0, "max_attempts": 30,
                "bytes_received": 0, "expected_bytes": 0, "message": "",
            },
            "123456789": {
                "workshop_id": "123456789", "title": "Active Map",
                "status": "downloading", "attempt": 1, "max_attempts": 30,
                "bytes_received": 100, "expected_bytes": 1000, "message": "transfer",
            },
            "555555555": {
                "workshop_id": "555555555", "title": "Failed Map",
                "status": "failed", "attempt": 2, "max_attempts": 30,
                "bytes_received": 0, "expected_bytes": 0, "message": "network failure",
            },
        },
        queue_search_var=Variable("death"),
        queue_status_filter_var=Variable("All statuses"),
        table=FakeTable(),
        summary_vars={key: Variable("0") for key in ("queued", "active", "completed", "attention")},
        _format_bytes=gui.DownloaderApp._format_bytes,
        _format_rate=gui.DownloaderApp._format_rate,
    )
    app._render_queue_row = lambda row: gui.DownloaderApp._render_queue_row(app, row)
    app._refresh_queue_summary = lambda: gui.DownloaderApp._refresh_queue_summary(app)

    gui.DownloaderApp._refresh_queue_view(app)
    assert tuple(app.table.rows) == ("3296316642",)
    assert len(app.queue_rows) == 3
    assert {key: value.get() for key, value in app.summary_vars.items()} == {
        "queued": "1", "active": "1", "completed": "0", "attention": "1",
    }

    app.queue_search_var.set("")
    app.queue_status_filter_var.set("Active")
    gui.DownloaderApp._refresh_queue_view(app)
    assert tuple(app.table.rows) == ("123456789",)
    assert len(app.queue_rows) == 3
    assert {key: value.get() for key, value in app.summary_vars.items()} == {
        "queued": "1", "active": "1", "completed": "0", "attention": "1",
    }


def test_unknown_size_disk_preflight_does_not_prompt_or_block_downloads(tmp_path, monkeypatch):
    pytest.importorskip("tkinter")
    from types import SimpleNamespace

    import bo3wd.gui as gui
    from bo3wd.models import WorkshopItem

    class Variable:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    monkeypatch.setattr(
        "bo3wd.preflight.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=16 * 1024**3, used=1 * 1024**3, free=15 * 1024**3),
    )
    monkeypatch.setattr(
        gui.messagebox, "askyesno",
        lambda *_args, **_kwargs: pytest.fail("unknown size with sufficient free space must not block"),
    )
    app = SimpleNamespace(
        status_var=Variable(),
        _format_bytes=gui.DownloaderApp._format_bytes,
        _format_rate=gui.DownloaderApp._format_rate,
    )

    assert gui.DownloaderApp._confirm_disk_space(
        app, [WorkshopItem("3296316642", expected_bytes=0)],
        tmp_path / "steamcmd.exe", operation="download",
    )
    assert "downloads are not blocked" in app.status_var.value
