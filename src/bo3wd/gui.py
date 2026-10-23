"""Small dependency-free desktop UI; all downloads execute off the Tk main thread."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import time
import traceback
import threading
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .engine import DownloadEngine
from .identifiers import parse_workshop_list
from .integrity import IntegrityCheckError, discover_integrity_targets, write_integrity_report
from .metadata import MetadataError, WorkshopMetadataClient
from .models import DownloadEvent, DownloadPaths, DownloadPolicy, JobStatus, WorkshopItem
from .steamcmd import discover_steamcmd, read_saved_account
from .storage import JobHistory, SettingsStore, default_data_dir
from .updater import ReleaseInfo, check_for_update, download_verified_update, launch_self_update


class DownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("1240x840")
        self.minsize(960, 700)
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
        self.events: queue.Queue[DownloadEvent | tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.running = False
        self.update_in_progress = False
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
        self.progress_text_var = tk.StringVar(value="Ready · downloaded bytes and integrity-check progress appear here")
        self.progress_is_indeterminate = False
        self.item_entry_var = tk.StringVar()
        self.summary_vars = {
            "queued": tk.StringVar(value="0"),
            "active": tk.StringVar(value="0"),
            "completed": tk.StringVar(value="0"),
            "attention": tk.StringVar(value="0"),
        }

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
        style.configure(
            "Treeview", background="#151a23", foreground="#e7e9ef",
            fieldbackground="#151a23", rowheight=31, borderwidth=0,
            font=("Segoe UI", 9), padding=(5, 3),
        )
        style.configure(
            "Treeview.Heading", background="#252d3b", foreground="#eef3fb",
            relief="flat", font=("Segoe UI", 9, "bold"), padding=(8, 8),
        )
        style.map(
            "Treeview", background=[("selected", "#254a75")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Horizontal.TProgressbar", troughcolor="#283140",
            background="#4e9ef7", bordercolor="#283140", lightcolor="#4e9ef7",
            darkcolor="#4e9ef7", thickness=12,
        )
        style.configure(
            "Vertical.TScrollbar", background="#252d3b", troughcolor="#111318",
            bordercolor="#111318", arrowcolor="#b8c6d9",
        )
        style.configure(
            "Horizontal.TScrollbar", background="#252d3b", troughcolor="#111318",
            bordercolor="#111318", arrowcolor="#b8c6d9",
        )
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
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        # Header: the update action stays visible even when the queue toolbar is narrow.
        header = tk.Frame(outer, bg="#111318")
        header.pack(fill="x", pady=(0, 12))
        self.update_button = self._button(
            header, "Check for Updates", self._check_for_updates, accent=True
        )
        self.update_button.pack(side="right", anchor="ne", padx=(8, 0), pady=4)
        self.integrity_button = self._button(
            header, "Check File Integrity", self._check_file_integrity,
        )
        self.integrity_button.pack(side="right", anchor="ne", padx=(8, 0), pady=4)
        title_area = tk.Frame(header, bg="#111318")
        title_area.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_area,
            text="BO3 WORKSHOP DOWNLOADER",
            font=("Segoe UI", 20, "bold"),
            bg="#111318",
            fg="#f4f6fa",
        ).pack(anchor="w")
        tk.Label(
            title_area,
            text=f"Steam Workshop queue  ·  AppID 311210  ·  v{__version__}",
            font=("Segoe UI", 10),
            bg="#111318",
            fg="#91a0b5",
        ).pack(anchor="w", pady=(2, 0))

        # Live queue summary gives the user a clear signal that jobs were loaded.
        summary = tk.Frame(outer, bg="#111318")
        summary.pack(fill="x", pady=(0, 12))
        summary_cards = (
            ("QUEUED", "queued", "#75b9ff"),
            ("ACTIVE", "active", "#f2c46d"),
            ("COMPLETED", "completed", "#72d7a0"),
            ("NEEDS ATTENTION", "attention", "#ff8c8c"),
        )
        for column, (caption, key, tint) in enumerate(summary_cards):
            summary.columnconfigure(column, weight=1, uniform="summary")
            card = tk.Frame(
                summary, bg="#1a202b", highlightthickness=1,
                highlightbackground="#2a3342", bd=0,
            )
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 3 else 6))
            tk.Label(
                card, text=caption, bg="#1a202b", fg="#8795aa",
                font=("Segoe UI", 8, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(
                card, textvariable=self.summary_vars[key], bg="#1a202b", fg=tint,
                font=("Segoe UI", 19, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(0, 8))

        settings = tk.LabelFrame(
            outer, text="  STEAMCMD SETUP  ", bg="#111318", fg="#8fa4bd",
            bd=1, relief="groove", font=("Segoe UI", 9, "bold"), labelanchor="nw",
        )
        settings.pack(fill="x", pady=(0, 10), ipady=4)
        settings.columnconfigure(1, weight=1)
        self._label(settings, "SteamCMD executable").grid(
            row=0, column=0, sticky="w", padx=(12, 8), pady=5
        )
        self._entry(settings, self.steamcmd_var).grid(
            row=0, column=1, sticky="ew", pady=5
        )
        self._button(settings, "Browse…", self._browse_steamcmd).grid(
            row=0, column=2, padx=8, pady=5
        )
        self._label(settings, "Steam account name").grid(
            row=1, column=0, sticky="w", padx=(12, 8), pady=5
        )
        self._entry(settings, self.account_var).grid(
            row=1, column=1, sticky="ew", pady=5
        )
        tk.Checkbutton(
            settings, text="Validate files", variable=self.validate_var,
            bg="#111318", fg="#d9dee8", selectcolor="#20242d",
            activebackground="#111318", activeforeground="#ffffff",
        ).grid(row=1, column=2, sticky="w", padx=8)
        row2 = tk.Frame(settings, bg="#111318")
        row2.grid(row=2, column=1, columnspan=2, sticky="w", pady=(3, 0))
        self._label(settings, "Retry budget").grid(
            row=2, column=0, sticky="w", padx=(12, 8), pady=5
        )
        self._entry(row2, self.max_attempts_var, width=6).pack(side="left")
        self._label(row2, "attempts · process timeout").pack(side="left", padx=(8, 6))
        self._entry(row2, self.timeout_var, width=5).pack(side="left")
        self._label(row2, "minutes").pack(side="left", padx=8)
        self._button(settings, "Save Settings", self._save_settings).grid(
            row=2, column=3, sticky="e", padx=(8, 12), pady=5
        )

        add = tk.LabelFrame(
            outer, text="  ADD WORKSHOP DOWNLOADS  ", bg="#111318", fg="#8fa4bd",
            bd=1, relief="groove", font=("Segoe UI", 9, "bold"), labelanchor="nw",
        )
        add.pack(fill="x", pady=(0, 10), ipady=3)
        add.columnconfigure(0, weight=1)
        self.item_entry = self._entry(add, self.item_entry_var)
        self.item_entry.grid(
            row=0, column=0, sticky="ew", padx=(12, 7), pady=8, ipady=5
        )
        self.item_entry.bind("<Return>", lambda _event: self._add_items(start_after=True))
        self._button(add, "Add to Queue", self._add_items).grid(
            row=0, column=1, padx=5, pady=7
        )
        self._button(add, "Add + Start", lambda: self._add_items(start_after=True), accent=True).grid(
            row=0, column=2, padx=5, pady=7
        )
        self._button(add, "Import List…", self._import_list).grid(
            row=0, column=3, padx=(5, 12), pady=7
        )
        self._label(
            add,
            "Paste a Workshop ID or URL. Multiple entries can be separated by new lines.",
            fg="#929baa",
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 7))

        transfer = tk.Frame(
            outer, bg="#191f2a", highlightthickness=1, highlightbackground="#2a3342",
        )
        transfer.pack(fill="x", pady=(10, 8))
        tk.Label(
            transfer, text="LIVE DOWNLOAD DATA / INTEGRITY STATUS", bg="#191f2a", fg="#8795aa",
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(
            transfer, textvariable=self.progress_text_var, bg="#191f2a", fg="#edf3fb",
            anchor="w", font=("Segoe UI", 9, "bold"),
        ).pack(fill="x", padx=12, pady=(3, 4))
        self.download_progress = ttk.Progressbar(
            transfer, orient="horizontal", mode="determinate", maximum=100,
        )
        self.download_progress.pack(fill="x", padx=12, pady=(0, 12))

        controls = tk.LabelFrame(
            outer, text="  QUEUE CONTROLS  ", bg="#111318", fg="#8fa4bd",
            bd=1, relief="groove", font=("Segoe UI", 9, "bold"), labelanchor="nw",
        )
        controls.pack(fill="x", pady=(10, 0))
        primary_actions = tk.Frame(controls, bg="#111318")
        primary_actions.pack(fill="x", padx=5, pady=(5, 0))
        for column in range(4):
            primary_actions.columnconfigure(column, weight=1, uniform="primary-actions")
        self.start_button = self._button(
            primary_actions, "START / RESUME QUEUE", self._start_queue, accent=True,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.resume_selected_button = self._button(
            primary_actions, "RESUME SELECTED", self._resume_selected,
        )
        self.resume_selected_button.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self.resume_old_button = self._button(
            primary_actions, "RESUME OLD JOBS", self._resume_old_jobs,
        )
        self.resume_old_button.grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        self.stop_button = self._button(
            primary_actions, "PAUSE QUEUE", self._stop_queue,
        )
        self.stop_button.grid(row=0, column=3, sticky="ew", padx=4, pady=4)
        self.stop_button.configure(state="disabled")

        utility_actions = tk.Frame(controls, bg="#111318")
        utility_actions.pack(fill="x", padx=5, pady=(0, 5))
        for column in range(5):
            utility_actions.columnconfigure(column, weight=1, uniform="utility-actions")
        self._button(utility_actions, "Recover Partial Files", self._recover_partial_downloads).grid(
            row=0, column=0, sticky="ew", padx=4, pady=4
        )
        self._button(utility_actions, "Remove Selected", self._remove_selected).grid(
            row=0, column=1, sticky="ew", padx=4, pady=4
        )
        self._button(utility_actions, "Clear Finished", self._clear_finished).grid(
            row=0, column=2, sticky="ew", padx=4, pady=4
        )
        self._button(utility_actions, "Open Download Folder", self._open_item_folder).grid(
            row=0, column=3, sticky="ew", padx=4, pady=4
        )
        self._button(utility_actions, "Open Logs", self._open_logs).grid(
            row=0, column=4, sticky="ew", padx=4, pady=4
        )

        table_frame = tk.Frame(outer, bg="#111318")
        table_frame.pack(fill="both", expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("id", "title", "status", "attempt", "progress", "received")
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse",
        )
        labels = {
            "id": "WORKSHOP ID", "title": "WORKSHOP ITEM", "status": "STATUS",
            "attempt": "ATTEMPT", "progress": "PROGRESS", "received": "DOWNLOADED DATA",
        }
        widths = {"id": 125, "title": 280, "status": 115, "attempt": 75, "progress": 100, "received": 190}
        for col in columns:
            self.table.heading(col, text=labels[col])
            self.table.column(col, width=widths[col], minwidth=65, stretch=(col == "title"))
        self.table.tag_configure("queued", foreground="#9bcaff")
        self.table.tag_configure("preparing", foreground="#ffd887")
        self.table.tag_configure("downloading", foreground="#ffd887")
        self.table.tag_configure("retry_wait", foreground="#ffd887")
        self.table.tag_configure("completed", foreground="#83dfaa")
        self.table.tag_configure("failed", foreground="#ff9292")
        self.table.tag_configure("cancelled", foreground="#d1b5ff")
        self.table.bind("<Double-1>", lambda _event: self._resume_selected())
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        self.status_label = tk.Label(
            outer, textvariable=self.status_var, bg="#111318", fg="#a9b7ca",
            anchor="w", font=("Segoe UI", 9), wraplength=1150, justify="left",
        )
        self.status_label.pack(fill="x", pady=(7, 0))
        log_header = tk.Label(
            outer, text="ACTIVITY LOG", bg="#111318", fg="#8795aa",
            anchor="w", font=("Segoe UI", 8, "bold"),
        )
        log_header.pack(fill="x", pady=(7, 2))
        self.log_view = tk.Text(
            outer, height=4, bg="#0c0e12", fg="#b8c6d9",
            insertbackground="#ffffff", relief="flat", font=("Consolas", 9),
            wrap="word", state="disabled",
        )
        self.log_view.pack(fill="x")
    
    def _check_for_updates(self) -> None:
        """Check the stable GitHub Releases channel without blocking the Tk event loop."""
        if self.running:
            messagebox.showwarning(
                "Download in progress",
                "Stop or finish the active Workshop queue before updating the application.",
            )
            return
        self.update_button.configure(state="disabled")
        self.status_var.set(f"Checking stable releases for updates (installed v{__version__})…")
        threading.Thread(
            target=self._check_for_updates_worker,
            daemon=True,
            name="bo3wd-update-check",
        ).start()

    def _check_for_updates_worker(self) -> None:
        try:
            release = check_for_update(__version__)
        except Exception as exc:
            self.events.put(("update_check_failed", str(exc)))
        else:
            self.events.put(("update_check_complete", release))

    def _handle_update_check_complete(self, release: ReleaseInfo) -> None:
        self.update_button.configure(state="normal")
        if not release.update_available:
            if release.latest_version == __version__:
                self.status_var.set(f"Application is up to date · v{__version__}")
                messagebox.showinfo(
                    "No update available",
                    f"You're running the latest stable release, v{__version__}.",
                )
            else:
                self.status_var.set(
                    f"Installed v{__version__} is newer than latest stable release v{release.latest_version}"
                )
                messagebox.showwarning(
                    "Version check",
                    f"The installed version (v{__version__}) is newer than the latest stable release "
                    f"(v{release.latest_version}). No downgrade was performed.",
                )
            return

        if not getattr(sys, "frozen", False):
            self.status_var.set(f"Update v{release.latest_version} is available · source install")
            if messagebox.askyesno(
                "Update available",
                f"Version {release.latest_version} is newer than v{__version__}. "
                "This copy is running from source, so it cannot replace itself safely. "
                "Open the stable release page?",
            ):
                webbrowser.open(release.release_url)
            return

        if not messagebox.askyesno(
            "Update available",
            f"Installed version: v{__version__}\nLatest stable version: v{release.latest_version}\n\n"
            "Download the official release, verify its SHA-256 checksum, replace this executable, "
            "and restart the application?",
        ):
            self.status_var.set("Update cancelled · current version unchanged")
            return
        self.update_in_progress = True
        self.update_button.configure(state="disabled")
        self.status_var.set(f"Downloading and verifying v{release.latest_version}…")
        threading.Thread(
            target=self._download_and_apply_update_worker,
            args=(release,),
            daemon=True,
            name="bo3wd-update-download",
        ).start()

    def _download_and_apply_update_worker(self, release: ReleaseInfo) -> None:
        try:
            target = Path(sys.executable).resolve()
            staged = download_verified_update(
                release, target.parent, target_name=target.name
            )
            launch_self_update(staged, target, __version__)
        except Exception as exc:
            self.events.put(("update_failed", str(exc)))
        else:
            self.events.put(("update_ready", release.latest_version))

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

    def _add_items(self, *, start_after: bool = False) -> None:
        try:
            ids = parse_workshop_list(self.item_entry_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid Workshop item", str(exc))
            self.status_var.set(f"Could not add downloads: {exc}")
            return
        if not ids:
            self.status_var.set("Enter a Workshop ID or URL before adding a download.")
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
        self._refresh_queue_summary()
        if steam and Path(steam).is_file():
            threading.Thread(target=self._lookup_metadata, args=(ids,), daemon=True, name="bo3wd-metadata").start()
        if start_after:
            self.after(80, self._start_queue)

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
        elif event.status == JobStatus.RETRY_WAIT:
            if self.progress_is_indeterminate:
                self.download_progress.stop()
                self.progress_is_indeterminate = False
            self.download_progress.configure(mode="determinate", maximum=100)
            if expected > 0:
                percent = min(100.0, received * 100.0 / expected)
                self.download_progress.configure(value=percent)
                data_text = f"{self._format_bytes(received)} / {self._format_bytes(expected)}"
            else:
                self.download_progress.configure(value=0)
                data_text = f"{self._format_bytes(received)} received"
            self.progress_text_var.set(
                f"{event.workshop_id}: retrying · {data_text} retained"
            )
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
                    self.download_progress.configure(value=0)
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
        progress = (
            f"{min(100, bytes_received * 100 // expected_bytes)}%"
            if expected_bytes
            else ("Downloading…" if status == JobStatus.DOWNLOADING.value else "—")
        )
        received = (
            f"{self._format_bytes(bytes_received)} / {self._format_bytes(expected_bytes)}"
            if expected_bytes
            else f"{self._format_bytes(bytes_received)} received"
        )
        if isinstance(row, DownloadEvent) and row.speed_bytes_per_second > 0:
            received += f" · {self._format_bytes(row.speed_bytes_per_second)}/s"
        title = str(data.get("title") or item_id).replace("\n", " ")[:85]
        values = (
            item_id, title, status.replace("_", " ").title(),
            f"{attempt}/{max_attempts or '—'}", progress, received,
        )
        previous_status = None
        if self.table.exists(item_id):
            old_values = self.table.item(item_id, "values")
            if len(old_values) > 2:
                previous_status = str(old_values[2]).lower().replace(" ", "_")
            self.table.item(item_id, values=values, tags=(status,))
        else:
            self.table.insert("", "end", iid=item_id, values=values, tags=(status,))
        if previous_status != status:
            self._refresh_queue_summary()
        if isinstance(row, DownloadEvent) and row.line:
            self._append_log(f"[{item_id} · try {row.attempt}] {row.line}")
        elif isinstance(row, DownloadEvent) and row.message:
            self._append_log(f"[{item_id}] {row.message}")

    def _refresh_queue_summary(self) -> None:
        counts = {"queued": 0, "active": 0, "completed": 0, "attention": 0}
        for iid in self.table.get_children(""):
            values = self.table.item(iid, "values")
            if len(values) < 3:
                continue
            status = str(values[2]).lower().replace(" ", "_")
            if status == JobStatus.QUEUED.value:
                counts["queued"] += 1
            elif status in {
                JobStatus.PREPARING.value, JobStatus.DOWNLOADING.value, JobStatus.RETRY_WAIT.value,
            }:
                counts["active"] += 1
            elif status == JobStatus.COMPLETED.value:
                counts["completed"] += 1
            else:
                counts["attention"] += 1
        for key, count in counts.items():
            self.summary_vars[key].set(str(count))

    def _check_file_integrity(self) -> None:
        """Run SteamCMD's native validation against already-installed Workshop items."""
        if self.running or self.update_in_progress:
            messagebox.showwarning(
                "Downloader busy",
                "Finish or pause the current operation before starting an integrity check.",
            )
            return
        if not self._save_settings():
            return
        if not self.account_var.get().strip():
            messagebox.showerror(
                "Steam account missing",
                "Enter the Steam account name used by SteamCMD before checking file integrity.",
            )
            return
        try:
            paths = DownloadPaths.from_steamcmd(Path(str(self.settings["steamcmd"])), self.data_dir)
            selected = self.table.selection()
            selected_id = selected[0] if selected else None
            targets = discover_integrity_targets(
                paths, self.history.list_jobs(limit=5000), selected_id=selected_id,
            )
        except (IntegrityCheckError, OSError, ValueError, RuntimeError) as exc:
            messagebox.showwarning("Integrity check unavailable", str(exc))
            self.status_var.set(f"Integrity check not started: {exc}")
            return

        if selected_id:
            scope = f"selected Workshop item {selected_id}"
        else:
            scope = f"all {len(targets)} installed Workshop item(s)"
        approved = messagebox.askyesno(
            "Check File Integrity",
            f"Run SteamCMD's native validation for {scope}?\n\n"
            "SteamCMD will compare the installed Workshop files with its available source data. "
            "It may download or repair changed/missing files and requires a working connection. "
            "This is not a read-only scan. Existing partial-download cache files will not be deleted.\n\n"
            "Continue?",
        )
        if not approved:
            self.status_var.set("Integrity check cancelled; files were not touched.")
            return

        try:
            timeout_seconds = int(self.timeout_var.get()) * 60
        except ValueError as exc:
            messagebox.showerror("Invalid timeout", str(exc))
            return
        policy = DownloadPolicy(
            max_attempts=1,
            attempt_timeout_seconds=timeout_seconds,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            max_stagnant_attempts=1,
        )
        self.stop_event.clear()
        self.running = True
        self.current_id = None
        self.start_button.configure(state="disabled")
        self.resume_selected_button.configure(state="disabled")
        self.resume_old_button.configure(state="disabled")
        self.integrity_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress_text_var.set(f"Integrity check queued · {len(targets)} item(s)")
        self.status_var.set(
            f"Checking integrity of {len(targets)} installed Workshop item(s) with SteamCMD…"
        )
        self.worker = threading.Thread(
            target=self._run_integrity_check,
            args=(targets, paths, policy),
            daemon=True,
            name="bo3wd-integrity-check",
        )
        self.worker.start()

    def _run_integrity_check(
        self,
        targets: list[WorkshopItem],
        paths: DownloadPaths,
        policy: DownloadPolicy,
    ) -> None:
        started_at = time.time()
        results: list[dict[str, object]] = []
        try:
            engine = DownloadEngine(
                paths,
                str(self.settings.get("steam_user", "")),
                policy=policy,
                validate=True,
                history=self.history,
                event_sink=self.events.put,
            )
            for index, item in enumerate(targets, start=1):
                if self.stop_event.is_set():
                    results.append({
                        "workshop_id": item.workshop_id,
                        "title": item.title,
                        "status": "not_checked",
                        "message": "Stopped before this item was checked.",
                        "content_path": str(paths.content_root / item.workshop_id),
                    })
                    continue
                self.current_id = item.workshop_id
                self.events.put((
                    "message",
                    f"SteamCMD integrity validation · {index}/{len(targets)} · {item.workshop_id}",
                ))
                try:
                    result = engine.download(item, self.stop_event)
                    results.append({
                        "workshop_id": result.workshop_id,
                        "title": item.title,
                        "status": result.status.value,
                        "message": result.message,
                        "attempt": result.attempt,
                        "log_path": result.log_path,
                        "content_path": str(paths.content_root / item.workshop_id),
                    })
                    if result.status == JobStatus.CANCELLED:
                        # Mark remaining targets so the report distinguishes skipped items.
                        continue
                except Exception as exc:
                    results.append({
                        "workshop_id": item.workshop_id,
                        "title": item.title,
                        "status": "failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "content_path": str(paths.content_root / item.workshop_id),
                    })
        except Exception as exc:
            results.append({
                "workshop_id": "",
                "status": "failed",
                "message": f"Integrity worker error: {type(exc).__name__}: {exc}",
            })

        report_path = ""
        report_error = ""
        try:
            report_path = str(write_integrity_report(
                self.data_dir,
                started_at=started_at,
                finished_at=time.time(),
                results=results,
            ))
        except Exception as exc:
            report_error = f"Could not save the integrity report: {type(exc).__name__}: {exc}"
        self.events.put((
            "integrity_done",
            {
                "results": results,
                "report_path": report_path,
                "report_error": report_error,
                "stopped": self.stop_event.is_set(),
            },
        ))

    def _start_queue(self) -> None:
        if self.running or self.update_in_progress:
            return
        try:
            if not self._save_settings():
                self.status_var.set("Select your actual steamcmd.exe under SteamCMD Setup before starting downloads.")
                return
            pending = self.history.pending_items()
            if not pending:
                self.status_var.set(
                    "No queued jobs. Add a Workshop ID above, select an old item and choose Resume Selected, "
                    "or click Resume Old Jobs to retry failed/cancelled entries."
                )
                return
            policy = self._policy()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Setup error", str(exc))
            self.status_var.set(f"Could not start queue: {exc}")
            return
        self.stop_event.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.resume_selected_button.configure(state="disabled")
        self.resume_old_button.configure(state="disabled")
        self.integrity_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set(
            f"Queue running · {len(pending)} item(s) pending · partial downloads are preserved"
        )
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
        self._refresh_queue_summary()
        self.status_var.set(f"Removed {item_id} from the queue/history. Download files were not deleted.")

    def _recover_partial_downloads(self) -> None:
        """Rebuild queue rows from SteamCMD's on-disk partial Workshop directories."""
        steamcmd_text = self.steamcmd_var.get().strip()
        steamcmd = Path(steamcmd_text).expanduser()
        if not steamcmd.is_file():
            messagebox.showerror(
                "SteamCMD required",
                "Choose the actual steamcmd.exe under SteamCMD Setup before recovering partial downloads.",
            )
            self.status_var.set("Recovery needs a valid SteamCMD executable path.")
            return

        partial_root = steamcmd.resolve().parent / "steamapps" / "workshop" / "downloads" / "311210"
        try:
            item_ids = sorted(
                child.name
                for child in partial_root.iterdir()
                if child.is_dir() and child.name.isascii() and child.name.isdigit() and len(child.name) <= 20
            )
        except FileNotFoundError:
            item_ids = []
        except OSError as exc:
            messagebox.showerror("Could not scan partial downloads", str(exc))
            return

        if not item_ids:
            self.status_var.set(f"No partial Workshop folders found at {partial_root}. No files were changed.")
            messagebox.showinfo(
                "No partial downloads found",
                "No numeric Workshop-ID folders were found in SteamCMD's partial-download cache. "
                "The existing cache was not modified.",
            )
            return

        log_root = self.data_dir / "logs"
        for item_id in item_ids:
            try:
                self.history.ensure_item(
                    WorkshopItem(workshop_id=item_id, title=item_id),
                    log_root / f"{item_id}.log",
                )
            except ValueError:
                continue
        self._load_history()
        self.status_var.set(
            f"Recovered {len(item_ids)} partial Workshop folder(s) into the queue. "
            "SteamCMD cache files have not been altered."
        )
        if messagebox.askyesno(
            "Resume recovered downloads?",
            f"Found {len(item_ids)} partial Workshop folder(s). Add them to the queue and start all queued jobs now?\n\n"
            "Existing partial and completed files will be kept.",
        ):
            self._start_queue()

    def _resume_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo(
                "Select a download",
                "Select a queued, failed, cancelled, or completed Workshop item in the table first.",
            )
            return
        item_id = selected[0]
        if not self.history.requeue(item_id):
            messagebox.showwarning(
                "Cannot resume this item",
                "This download is currently active or no longer exists. Pause the queue before retrying it.",
            )
            return
        row = self.history.get(item_id)
        if row:
            self._upsert_row(row)
        self.status_var.set(
            f"Workshop item {item_id} queued to resume. Existing SteamCMD partial/download files are kept."
        )
        self._start_queue()

    def _resume_old_jobs(self) -> None:
        if self.running or self.update_in_progress:
            return
        count = self.history.requeue_retryable()
        self._load_history()
        pending = self.history.pending_items()
        if not count and not pending:
            self.status_var.set(
                "No queued, failed, or cancelled downloads to resume. Add an ID above or select a completed row."
            )
            return
        if count:
            self.status_var.set(
                f"Requeued {count} failed/cancelled old job(s). Existing partial data is preserved."
            )
        self._start_queue()

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
        self._refresh_queue_summary()
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
                    self.status_var.set(str(event[1]))
                elif event[0] == "update_check_complete":
                    self._handle_update_check_complete(event[1])  # type: ignore[arg-type]
                elif event[0] == "update_check_failed":
                    self.update_button.configure(state="normal")
                    self.status_var.set("Could not check for updates")
                    messagebox.showerror(
                        "Update check failed",
                        f"Could not check GitHub Releases. Your installed version was not changed.\n\n{event[1]}",
                    )
                elif event[0] == "update_failed":
                    self.update_in_progress = False
                    self.update_button.configure(state="normal")
                    self.status_var.set("Update failed · installed version unchanged")
                    messagebox.showerror(
                        "Update failed",
                        f"The update was not applied. Your installed version remains v{__version__}.\n\n{event[1]}",
                    )
                elif event[0] == "update_ready":
                    self.update_in_progress = False
                    self.status_var.set(f"Applying v{event[1]} · the application will restart")
                    messagebox.showinfo(
                        "Update verified",
                        f"Version {event[1]} downloaded and SHA-256 verified. "
                        "The app will close, replace its executable, and reopen. "
                        "A versioned backup of the previous executable will be retained.",
                    )
                    self.destroy()
                elif event[0] == "integrity_done":
                    summary = event[1]
                    self.running = False
                    self.current_id = None
                    self.start_button.configure(state="normal")
                    self.resume_selected_button.configure(state="normal")
                    self.resume_old_button.configure(state="normal")
                    self.integrity_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._load_history()
                    self._refresh_queue_summary()
                    results = summary.get("results", [])
                    verified = sum(str(item.get("status", "")).lower() == "completed" for item in results)
                    failed = sum(str(item.get("status", "")).lower() == "failed" for item in results)
                    cancelled = sum(str(item.get("status", "")).lower() == "cancelled" for item in results)
                    not_checked = sum(
                        str(item.get("status", "")).lower() not in {"completed", "failed", "cancelled"}
                        for item in results
                    )
                    report_path = str(summary.get("report_path") or "")
                    report_error = str(summary.get("report_error") or "")
                    report_line = f"\n\nReport: {report_path}" if report_path else ""
                    error_line = f"\n\n{report_error}" if report_error else ""
                    detail = (
                        f"SteamCMD integrity validation finished.\n\n"
                        f"Verified/repaired: {verified}\nFailed: {failed}\n"
                        f"Cancelled: {cancelled}\nNot checked: {not_checked}"
                        f"{report_line}{error_line}"
                    )
                    self.status_var.set(
                        f"Integrity check finished · verified {verified} · failed {failed} · "
                        f"cancelled {cancelled} · not checked {not_checked}"
                    )
                    if failed or cancelled or not_checked or report_error:
                        messagebox.showwarning("Integrity check results", detail)
                    else:
                        messagebox.showinfo("Integrity check complete", detail)
                elif event[0] == "queue_done":
                    self.running = False
                    self.current_id = None
                    self.start_button.configure(state="normal")
                    self.resume_selected_button.configure(state="normal")
                    self.resume_old_button.configure(state="normal")
                    self.integrity_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._load_history()
                    self._refresh_queue_summary()
                    self.status_var.set(str(event[1]) + " · partial/downloaded data was not cleaned up")
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _close(self) -> None:
        if self.update_in_progress:
            messagebox.showinfo(
                "Update in progress",
                "The update is being downloaded and verified. Let this operation finish; "
                "the app will offer to restart after verification.",
            )
            return
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
