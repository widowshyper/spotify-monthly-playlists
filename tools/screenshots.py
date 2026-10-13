"""Regenerate the README/guide screenshots in docs/screenshots/ (Windows only).

Runs the real GUI against an in-memory demo "Spotify" with made-up songs, so it
never touches a real account, the real app-data folder or the real Task
Scheduler. Windows are captured with the Win32 PrintWindow API.

    python tools/screenshots.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import random
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))
os.environ["SMP_APP_DIR"] = tempfile.mkdtemp(prefix="smp_screens_")

from PIL import Image  # noqa: E402

import config  # noqa: E402
import scheduler  # noqa: E402

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

# ----- demo data ---------------------------------------------------------------

ARTISTS = ["Juniper Vale", "Static Orchard", "Mira Kade", "The Low Tides", "Halcyon Drive",
           "Paper Lanterns", "Ostra", "Northbound Echo", "Velvet Signal", "Kiri & The Moths"]
WORDS_A = ["Neon", "Quiet", "Golden", "Silver", "Midnight", "Paper", "Electric", "Lazy", "Hollow", "Wild"]
WORDS_B = ["Harbour", "Satellites", "Summer", "Rivers", "Static", "Lanterns", "Weather", "Hearts", "Avenue", "Echoes"]


def demo_liked(now: datetime) -> list[dict]:
    rnd = random.Random(7)
    items, n = [], 0
    y, m = 2024, 11
    while (y, m) <= (now.year, now.month):
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        current = (y, m) == (now.year, now.month)
        span = (now - start - timedelta(hours=2)) if current else timedelta(days=28)  # never in the future
        for _ in range(12 if current else rnd.randint(5, 14)):
            n += 1
            when = start + span * rnd.random()
            items.append({
                "added_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "track": {
                    "uri": f"spotify:track:demo{n:04d}",
                    "name": f"{rnd.choice(WORDS_A)} {rnd.choice(WORDS_B)}",
                    "artists": [{"name": rnd.choice(ARTISTS)}],
                },
            })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    items.sort(key=lambda e: e["added_at"], reverse=True)  # API order: newest first
    return items


class DemoSpotify:
    def __init__(self, liked, playlists):
        self.liked, self.playlists, self.calls = liked, playlists, 0

    def me(self):
        return {"id": "demo", "display_name": "Demo User"}

    def iter_saved_tracks(self):
        for e in self.liked:
            time.sleep(0.002)  # let the progress spinner show
            yield e

    def iter_my_playlists(self):
        return iter([{k: v for k, v in p.items() if k != "uris"} for p in self.playlists])

    def iter_playlist_items(self, pid):
        p = next(p for p in self.playlists if p["id"] == pid)
        return iter([{"item": {"uri": u}} for u in p["uris"]])

    def create_playlist(self, name, public, description):
        p = {"id": f"pl{len(self.playlists)}", "name": name, "owner": {"id": "demo"}, "uris": []}
        self.playlists.append(p)
        return {k: v for k, v in p.items() if k != "uris"}

    def add_items(self, pid, uris):
        time.sleep(0.05)
        next(p for p in self.playlists if p["id"] == pid)["uris"].extend(uris)
        return len(uris)


# ----- window capture ----------------------------------------------------------

class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture(hwnd: int) -> tuple[Image.Image, wt.RECT]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT
    bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy(), r


def save(img: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / name, optimize=True)
    print("saved", OUT / name)


def tk_hwnd(win) -> int:
    return int(win.wm_frame(), 16)


# ----- script --------------------------------------------------------------------

def main() -> None:
    now = datetime.now(timezone.utc)
    liked = demo_liked(now)
    oct_uris = [e["track"]["uri"] for e in reversed(liked) if e["added_at"].startswith(now.strftime("%Y-%m"))]
    demo = DemoSpotify(liked, [
        # this month's playlist already exists with some of its songs -> "sync" mode in the preview
        {"id": "x1", "name": f"{now:%B %Y}", "owner": {"id": "demo"}, "uris": oct_uris[:4]},
        {"id": "x2", "name": "Road Trip", "owner": {"id": "demo"}, "uris": []},
    ])

    # Stub Task Scheduler: show a saved daily schedule without registering anything.
    sched = scheduler.Schedule(1, "days", "09:00")
    tomorrow = datetime.now() + timedelta(days=1)
    scheduler.is_supported = lambda: True
    scheduler.saved_schedule = lambda: sched
    scheduler.status = lambda *a, **k: scheduler.TaskStatus(
        True, "Ready", tomorrow.strftime("%d/%m/%Y 9:00:00 AM"), datetime.now().strftime("%d/%m/%Y 9:00:01 AM"), "OK")

    config.set_client_id("demo")
    import customtkinter as ctk
    import gui

    app = gui.App()
    ctk.set_appearance_mode("dark")

    def wait_idle(then, delay=300):
        app.after(delay, lambda: then() if not app.busy else wait_idle(then, 200))

    def step_preview():
        app.client, app.user = demo, demo.me()
        app._refresh_buttons()
        app.append_log("Signed in as Demo User.")
        app.do_preview()
        wait_idle(step_main_shot, 800)

    def step_main_shot():
        app.update()
        img, rect = capture(tk_hwnd(app))
        save(img, "main.png")
        # crop the schedule panel out of the same capture
        frame = app.sched_status.master
        x0, y0 = frame.winfo_rootx() - rect.left, frame.winfo_rooty() - rect.top
        save(img.crop((x0 - 6, y0 - 6, x0 + frame.winfo_width() + 6, y0 + frame.winfo_height() + 6)), "schedule.png")
        step_settings()

    def step_settings():
        dlg = gui.SettingsDialog(app, None)

        def shoot():
            dlg.update()
            save(capture(tk_hwnd(dlg))[0], "settings.png")
            dlg.grab_release()
            dlg.destroy()
            step_backfill()

        dlg.after(1200, shoot)

    def step_backfill():
        def answer_dialog():  # the confirm box is a native modal dialog; capture it, then click Yes
            for _ in range(200):
                hwnd = user32.FindWindowW(None, "Backfill playlists")
                if hwnd:
                    time.sleep(0.6)
                    save(capture(hwnd)[0], "backfill_confirm.png")
                    user32.PostMessageW(hwnd, 0x0111, 6, 0)  # WM_COMMAND, IDYES
                    return
                time.sleep(0.1)

        threading.Thread(target=answer_dialog, daemon=True).start()
        # show the "Range" option: January to June of the current year
        year = str(datetime.now().year)
        app.bf_mode.set("range")
        app.bf_month_var.set("January")
        app.bf_year_var.set(year)
        app.bf_end_month_var.set("June")
        app.bf_end_year_var.set(year)
        app._update_backfill_controls()
        app.do_backfill()
        wait_idle(step_backfill_shot, 1500)

    def step_backfill_shot():
        app.update()
        save(capture(tk_hwnd(app))[0], "backfill_done.png")
        app.destroy()

    app.after(1500, step_preview)
    app.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Screenshots use the Win32 PrintWindow API; run this on Windows.")
    main()
