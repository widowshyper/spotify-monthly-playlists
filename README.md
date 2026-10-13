# Spotify Monthly Playlists

> [!WARNING]
> **Disclaimer:** All code in this repository is 100% AI-generated. Review it before you
> rely on it, and use it at your own risk.

<img src="assets/icon.png" alt="" width="96" align="right">

**[Download the latest release](https://github.com/widowshyper/spotify-monthly-playlists/releases/latest)**
· **[User guide (with screenshots)](docs/USER_GUIDE.md)**
· **[MIT License](LICENSE)**

![Main window](docs/screenshots/main.png)

A small desktop app that turns your Spotify **Liked Songs** into monthly playlists.

For any month, it finds every track you liked during that month and puts them into a
playlist called `<Month> <Year>` (e.g. **October 2026**), ordered by when you liked
them, oldest first.

- **Preview** a month before changing anything.
- **Create / Update** a single month. Re-running is safe: if a playlist with that exact
  name already exists (and you own it), only missing tracks are added. Playlists are
  never duplicated and tracks are never removed.
- **Backfill** creates or updates a playlist for every month that has liked songs.
  Choose **all months**, **from** a chosen month onwards, or a **range** (start month
  to end month). It asks for confirmation first and shows a progress bar.
- **Auto-sync every X hours, days, weeks or months.** Set it once in the app and it
  keeps running via Windows Task Scheduler, even when the app is closed.
- **`--auto` mode** runs headless for scheduled runs. It syncs last month and the current
  month, then exits.
- Dates are converted from UTC to **your computer's local timezone** before grouping.
  For example, a song liked at 9am on 1 November in Tasmania goes into *November*,
  not October.

Built with Python, CustomTkinter and plain `requests` calls to the Spotify Web API. It
uses the endpoints that are current after Spotify's February 2026 changes.

New to the app? The **[user guide](docs/USER_GUIDE.md)** walks through every screen
step by step. The rest of this README is the reference for setup, automation and
building.

---

## 1. Spotify Developer Dashboard setup (one time)

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. Click **Create app** and fill in:
   - **App name / description:** anything, e.g. "Monthly Playlists".
   - **Redirect URI:** `http://127.0.0.1:8888/callback`. Copy this exactly. It must be
     the loopback IP `127.0.0.1`, not `localhost`.
   - **Which API/SDKs are you planning to use?** Tick **Web API**.
3. Save, open the app's **Settings** and copy the **Client ID**.
   You do **not** need the client secret. The app uses PKCE.

> **Development Mode notes (Spotify policy as of 2026)**
> - The app owner's Spotify account needs an active **Premium** subscription, or API
>   calls fail. The app shows "Premium subscription required" in this case.
> - Only users listed under **User Management** in the dashboard can log in (up to 5).
>   You're included automatically as the owner.

## 2. First run

1. Start `SpotifyMonthlyPlaylists.exe` (or `python main.py`, see below).
2. On first launch a **Settings** dialog asks for your **Client ID**. Paste it and click Save.
3. Click **Connect**. Your browser opens the Spotify login and consent page. Approve it,
   and the tab will say you're connected. The app now shows *Signed in as &lt;your name&gt;*.
4. Pick a month and year (the current month is the default), then click **Preview** or
   **Create / Update Playlist**. New playlists are **private** unless you tick
   *Make new playlists public*.

You only log in once. The app caches your token and refreshes it automatically.
**Disconnect** deletes the cached token.

### Where your data is stored

Nothing sensitive is stored in the project folder. Everything lives in your user
app-data folder:

| OS      | Folder |
|---------|--------|
| Windows | `%APPDATA%\SpotifyMonthlyPlaylists\` |
| macOS   | `~/Library/Application Support/SpotifyMonthlyPlaylists/` |
| Linux   | `~/.config/SpotifyMonthlyPlaylists/` |

| File        | Contents |
|-------------|----------|
| `config.json` | Your Client ID |
| `token.json`  | Spotify access and refresh token (treat like a password) |
| `app.log`     | GUI log |
| `auto.log`    | Log of `--auto` runs |

You can also supply the Client ID with the `SPOTIFY_CLIENT_ID` environment variable.

## 3. Headless `--auto` mode

```
SpotifyMonthlyPlaylists.exe --auto [--public]
```

This creates or updates the playlist for the **previous month** and also syncs the
**current month**, using the cached login. It writes to `auto.log` (see above) and exits.
It reads Liked Songs only back to the start of last month, so it's quick and uses few
API calls.

Exit codes: `0` success, `1` error (see the log), `2` not set up yet (no Client ID, or
you've never clicked Connect).

> You must run the GUI once and click **Connect** before `--auto` will work.

The exe is a windowed app, so it prints nothing to a terminal. To run it manually and
see the exit code:

```powershell
$p = Start-Process .\SpotifyMonthlyPlaylists.exe -ArgumentList --auto -Wait -PassThru; $p.ExitCode
```

## 4. Automatic sync every X hours, days, weeks or months

### Built in (recommended)

The **Auto-sync every** row in the app sets this up for you:

1. Enter a number and pick a unit (*hours*, *days*, *weeks* or *months*), plus a time
   (24-hour `HH:MM`). For example: `1 days at 09:00`, `6 hours at 08:00`, or
   `1 months at 09:00`.
   - **hours:** 1–23. Runs every N hours, starting at the time you give.
   - **days / weeks:** at that time. Weekly runs fall on the weekday of the first run.
   - **months:** runs on the **1st** at that time. Allowed intervals are 1, 2, 3, 4, 6
     or 12, so the schedule repeats evenly each year.
2. Click **Save schedule**. The app registers a Windows Task Scheduler task called
   `SpotifyMonthlyPlaylists\Auto Sync` that runs `SpotifyMonthlyPlaylists.exe --auto`.
   It runs even when the app is closed.
3. The status line shows the next run and the result of the last run. **Run now**
   triggers it immediately. **Open auto log** shows what happened. **Turn off**
   deletes the task.

How the task is set up:
- It runs as **you** and only while you're logged in. No password is stored.
- If the PC was off at run time, it runs as soon as possible afterwards.
- It runs on battery, waits for a network connection, and never starts a second copy.

If you **move the exe**, open the app and click **Update schedule** so the task points
at the new location. You must have clicked **Connect** at least once, or scheduled runs
fail with "not set up".

Every run syncs **last month and the current month**. So a daily schedule keeps this
month's playlist growing as you like songs, and still finishes off last month's
playlist after the month ends.

### Manually with Task Scheduler (alternative)

The examples below run at **9:00 am on the 1st of every month**. Replace the path with
wherever you keep the exe.

**Option A: one command (Command Prompt or PowerShell)**

```
schtasks /Create /TN "Spotify Monthly Playlists" /SC MONTHLY /D 1 /ST 09:00 /TR "\"C:\Apps\SpotifyMonthlyPlaylists.exe\" --auto"
```

Then open **Task Scheduler** and edit the task. Under **Settings**, tick **Run task as
soon as possible after a scheduled start is missed**, so it still runs if your PC was
off at 9am.

**Option B: Task Scheduler GUI**

1. Open **Task Scheduler** and click **Create Task...** (not *Basic Task*).
2. **General:** Name it "Spotify Monthly Playlists". Leave *Run only when user is
   logged on* selected. The task must run as **your** user, because the login is
   stored in your profile.
3. **Triggers:** New, then *Monthly*, *Months: Select all months*, *Days: 1*, start
   time 09:00.
4. **Actions:** New, then *Start a program*. Set *Program* to the full path of
   `SpotifyMonthlyPlaylists.exe` and *Add arguments* to `--auto`.
5. **Settings:** Tick *Run task as soon as possible after a scheduled start is missed*.
6. Click **OK**. To test it, right-click the task, click **Run**, then check `auto.log`.

## 5. Running from source

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python main.py          # GUI
.\.venv\Scripts\python main.py --auto   # headless
.\.venv\Scripts\python -m pytest        # tests
```

On macOS or Linux use `.venv/bin/python`. The code is cross-platform. Only the
packaged exe and the built-in scheduling are Windows-specific. Elsewhere the schedule
row is disabled, and you can run `main.py --auto` from cron or launchd instead.

## 6. Building the .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

This runs the tests, then builds `dist\SpotifyMonthlyPlaylists.exe`, a single file with
no console window. It uses PyInstaller with `--onefile --windowed --collect-data customtkinter`,
plus `--icon assets\icon.ico` and the `assets` folder bundled for the window icon.

### App icon

![App icon at 16, 32, 48 and 256 px](assets/icon_preview.png)

`assets/icon.ico` (16–256 px) and `assets/icon.png` are generated by
`tools/make_icon.py`, so edit the script and rerun it to change the design:

```powershell
.\.venv\Scripts\python tools\make_icon.py
```

Sizes below 48 px use a simplified note-only design so the icon stays readable in the
taskbar and title bar.

### Screenshots

The images in `docs/screenshots/` are generated by `tools/screenshots.py` (Windows only).
It runs the real GUI against an in-memory demo library of made-up songs, and never
touches a real Spotify account, your app-data folder or Task Scheduler. Rerun it after
changing the UI:

```powershell
.\.venv\Scripts\python tools\screenshots.py
```

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point: GUI, or `--auto` with file logging |
| `gui.py` | CustomTkinter UI. API work runs on a background thread. |
| `auth.py` | Authorization Code + PKCE flow, loopback callback server, token cache and refresh |
| `spotify_client.py` | Minimal Web API client: pagination, 429 `Retry-After`, 401 refresh, friendly errors |
| `playlist_logic.py` | Pure functions: parsing, timezone-aware month bucketing, pagination cutoff, dedupe |
| `sync.py` | Find-or-create, append-only sync, preview, backfill, auto |
| `scheduler.py` | Recurring auto sync: builds the Task Scheduler XML and wraps `schtasks` |
| `config.py` | App-data paths, the Client ID config and bundled-resource paths |
| `errors.py` | User-facing exception types |
| `assets/` | App icon (`icon.ico`, `icon.png`) |
| `tools/make_icon.py` | Regenerates the icon |
| `tools/screenshots.py` | Regenerates the screenshots with demo data |
| `docs/USER_GUIDE.md` | Step-by-step user guide with screenshots |
| `tests/` | pytest suite (`SMP_TEST_SCHTASKS=1` also runs a real Task Scheduler round trip with a disabled test task) |

### Spotify endpoints used

| Purpose | Endpoint |
|---------|----------|
| Current user | `GET /v1/me` |
| Liked Songs | `GET /v1/me/tracks?limit=50` (follows `next`, newest first, stops early) |
| Your playlists | `GET /v1/me/playlists?limit=50` |
| Create playlist | `POST /v1/me/playlists` |
| Read playlist | `GET /v1/playlists/{id}/items?limit=50` (entries use `item`, not `track`) |
| Add tracks | `POST /v1/playlists/{id}/items`, at most 100 URIs per request |

Scopes: `user-library-read playlist-read-private playlist-modify-private playlist-modify-public`.
Local files and entries with no item or URI are skipped.

## Troubleshooting

| Message | Fix |
|---------|-----|
| *Spotify rejected the Client ID* | Check the Client ID in Settings, and make sure the Redirect URI `http://127.0.0.1:8888/callback` is registered on the app. |
| *INVALID_CLIENT: Invalid redirect URI* (in browser) | The Redirect URI in the dashboard must match exactly, including `127.0.0.1` and `/callback`. |
| *Premium subscription required* | The app owner's account needs active Premium (Development Mode rule). |
| *Spotify refused the request (403)* | Add your account under User Management in the dashboard, then Disconnect and Connect again. |
| *Your Spotify session has expired* | Click Connect to log in again. |
| *Could not listen on 127.0.0.1:8888* | Something else is using port 8888, often another copy of this app. Close it and retry. |
| *Network error* | Check your internet connection and retry. |
| *Could not create the scheduled task* | Task Scheduler may be restricted on work-managed PCs. Try the manual Task Scheduler steps, or ask your IT admin. |
| Scheduled run shows *not set up* | Open the app and click **Connect** once. Scheduled runs reuse that login. |

## License

[MIT](LICENSE) © 2026 widowshyper. You're free to use, modify and share this code,
including commercially, as long as you keep the copyright and license notice. It's
provided "as is", without warranty.
