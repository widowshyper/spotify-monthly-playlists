"""CustomTkinter desktop UI. All Spotify work runs on a background thread;
results come back to the Tk thread through a queue polled with `after()`."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import traceback
import webbrowser
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

import config
import playlist_logic as pl
import scheduler
from auth import SpotifyAuth
from errors import AuthError, ConfigError, SpotifyAppError
from spotify_client import SpotifyClient
from sync import BackfillPlan, Cancelled, PlaylistSyncer, Preview, SyncResult

log = logging.getLogger("gui")

FIRST_YEAR = 2008  # Spotify launched in 2008
MONO = "Consolas" if sys.platform == "win32" else "Courier"



class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master: "App", current: str | None):
        super().__init__(master)
        self.title("Settings")
        self.geometry("520x260")
        self.resizable(False, False)
        self.result: str | None = None

        ctk.CTkLabel(self, text="Spotify Client ID", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
        self.entry = ctk.CTkEntry(self, width=480, placeholder_text="Paste the Client ID from the Spotify Developer Dashboard")
        self.entry.pack(padx=16)
        if current:
            self.entry.insert(0, current)

        help_text = (
            "In your Spotify app's settings, add this exact Redirect URI:\n"
            f"    {config.REDIRECT_URI}\n"
            "and tick 'Web API'. No client secret is needed."
        )
        ctk.CTkLabel(self, text=help_text, justify="left").pack(anchor="w", padx=16, pady=12)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(row, text="Copy Redirect URI", width=150, command=self._copy).pack(side="left")
        ctk.CTkButton(row, text="Save", width=100, command=self._save).pack(side="right")
        ctk.CTkButton(row, text="Cancel", width=100, fg_color="gray40", command=self.destroy).pack(side="right", padx=8)

        self.after(50, self._focus)

    def _focus(self):
        self.lift()
        self.grab_set()
        self.entry.focus_set()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(config.REDIRECT_URI)

    def _save(self):
        value = self.entry.get().strip()
        if not value or not value.isalnum():
            messagebox.showerror("Settings", "That doesn't look like a Client ID (letters and digits only).", parent=self)
            return
        self.result = value
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("system")
        self.title("Spotify Monthly Playlists")
        self.geometry("900x840")
        self.minsize(780, 680)

        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self._showing_progress = False
        self.cancel_event = threading.Event()
        self.auth: SpotifyAuth | None = None
        self.client: SpotifyClient | None = None
        self.user: dict | None = None

        self._build()
        self.after(100, self._poll)
        self.after(200, self._startup)

    # ----- layout -----------------------------------------------------
    def _build(self):
        pad = {"padx": 12, "pady": 6}

        top = ctk.CTkFrame(self)
        top.pack(fill="x", **pad)
        self.status = ctk.CTkLabel(top, text="Not connected", anchor="w")
        self.status.pack(side="left", padx=12, pady=10)
        self.settings_btn = ctk.CTkButton(top, text="Settings", width=90, fg_color="gray40", command=self.open_settings)
        self.settings_btn.pack(side="right", padx=(6, 12))
        self.connect_btn = ctk.CTkButton(top, text="Connect", width=120, command=self.toggle_connect)
        self.connect_btn.pack(side="right")

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", **pad)
        cur_y, cur_m = pl.current_month()
        ctk.CTkLabel(controls, text="Month").pack(side="left", padx=(12, 4), pady=10)
        self.month_var = ctk.StringVar(value=pl.MONTH_NAMES[cur_m - 1])
        ctk.CTkOptionMenu(controls, values=pl.MONTH_NAMES, variable=self.month_var, width=130).pack(side="left")
        ctk.CTkLabel(controls, text="Year").pack(side="left", padx=(16, 4))
        self.year_var = ctk.StringVar(value=str(cur_y))
        years = [str(y) for y in range(cur_y, FIRST_YEAR - 1, -1)]
        ctk.CTkOptionMenu(controls, values=years, variable=self.year_var, width=90).pack(side="left")
        self.public_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(controls, text="Make new playlists public", variable=self.public_var).pack(side="left", padx=20)

        actions = ctk.CTkFrame(self)
        actions.pack(fill="x", **pad)
        self.preview_btn = ctk.CTkButton(actions, text="Preview", command=self.do_preview)
        self.sync_btn = ctk.CTkButton(actions, text="Create / Update Playlist", command=self.do_sync)
        self.backfill_btn = ctk.CTkButton(actions, text="Backfill all months", command=self.do_backfill)
        self.cancel_btn = ctk.CTkButton(actions, text="Cancel", width=90, fg_color="gray40", command=self.cancel)
        for b in (self.preview_btn, self.sync_btn, self.backfill_btn):
            b.pack(side="left", padx=(12, 0), pady=10)
        self.cancel_btn.pack(side="right", padx=12)

        self._build_schedule(pad)

        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.pack(fill="x", padx=12)
        self.progress = ctk.CTkProgressBar(prog)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.progress_label = ctk.CTkLabel(prog, text="", width=160, anchor="e")
        self.progress_label.pack(side="right")

        ctk.CTkLabel(self, text="Preview", anchor="w", font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=16, pady=(8, 0))
        self.preview_box = ctk.CTkTextbox(self, font=ctk.CTkFont(family=MONO, size=12), wrap="none")
        self.preview_box.pack(fill="both", expand=True, padx=12, pady=(2, 6))
        self.preview_box.configure(state="disabled")

        ctk.CTkLabel(self, text="Log", anchor="w", font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=16)
        self.log_box = ctk.CTkTextbox(self, height=160, font=ctk.CTkFont(family=MONO, size=12))
        self.log_box.pack(fill="both", padx=12, pady=(2, 12))
        self.log_box.configure(state="disabled")

        self._refresh_buttons()

    def _build_schedule(self, pad: dict):
        frame = ctk.CTkFrame(self)
        frame.pack(fill="x", **pad)
        saved = scheduler.saved_schedule()

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row, text="Auto-sync every", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(12, 6))
        self.every_var = ctk.StringVar(value=str(saved.every))
        self.every_entry = ctk.CTkEntry(row, textvariable=self.every_var, width=48, justify="center")
        self.every_entry.pack(side="left")
        self.unit_var = ctk.StringVar(value=saved.unit)
        self.unit_menu = ctk.CTkOptionMenu(row, values=scheduler.UNITS, variable=self.unit_var, width=100)
        self.unit_menu.pack(side="left", padx=6)
        ctk.CTkLabel(row, text="at").pack(side="left", padx=(4, 6))
        self.at_var = ctk.StringVar(value=saved.at)
        self.at_entry = ctk.CTkEntry(row, textvariable=self.at_var, width=64, justify="center")
        self.at_entry.pack(side="left")

        self.sched_log_btn = ctk.CTkButton(row, text="Open auto log", width=110, fg_color="gray40", command=self.open_auto_log)
        self.sched_log_btn.pack(side="right", padx=(6, 12))
        self.sched_off_btn = ctk.CTkButton(row, text="Turn off", width=80, fg_color="gray40", command=self.schedule_off)
        self.sched_off_btn.pack(side="right", padx=(6, 0))
        self.sched_run_btn = ctk.CTkButton(row, text="Run now", width=80, fg_color="gray40", command=self.schedule_run_now)
        self.sched_run_btn.pack(side="right", padx=(6, 0))
        self.sched_save_btn = ctk.CTkButton(row, text="Save schedule", width=110, command=self.schedule_save)
        self.sched_save_btn.pack(side="right")

        self.sched_status = ctk.CTkLabel(frame, text="Checking schedule...", anchor="w", justify="left", wraplength=720)
        self.sched_status.pack(fill="x", padx=12, pady=(4, 8))

        if not scheduler.is_supported():
            for w in (self.every_entry, self.unit_menu, self.at_entry, self.sched_save_btn,
                      self.sched_run_btn, self.sched_off_btn):
                w.configure(state="disabled")
            self.sched_status.configure(
                text="Automatic scheduling is built in on Windows only. Elsewhere, run "
                     "'main.py --auto' from cron or launchd."
            )
        else:
            self._set_schedule_buttons(exists=False)

    # ----- helpers ----------------------------------------------------
    def _set_text(self, box: ctk.CTkTextbox, text: str):
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def append_log(self, msg: str):
        log.info(msg)
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def log_from_worker(self, msg: str):
        self.events.put(("log", msg))

    def _refresh_buttons(self):
        connected = self.user is not None
        work_state = "normal" if connected and not self.busy else "disabled"
        for b in (self.preview_btn, self.sync_btn, self.backfill_btn):
            b.configure(state=work_state)
        self.connect_btn.configure(state="disabled" if self.busy else "normal", text="Disconnect" if connected else "Connect")
        self.settings_btn.configure(state="disabled" if self.busy else "normal")
        self.cancel_btn.configure(state="normal" if self.busy else "disabled")
        if connected:
            name = self.user.get("display_name") or self.user.get("id")
            self.status.configure(text=f"Signed in as {name}")
        else:
            self.status.configure(text="Not connected")

    def selected_month(self) -> tuple[int, int]:
        return int(self.year_var.get()), pl.MONTH_NAMES.index(self.month_var.get()) + 1

    def new_syncer(self) -> PlaylistSyncer:
        assert self.client is not None
        return PlaylistSyncer(self.client, log=self.log_from_worker, user=self.user)

    # ----- background work --------------------------------------------
    def run_bg(self, label: str, work: Callable[[], Any], on_done: Callable[[Any], None] | None = None):
        if self.busy:
            return
        self.busy = True
        self._showing_progress = False
        self.cancel_event.clear()
        self.progress_label.configure(text=label)
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._refresh_buttons()

        def target():
            try:
                result = work()
                self.events.put(("finished", on_done, result, None, None))
            except BaseException as exc:  # report everything to the UI thread
                self.events.put(("finished", None, None, exc, traceback.format_exc()))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.append_log(ev[1])
                elif kind == "progress":
                    i, n = ev[1], ev[2]
                    self._showing_progress = True
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(i / n if n else 1)
                    self.progress_label.configure(text=f"{i} / {n} months")
                elif kind == "call":
                    ev[1](ev[2])
                elif kind == "finished":
                    _, cb, result, exc, tb = ev
                    # Go idle *before* the callback so it can start a follow-up job.
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    if not self._showing_progress:  # keep a finished backfill's bar visible
                        self.progress.set(0)
                        self.progress_label.configure(text="")
                    self._refresh_buttons()
                    if exc is not None:
                        self._handle_error(exc, tb)
                    elif cb:
                        cb(result)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle_error(self, exc: BaseException, tb: str):
        if isinstance(exc, Cancelled):
            return
        if isinstance(exc, SpotifyAppError):
            msg = str(exc)
            log.warning("%s", msg)
        else:
            msg = f"Unexpected error: {exc}"
            log.error("Unexpected error\n%s", tb)
        self.append_log("ERROR: " + msg)
        if isinstance(exc, AuthError) and self.auth is not None and not self.auth.is_logged_in:
            self.user = None
            self._refresh_buttons()
        messagebox.showerror("Spotify Monthly Playlists", msg, parent=self)

    def cancel(self):
        self.cancel_event.set()
        self.append_log("Cancelling...")

    def run_side(self, work: Callable[[], Any], on_done: Callable[[Any], None]):
        """Quick background job that doesn't lock the UI (used for Task Scheduler calls)."""
        def target():
            try:
                self.events.put(("call", on_done, work()))
            except Exception as exc:
                self.events.put(("call", self._on_schedule_error, exc))

        threading.Thread(target=target, daemon=True).start()

    # ----- automatic sync schedule ------------------------------------
    def _set_schedule_buttons(self, exists: bool):
        self.sched_run_btn.configure(state="normal" if exists else "disabled")
        self.sched_off_btn.configure(state="normal" if exists else "disabled")
        self.sched_save_btn.configure(text="Update schedule" if exists else "Save schedule")

    def refresh_schedule_status(self):
        if scheduler.is_supported():
            self.run_side(scheduler.status, self._show_schedule_status)

    def _show_schedule_status(self, st: scheduler.TaskStatus):
        self._set_schedule_buttons(st.exists)
        if not st.exists:
            self.sched_status.configure(text="Off. Choose how often to sync and click Save schedule. "
                                             "Runs even when this app is closed.")
            return
        parts = [f"On: syncs {scheduler.saved_schedule().describe()}."]
        if st.status.lower() == "disabled":
            parts.append("(Task is disabled in Task Scheduler.)")
        elif st.next_run:
            parts.append(f"Next run: {st.next_run}.")
        if st.last_result and st.last_result != "has not run yet":
            parts.append(f"Last run: {st.last_run} ({st.last_result}).")
        self.sched_status.configure(text="  ".join(parts))

    def _on_schedule_error(self, exc: Exception):
        msg = str(exc)
        self.append_log("ERROR: " + msg)
        messagebox.showerror("Automatic sync", msg, parent=self)
        self.refresh_schedule_status()

    def _read_schedule(self) -> scheduler.Schedule | None:
        try:
            every = int(self.every_var.get().strip())
        except ValueError:
            messagebox.showerror("Automatic sync", "Enter a whole number for how often to sync.", parent=self)
            return None
        s = scheduler.Schedule(every, self.unit_var.get(), self.at_var.get().strip())
        try:
            s.validate()
        except scheduler.ScheduleError as exc:
            messagebox.showerror("Automatic sync", str(exc), parent=self)
            return None
        return s

    def schedule_save(self):
        s = self._read_schedule()
        if s is None:
            return
        if self.user is None:
            self.append_log("Note: scheduled runs use your saved login. Click Connect at least once or they will fail.")

        def done(registered: scheduler.Schedule):
            program, args, _wd = scheduler.launch_command()
            self.append_log(f"Automatic sync scheduled {registered.describe()}.")
            self.append_log(f"Task runs: {program} {args}. If you move the app, click Update schedule again.")
            self.refresh_schedule_status()

        self.run_side(lambda: scheduler.install(s), done)

    def schedule_off(self):
        def done(removed):
            self.append_log("Automatic sync turned off." if removed else "Automatic sync was already off.")
            self.refresh_schedule_status()

        self.run_side(scheduler.remove, done)

    def schedule_run_now(self):
        def done(_):
            self.append_log("Started a scheduled sync in the background. Results go to the auto log.")
            self.after(4000, self.refresh_schedule_status)

        self.run_side(scheduler.run_now, done)

    def open_auto_log(self):
        path = config.log_path("auto.log")
        if not path.exists():
            messagebox.showinfo("Automatic sync", "No automatic sync has run yet, so there's no log.", parent=self)
            return
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    # ----- startup / settings / auth ----------------------------------
    def _startup(self):
        self.refresh_schedule_status()
        if not config.get_client_id():
            self.append_log("Welcome! Enter your Spotify Client ID to get started.")
            self.open_settings()
        else:
            self._init_auth()

    def _init_auth(self):
        cid = config.get_client_id()
        self.user = None
        self.auth = self.client = None
        if not cid:
            self._refresh_buttons()
            return
        try:
            self.auth = SpotifyAuth(cid)
        except ConfigError as exc:
            self.append_log(f"ERROR: {exc}")
            return
        self.client = SpotifyClient(self.auth, on_wait=self.log_from_worker)
        if self.auth.is_logged_in:
            self.run_bg("Checking session...", self.client.me, self._on_user)
        else:
            self._refresh_buttons()

    def open_settings(self):
        dlg = SettingsDialog(self, config.get_client_id())
        self.wait_window(dlg)
        if dlg.result and dlg.result != config.get_client_id():
            config.set_client_id(dlg.result)
            self.append_log("Client ID saved.")
            self._init_auth()

    def toggle_connect(self):
        if self.user is not None:
            assert self.auth is not None
            self.auth.logout()
            self.user = None
            self.append_log("Disconnected. Cached login removed.")
            self._refresh_buttons()
            return
        if self.auth is None:
            self.open_settings()
            if self.auth is None:
                return
        auth, client = self.auth, self.client
        assert client is not None

        def work():
            self.log_from_worker("Opening your browser to log in to Spotify...")
            auth.login(cancel=self.cancel_event)
            return client.me()

        self.run_bg("Waiting for browser login...", work, self._on_user)

    def _on_user(self, user: dict):
        self.user = user
        self.append_log(f"Signed in as {user.get('display_name') or user.get('id')}.")
        self._refresh_buttons()

    # ----- actions ----------------------------------------------------
    def do_preview(self):
        y, m = self.selected_month()
        syncer = self.new_syncer()
        self.run_bg("Loading preview...", lambda: syncer.preview(y, m), self._show_preview)

    def _show_preview(self, pv: Preview):
        lines = []
        if pv.existing_playlist:
            header = (f"{pv.name}: {len(pv.tracks)} liked songs, {len(pv.to_add)} new "
                      f"(playlist already exists, {len(pv.tracks) - len(pv.to_add)} already in it)")
        else:
            header = f"{pv.name}: {len(pv.to_add)} tracks would be added to a new playlist"
        lines.append(header)
        lines.append("")
        if not pv.to_add:
            lines.append("Nothing to add." if pv.tracks else "No liked songs in this month.")
        else:
            lines.append(f"{'#':>4}  {'Date liked':<16}  {'Title':<45}  Artist")
            lines.append("-" * 100)
            for i, t in enumerate(pv.to_add, start=1):
                when = pl.to_local(t.added_at).strftime("%Y-%m-%d %H:%M")
                lines.append(f"{i:>4}  {when:<16}  {_clip(t.name, 45):<45}  {t.artists}")
        self._set_text(self.preview_box, "\n".join(lines))
        self.append_log(header)

    def do_sync(self):
        y, m = self.selected_month()
        public = self.public_var.get()
        syncer = self.new_syncer()
        self.run_bg(f"Syncing {pl.playlist_name(y, m)}...", lambda: syncer.sync_one(y, m, public))

    def do_backfill(self):
        syncer = self.new_syncer()
        self.run_bg("Scanning Liked Songs...", syncer.plan_backfill, lambda plan: self._confirm_backfill(syncer, plan))

    def _confirm_backfill(self, syncer: PlaylistSyncer, plan: BackfillPlan):
        if not plan.months:
            messagebox.showinfo("Backfill", "No liked songs found.", parent=self)
            return
        first, last = pl.playlist_name(*plan.months[0]), pl.playlist_name(*plan.months[-1])
        msg = (
            f"Found liked songs in {len(plan.months)} months ({first} to {last}).\n\n"
            f"{len(plan.to_create)} new playlists will be created.\n"
            f"{len(plan.to_update)} existing playlists will be checked for new tracks.\n\n"
            "Continue?"
        )
        if not messagebox.askyesno("Backfill all months", msg, parent=self):
            self.append_log("Backfill cancelled.")
            return
        public = self.public_var.get()

        def work():
            return syncer.run_backfill(
                plan,
                public=public,
                progress=lambda i, n: self.events.put(("progress", i, n)),
                cancel=self.cancel_event,
            )

        self.run_bg("Backfilling...", work, self._on_backfilled)

    def _on_backfilled(self, results: list[SyncResult]):
        counts = {a: sum(r.action == a for r in results) for a in ("created", "updated", "unchanged", "skipped")}
        self.append_log(
            f"Backfill done: {counts['created']} created, {counts['updated']} updated, "
            f"{counts['unchanged']} already up to date."
        )


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def run() -> None:
    App().mainloop()
