"""Small dependency-free desktop UI; all downloads execute off the Tk main thread."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import time
import traceback
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .engine import DownloadEngine
from .identifiers import parse_workshop_list
from .metadata import MetadataError, WorkshopMetadataClient
from .models import DownloadEvent, DownloadPaths, DownloadPolicy, JobStatus, WorkshopItem
from .steamcmd import discover_steamcmd, read_saved_account
from .storage import JobHistory, SettingsStore, default_data_dir


class DownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("1120x750")
        self.minsize(920, 620)
        self.configure(bg="#111318")

        self.data_dir = default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_store = SettingsStore(self.data_dir / "settings.json")
        try:
            self.settings = self.settings_store.load()
        except RuntimeError as exc:
            messagebox.showerror("Settings error", str(exc))
            self.settings = dict(SettingsStore.DEFAULTS)
        self.history = JobHistory(self.data_dir / "history.sqlite3")
        self.recovered_jobs = self.history.recover_interrupted()
        self.events: queue.Queue[DownloadEvent | tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.running = False
        self.current_id: str | None = None
        self.last_log_path = ""

        steamcmd = self.settings.get("steamcmd") or ""
        if not steamcmd:
            found = discover_steamcmd()
            steamcmd = str(found) if found else ""
        self.steamcmd_var = tk.StringVar(value=steamcmd)
        account = self.settings.get("steam_user") or ""
        if not account and steamcmd and Path(steamcmd).is_file():
            account = read_saved_account(Path(steamcmd)) or ""
        self.account_var = tk.StringVar(value=account)
        self.validate_var = tk.BooleanVar(value=bool(self.settings.get("validate_downloads", True)))
        self.max_attempts_var = tk.StringVar(value=str(self.settings.get("max_attempts", 30)))
        self.timeout_var = tk.StringVar(value=str(self.settings.get("attempt_timeout_minutes", 15)))
        self.status_var = tk.StringVar(value="Ready · partial downloads are preserved")
        self.progress_text_var = tk.StringVar(value="No active download")
        self.progress_is_indeterminate = False
        self.item_entry_var = tk.StringVar()

        self._setup_style()
        self._build_ui()
        self._load_history()
        if self.recovered_jobs:
            self.status_var.set(
                f"Recovered {self.recovered_jobs} interrupted job(s) into the queue · partial files kept"
            )
        self.after(120, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background="#1b1e26", foreground="#e7e9ef", fieldbackground="#1b1e26", rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background="#252a35", foreground="#e7e9ef", relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#294a73")], foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground="#252a35", background="#252a35", foreground="#e7e9ef")

    def _label(self, parent: tk.Widget, text: str, **kwargs: object) -> tk.Label:
        # Allow individual labels to override defaults without passing duplicate Tk options.
        options: dict[str, object] = {"bg": "#111318", "fg": "#e7e9ef", "font": ("Segoe UI", 9)}
        options.update(kwargs)
        return tk.Label(parent, text=text, **options)

    def _button(self, parent: tk.Widget, text: str, command, *, accent: bool = False, width: int | None = None) -> tk.Button:
        kwargs: dict[str, object] = {"text": text, "command": command, "relief": "flat", "bd": 0,
            "padx": 12, "pady": 7, "cursor": "hand2", "font": ("Segoe UI", 9, "bold"),
            "bg": "#2e6bba" if accent else "#282d38", "fg": "#ffffff", "activebackground": "#397ecf" if accent else "#363d4c",
            "activeforeground": "#ffffff", "disabledforeground": "#7e8491"}
        if width is not None:
            kwargs["width"] = width
        return tk.Button(parent, **kwargs)

    def _entry(self, parent: tk.Widget, variable: tk.StringVar, *, width: int | None = None, show: str | None = None) -> tk.Entry:
        kwargs: dict[str, object] = {"textvariable": variable, "bg": "#20242d", "fg": "#f2f4f8", "insertbackground": "#ffffff",
            "relief": "flat", "highlightthickness": 1, "highlightbackground": "#363d49", "highlightcolor": "#3a82d2",
            "font": ("Segoe UI", 10)}
        if width is not None:
            kwargs["width"] = width
        if show is not None:
            kwargs["show"] = show
        return tk.Entry(parent, **kwargs)

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg="#111318")
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        header = tk.Frame(outer, bg="#111318")
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="BO3 WORKSHOP", font=("Segoe UI", 19, "bold"), bg="#111318", fg="#f4f6fa").pack(anchor="w")
        tk.Label(header, text="Resumable SteamCMD downloads · AppID 311210", font=("Segoe UI", 10), bg="#111318", fg="#929baa").pack(anchor="w", pady=(0, 7))
        self._label(header, "", anchor="w").pack(fill="x")

        settings = tk.LabelFrame(outer, text="  Setup  ", bg="#111318", fg="#8fa4bd", bd=1, relief="groove", font=("Segoe UI", 9, "bold"), labelanchor="nw")
        settings.pack(fill="x", pady=(0, 12), ipady=7)
        settings.columnconfigure(1, weight=1)
        self._label(settings, "SteamCMD executable").grid(row=0, column=0, sticky="w", padx=(12, 8), pady=5)
        self._entry(settings, self.steamcmd_var).grid(row=0, column=1, sticky="ew", pady=5)
        self._button(settings, "Browse…", self._browse_steamcmd).grid(row=0, column=2, padx=8, pady=5)
        self._label(settings, "Steam account name").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=5)
        self._entry(settings, self.account_var).grid(row=1, column=1, sticky="ew", pady=5)
        tk.Checkbutton(settings, text="Validate files", variable=self.validate_var, bg="#111318", fg="#d9dee8", selectcolor="#20242d", activebackground="#111318", activeforeground="#ffffff").grid(row=1, column=2, sticky="w", padx=8)
        row2 = tk.Frame(settings, bg="#111318")
        row2.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self._label(settings, "Retry budget").grid(row=2, column=0, sticky="w", padx=(12, 8), pady=5)
        self._entry(row2, self.max_attempts_var, width=6).pack(side="left")
        self._label(row2, "attempts · process timeout").pack(side="left", padx=(8, 6))
        self._entry(row2, self.timeout_var, width=5).pack(side="left")
        self._label(row2, "minutes · saved log/history").pack(side="left", padx=8)
        self._button(settings, "Save settings", self._save_settings).grid(row=2, column=3, sticky="e", padx=(8, 12), pady=5)

        add = tk.LabelFrame(outer, text="  Add Workshop items  ", bg="#111318", fg="#8fa4bd", bd=1, relief="groove", font=("Segoe UI", 9, "bold"), labelanchor="nw")
        add.pack(fill="x", pady=(0, 12), ipady=7)
        add.columnconfigure(0, weight=1)
        self._entry(add, self.item_entry_var).grid(row=0, column=0, sticky="ew", padx=(12, 7), pady=7, ipady=5)
        self._button(add, "Add IDs / URLs", self._add_items, accent=True).grid(row=0, column=1, padx=5, pady=7)
        self._button(add, "Import list…", self._import_list).grid(row=0, column=2, padx=(5, 12), pady=7)
        self._label(add, "Paste IDs or Steam Workshop URLs. One per line works best; duplicates are ignored.", fg="#929baa").grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 4))

        table_frame = tk.Frame(outer, bg="#111318")
        table_frame.pack(fill="both", expand=True)
        columns = ("id", "title", "status", "attempt", "progress", "received")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        labels = {"id": "Workshop ID", "title": "Title", "status": "Status", "attempt": "Attempt", "progress": "Progress", "received": "Downloaded data"}
        widths = {"id": 125, "title": 280, "status": 110, "attempt": 75, "progress": 100, "received": 190}
        for col in columns:
            self.table.heading(col, text=labels[col])
            self.table.column(col, width=widths[col], minwidth=70, stretch=(col == "title"))
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        progress_frame = tk.Frame(outer, bg="#111318")
        progress_frame.pack(fill="x", pady=(8, 0))
        tk.Label(
            progress_frame, textvariable=self.progress_text_var,
            bg="#111318", fg="#d1d9e6", anchor="w", font=("Segoe UI", 9, "bold")
        ).pack(fill="x")
        self.download_progress = ttk.Progressbar(
            progress_frame, orient="horizontal", mode="determinate", maximum=100
        )
        self.download_progress.pack(fill="x", pady=(5, 0))

        controls = tk.Frame(outer, bg="#111318")
        controls.pack(fill="x", pady=(12, 6))
        self.start_button = self._button(controls, "Install / Download queue", self._start_queue, accent=True)
        self.start_button.pack(side="left")
        self.stop_button = self._button(controls, "Stop current attempt", self._stop_queue)
        self.stop_button.pack(side="left", padx=7)
        self.stop_button.configure(state="disabled")
        self._button(controls, "Retry selected", self._retry_selected).pack(side="left", padx=7)
        self._button(controls, "Remove selected", self._remove_selected).pack(side="left", padx=7)
        self._button(controls, "Clear finished", self._clear_finished).pack(side="left", padx=7)
        self._button(controls, "Open item folder", self._open_item_folder).pack(side="right", padx=(7, 0))
        self._button(controls, "Open logs", self._open_logs).pack(side="right")
        tk.Label(outer, textvariable=self.status_var, bg="#111318", fg="#9ca8b9", anchor="w", font=("Segoe UI", 9)).pack(fill="x", pady=(2, 0))
        self.log_view = tk.Text(outer, height=5, bg="#0c0e12", fg="#b8c6d9", insertbackground="#ffffff", relief="flat", font=("Consolas", 9), wrap="word", state="disabled")
        self.log_view.pack(fill="x", pady=(8, 0))

    def _browse_steamcmd(self) -> None:
        chosen = filedialog.askopenfilename(title="Select SteamCMD", filetypes=[("SteamCMD", "steamcmd.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
        if chosen:
            self.steamcmd_var.set(chosen)
            if not self.account_var.get().strip():
                self.account_var.set(read_saved_account(Path(chosen)) or "")

    def _save_settings(self) -> bool:
        steamcmd = Path(self.steamcmd_var.get().strip()).expanduser()
        if not steamcmd.is_file():
            messagebox.showerror("SteamCMD not found", "Select the actual steamcmd.exe executable.")
            return False
        try:
            attempts = int(self.max_attempts_var.get())
            timeout = int(self.timeout_var.get())
            DownloadPolicy(max_attempts=attempts, attempt_timeout_seconds=timeout * 60)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid settings", f"Retry budget or timeout is invalid: {exc}")
            return False
        values = {
            "steamcmd": str(steamcmd.resolve()),
            "steam_user": self.account_var.get().strip(),
            "download_root": str(self.data_dir),
            "validate_downloads": bool(self.validate_var.get()),
            "max_attempts": attempts,
            "attempt_timeout_minutes": timeout,
            "initial_backoff_seconds": int(self.settings.get("initial_backoff_seconds", 3)),
            "max_backoff_seconds": int(self.settings.get("max_backoff_seconds", 45)),
            "max_stagnant_attempts": int(self.settings.get("max_stagnant_attempts", 8)),
            "theme": "dark",
        }
        try:
            self.settings_store.save(values)
        except OSError as exc:
            messagebox.showerror("Could not save settings", str(exc))
            return False
        self.settings = values
        self.status_var.set("Settings saved")
        return True

    def _add_items(self) -> None:
        try:
            ids = parse_workshop_list(self.item_entry_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid Workshop item", str(exc))
            return
        if not ids:
            return
        steam = self.steamcmd_var.get().strip()
        for item_id in ids:
            log_path = self.data_dir / "logs" / f"{item_id}.log"
            self.history.ensure_item(WorkshopItem(workshop_id=item_id, title=item_id), log_path)
            self._upsert_row(self.history.get(item_id) or {"workshop_id": item_id, "title": item_id, "status": "queued", "attempt": 0, "max_attempts": 0, "bytes_received": 0, "expected_bytes": 0})
        self.item_entry_var.set("")
        added_message = f"Added {len(ids)} item(s). Existing partial data will be retained."
        if self.running:
            added_message += " New items will start when you resume the queue after this batch."
        self.status_var.set(added_message)
        if steam and Path(steam).is_file():
            threading.Thread(target=self._lookup_metadata, args=(ids,), daemon=True, name="bo3wd-metadata").start()

    def _lookup_metadata(self, ids: list[str]) -> None:
        client = WorkshopMetadataClient()
        details = {}
        errors: list[str] = []
        for offset in range(0, len(ids), 100):
            try:
                details.update(client.fetch_many(ids[offset:offset + 100]))
            except MetadataError as exc:
                errors.append(str(exc))
        for item_id in ids:
            meta = details.get(item_id)
            if not meta:
                continue
            self.history.ensure_item(meta, self.data_dir / "logs" / f"{item_id}.log")
            self.events.put(("refresh", item_id))
        if errors:
            self.events.put(("message", "Steam metadata unavailable; IDs remain queued and downloadable."))

    def _import_list(self) -> None:
        chosen = filedialog.askopenfilename(title="Import Workshop ID list", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not chosen:
            return
        try:
            contents = Path(chosen).read_text(encoding="utf-8-sig")
            ids = parse_workshop_list(contents)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        self.item_entry_var.set("\n".join(ids))
        self._add_items()

    def _load_history(self) -> None:
        for row in reversed(self.history.list_jobs()):
            self._upsert_row(row)

    @staticmethod
    def _format_bytes(byte_count: int | float) -> str:
        value = max(0.0, float(byte_count))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1000 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1000
        return f"{value:.1f} TB"

    def _update_download_progress(self, event: DownloadEvent) -> None:
        stored = self.history.get(event.workshop_id) or {}
        received = max(0, int(event.bytes_received))
        expected = max(0, int(event.expected_bytes or stored.get("expected_bytes") or 0))
        speed = max(0.0, float(event.speed_bytes_per_second))
        eta = ""
        if event.eta_seconds is not None and event.eta_seconds >= 0:
            seconds = int(event.eta_seconds)
            eta = f" · ETA {seconds // 60}m {seconds % 60:02d}s"

        if event.status == JobStatus.DOWNLOADING:
            if expected > 0:
                if self.progress_is_indeterminate:
                    self.download_progress.stop()
                    self.progress_is_indeterminate = False
                self.download_progress.configure(mode="determinate", maximum=100)
                percent = min(100.0, received * 100.0 / expected)
                self.download_progress.configure(value=percent)
                data_text = f"{self._format_bytes(received)} / {self._format_bytes(expected)}"
                self.progress_text_var.set(
                    f"{event.workshop_id}: {percent:.1f}% · {data_text}"
                    + (f" · {self._format_bytes(speed)}/s" if speed > 0 else "")
                    + eta
                )
            else:
                self.download_progress.configure(mode="indeterminate")
                if not self.progress_is_indeterminate:
                    self.download_progress.start(12)
                    self.progress_is_indeterminate = True
                self.progress_text_var.set(
                    f"{event.workshop_id}: {self._format_bytes(received)} downloaded"
                    + (f" · {self._format_bytes(speed)}/s" if speed > 0 else "")
                    + " · waiting for Steam's total-size metadata"
                )
        elif event.status == JobStatus.PREPARING:
            if self.progress_is_indeterminate:
                self.download_progress.stop()
                self.progress_is_indeterminate = False
            self.download_progress.configure(mode="determinate", maximum=100, value=0)
            self.progress_text_var.set(f"{event.workshop_id}: preparing SteamCMD…")
        elif event.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            if self.progress_is_indeterminate:
                self.download_progress.stop()
                self.progress_is_indeterminate = False
            self.download_progress.configure(mode="determinate", maximum=100)
            if event.status == JobStatus.COMPLETED:
                self.download_progress.configure(value=100)
                self.progress_text_var.set(
                    f"{event.workshop_id}: download complete · {self._format_bytes(received)} downloaded"
                )
            else:
                if expected > 0:
                    self.download_progress.configure(value=min(100.0, received * 100.0 / expected))
                    data_text = f"{self._format_bytes(received)} / {self._format_bytes(expected)}"
                else:
                    data_text = f"{self._format_bytes(received)} received"
                self.progress_text_var.set(
                    f"{event.workshop_id}: {event.status.value} · {data_text}"
                )

    def _upsert_row(self, row: dict[str, object] | DownloadEvent) -> None:
        if isinstance(row, DownloadEvent):
            item_id = row.workshop_id
            stored = self.history.get(item_id) or {}
            data = {
                "workshop_id": item_id,
                "title": stored.get("title") or row.metadata.get("title") or item_id,
                "status": row.status.value,
                "attempt": row.attempt,
                "max_attempts": row.max_attempts,
                "bytes_received": row.bytes_received,
                "expected_bytes": row.expected_bytes or stored.get("expected_bytes") or 0,
                "message": row.message,
            }
        else:
            data = row
            item_id = str(data.get("workshop_id", ""))
        if not item_id:
            return
        status = str(data.get("status", "queued"))
        attempt = int(data.get("attempt", 0) or 0)
        max_attempts = int(data.get("max_attempts", 0) or 0)
        bytes_received = int(data.get("bytes_received", 0) or 0)
        expected_bytes = int(data.get("expected_bytes", 0) or 0)
        progress = f"{min(100, bytes_received * 100 // expected_bytes)}%" if expected_bytes else ("Downloading…" if status == JobStatus.DOWNLOADING.value else "—")
        received = (
            f"{self._format_bytes(bytes_received)} / {self._format_bytes(expected_bytes)}"
            if expected_bytes else f"{self._format_bytes(bytes_received)} received"
        )
        if isinstance(row, DownloadEvent) and row.speed_bytes_per_second > 0:
            received += f" · {self._format_bytes(row.speed_bytes_per_second)}/s"
        title = str(data.get("title") or item_id)
        title = title.replace("\n", " ")[:85]
        values = (item_id, title, status.replace("_", " ").title(), f"{attempt}/{max_attempts or '—'}", progress, received)
        if self.table.exists(item_id):
            self.table.item(item_id, values=values)
        else:
            self.table.insert("", "end", iid=item_id, values=values)
        if isinstance(row, DownloadEvent) and row.line:
            self._append_log(f"[{item_id} · try {row.attempt}] {row.line}")
        elif isinstance(row, DownloadEvent) and row.message:
            self._append_log(f"[{item_id}] {row.message}")

    def _start_queue(self) -> None:
        if self.running:
            return
        try:
            if not self._save_settings():
                return
            if not self.settings.get("steamcmd"):
                return
            pending = self.history.pending_items()
            if not pending:
                self.status_var.set("Queue is empty")
                return
            policy = self._policy()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Setup error", str(exc))
            return
        self.stop_event.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set(f"Queue running · {len(pending)} item(s) pending · retries preserve partial files")
        self.worker = threading.Thread(target=self._run_queue, args=(pending, policy), daemon=True, name="bo3wd-queue")
        self.worker.start()

    def _policy(self) -> DownloadPolicy:
        return DownloadPolicy(
            max_attempts=int(self.max_attempts_var.get()),
            attempt_timeout_seconds=int(self.timeout_var.get()) * 60,
            initial_backoff_seconds=int(self.settings.get("initial_backoff_seconds", 3)),
            max_backoff_seconds=int(self.settings.get("max_backoff_seconds", 45)),
            max_stagnant_attempts=int(self.settings.get("max_stagnant_attempts", 8)),
        )

    def _run_queue(self, pending: list[dict[str, object]], policy: DownloadPolicy) -> None:
        completion_message = "Queue paused" if self.stop_event.is_set() else "Queue finished"
        try:
            steamcmd = Path(str(self.settings["steamcmd"]))
            paths = DownloadPaths.from_steamcmd(steamcmd, self.data_dir)
            engine = DownloadEngine(
                paths,
                str(self.settings.get("steam_user", "")),
                policy=policy,
                validate=bool(self.settings.get("validate_downloads", True)),
                history=self.history,
                event_sink=self.events.put,
            )
            for planned_row in pending:
                if self.stop_event.is_set():
                    completion_message = "Queue paused"
                    break
                item_id = str(planned_row["workshop_id"])
                # Respect removals and retries made after the initial queue snapshot.
                row = self.history.get(item_id)
                if row is None or row.get("status") != JobStatus.QUEUED.value:
                    continue
                self.current_id = item_id
                item = WorkshopItem(
                    workshop_id=item_id,
                    title=str(row.get("title") or item_id),
                    expected_bytes=int(row.get("expected_bytes") or 0),
                    url=str(row.get("url") or f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}"),
                )
                engine.download(item, self.stop_event)
            else:
                completion_message = "Queue paused" if self.stop_event.is_set() else "Queue finished"
        except Exception as exc:
            completion_message = f"Queue stopped after an error: {type(exc).__name__}: {exc}"
            try:
                log_dir = self.data_dir / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with (log_dir / "application.log").open("a", encoding="utf-8") as log:
                    log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] queue worker error\n")
                    traceback.print_exc(file=log)
            except OSError:
                pass
        finally:
            self.events.put(("queue_done", completion_message))

    def _stop_queue(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("Stopping current SteamCMD attempt… partial files will be preserved")
        self.stop_button.configure(state="disabled")

    def _remove_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            return
        item_id = selected[0]
        row = self.history.get(item_id)
        active_statuses = {
            JobStatus.PREPARING.value, JobStatus.DOWNLOADING.value, JobStatus.RETRY_WAIT.value
        }
        if self.running and item_id == self.current_id:
            messagebox.showwarning("Item is active", "Stop the current attempt before removing its row.")
            return
        if row and row.get("status") in active_statuses:
            messagebox.showwarning("Item is active", "This job is active or waiting to retry; stop it before removing its row.")
            return
        self.history.delete(item_id)
        self.table.delete(item_id)
        self.status_var.set(f"Removed {item_id} from the queue/history. Download files were not deleted.")

    def _retry_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            return
        item_id = selected[0]
        if not self.history.requeue(item_id):
            messagebox.showwarning("Cannot retry", "This item is active or no longer exists in history.")
            return
        row = self.history.get(item_id)
        if row:
            self._upsert_row(row)
        self.status_var.set(f"{item_id} queued again. Existing partial data will be retained.")

    def _clear_finished(self) -> None:
        count = self.history.clear_finished()
        for item_id in list(self.table.get_children()):
            row = self.history.get(item_id)
            if row is None:
                self.table.delete(item_id)
        self.status_var.set(f"Cleared {count} finished history record(s). Downloaded/partial files were not deleted.")

    def _open_logs(self) -> None:
        folder = self.data_dir / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

    def _open_item_folder(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("Select a Workshop item", "Select an item in the queue first.")
            return
        steamcmd = Path(self.steamcmd_var.get().strip()).expanduser()
        if not steamcmd.is_file():
            messagebox.showerror("SteamCMD not found", "Select the actual SteamCMD executable first.")
            return
        item_id = selected[0]
        folder = steamcmd.parent / "steamapps" / "workshop" / "content" / "311210" / item_id
        if not folder.is_dir():
            messagebox.showinfo(
                "Content folder not found",
                f"SteamCMD has not created this item's content folder yet:\n{folder}\n\nPartial files, if any, are preserved separately.",
            )
            return
        self._open_path(folder)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def _append_log(self, text: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", text[:800] + "\n")
        lines = int(self.log_view.index("end-1c").split(".")[0])
        if lines > 180:
            self.log_view.delete("1.0", f"{lines - 150}.0")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, DownloadEvent):
                    self._upsert_row(event)
                    self._update_download_progress(event)
                    if event.speed_bytes_per_second > 0 and event.status == JobStatus.DOWNLOADING:
                        speed = event.speed_bytes_per_second / 1_000_000
                        eta = ""
                        if event.eta_seconds is not None:
                            seconds = int(event.eta_seconds)
                            eta = f" · ETA {seconds // 60}m {seconds % 60:02d}s"
                        self.status_var.set(f"{event.workshop_id}: {speed:.2f} MB/s{eta} · transfer rate is estimated")
                    elif event.message != "Transfer telemetry":
                        self.status_var.set(f"{event.workshop_id}: {event.message[:180]}")
                elif event[0] == "refresh":
                    row = self.history.get(event[1])
                    if row:
                        self._upsert_row(row)
                elif event[0] == "message":
                    self.status_var.set(event[1])
                elif event[0] == "queue_done":
                    self.running = False
                    self.current_id = None
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set(event[1] + " · data was not cleaned up")
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _close(self) -> None:
        if self.running:
            if not messagebox.askyesno("Stop downloader?", "Stop the active attempt? Partial data will be preserved."):
                return
            self.stop_event.set()
            self.after(250, self._wait_and_close)
            return
        self.destroy()

    def _wait_and_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.after(250, self._wait_and_close)
        else:
            self.destroy()


def launch() -> None:
    app = DownloaderApp()
    app.mainloop()
