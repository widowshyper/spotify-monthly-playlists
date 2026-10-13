# Spotify Monthly Playlists

A small desktop app that turns your Spotify **Liked Songs** into monthly playlists.

For any month, it finds every track you liked during that month and puts them into a
playlist called `<Month> <Year>` (e.g. **October 2026**), ordered by when you liked
them, oldest first.

- **Preview** a month before changing anything.
- **Create / Update** a single month. Re-running is safe: if a playlist with that exact
  name already exists (and you own it), only missing tracks are added. Playlists are
  never duplicated and tracks are never removed.
- **Backfill all months** creates or updates a playlist for every month that has liked
  songs. It asks for confirmation first and shows a progress bar.
- **`--auto` mode** runs headless for scheduled runs. It syncs last month and the current
  month, then exits.
- Dates are converted from UTC to **your computer's local timezone** before grouping.
  For example, a song liked at 9am on 1 November in Tasmania goes into *November*,
  not October.

Built with Python, CustomTkinter and plain `requests` calls to the Spotify Web API. It
uses the endpoints that are current after Spotify's February 2026 changes.

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

## 4. Schedule it with Windows Task Scheduler

The example below runs at **9:00 am on the 1st of every month**. Replace the path with
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

Because `--auto` also syncs the current month, you can add a weekly or daily trigger
too, to keep this month's playlist growing as you like songs.

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
packaged exe and the Task Scheduler instructions are Windows-specific (use cron or
launchd elsewhere).

## 6. Building the .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

This runs the tests, then builds `dist\SpotifyMonthlyPlaylists.exe`, a single file with
no console window. It uses PyInstaller with `--onefile --windowed --collect-data customtkinter`.

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Entry point: GUI, or `--auto` with file logging |
| `gui.py` | CustomTkinter UI. API work runs on a background thread. |
| `auth.py` | Authorization Code + PKCE flow, loopback callback server, token cache and refresh |
| `spotify_client.py` | Minimal Web API client: pagination, 429 `Retry-After`, 401 refresh, friendly errors |
| `playlist_logic.py` | Pure functions: parsing, timezone-aware month bucketing, pagination cutoff, dedupe |
| `sync.py` | Find-or-create, append-only sync, preview, backfill, auto |
| `config.py` | App-data paths and the Client ID config |
| `errors.py` | User-facing exception types |
| `tests/` | pytest suite |

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
