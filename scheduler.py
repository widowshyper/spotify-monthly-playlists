"""Recurring automatic sync via Windows Task Scheduler.

The app registers a per-user scheduled task that runs `<app> --auto` every N
hours/days/weeks/months. Because Task Scheduler owns the timer, it keeps working
while the app is closed, and (via StartWhenAvailable) catches up on runs missed
while the PC was off.

The task is defined with Task Scheduler XML rather than plain `schtasks` flags,
because only the XML form can set StartWhenAvailable, the battery options and
"don't start a second copy".
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config

TASK_NAME = r"SpotifyMonthlyPlaylists\Auto Sync"
UNITS = ["hours", "days", "weeks", "months"]
MONTH_INTERVALS = (1, 2, 3, 4, 6, 12)  # must divide the year evenly
MAX_EVERY = {"hours": 23, "days": 365, "weeks": 52, "months": 12}

_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class ScheduleError(Exception):
    pass


@dataclass(frozen=True)
class Schedule:
    every: int = 1
    unit: str = "days"
    at: str = "09:00"  # local time HH:MM; for hours, the first run time

    def validate(self) -> None:
        if self.unit not in UNITS:
            raise ScheduleError(f"Unknown unit '{self.unit}'.")
        if not isinstance(self.every, int) or self.every < 1 or self.every > MAX_EVERY[self.unit]:
            raise ScheduleError(f"Enter a number from 1 to {MAX_EVERY[self.unit]} for {self.unit}.")
        if self.unit == "months" and self.every not in MONTH_INTERVALS:
            raise ScheduleError("For months, choose 1, 2, 3, 4, 6 or 12 (so it repeats evenly each year).")
        parse_time(self.at)

    def describe(self, now: datetime | None = None) -> str:
        n = self.every
        if self.unit == "hours":
            return f"every hour (from {self.at})" if n == 1 else f"every {n} hours (from {self.at})"
        if self.unit == "days":
            return f"every day at {self.at}" if n == 1 else f"every {n} days at {self.at}"
        if self.unit == "weeks":
            day = _WEEKDAYS[next_start(self, now).weekday()]
            return f"every {day} at {self.at}" if n == 1 else f"every {n} weeks on {day} at {self.at}"
        return f"on the 1st of every month at {self.at}" if n == 1 else f"on the 1st of every {n} months at {self.at}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Schedule":
        if not d:
            return cls()
        try:
            return cls(int(d.get("every", 1)), str(d.get("unit", "days")), str(d.get("at", "09:00")))
        except (TypeError, ValueError):
            return cls()


@dataclass
class TaskStatus:
    exists: bool
    status: str = ""
    next_run: str = ""
    last_run: str = ""
    last_result: str = ""


def is_supported() -> bool:
    return sys.platform == "win32"


def parse_time(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.strip().split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise ScheduleError("Enter the time as HH:MM, e.g. 09:00.") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ScheduleError("Enter the time as HH:MM, e.g. 09:00.")
    return h, m


def next_start(schedule: Schedule, now: datetime | None = None) -> datetime:
    """Next local datetime at the schedule's HH:MM (today if still ahead, else tomorrow)."""
    now = now or datetime.now()
    h, m = parse_time(schedule.at)
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return start if start > now else start + timedelta(days=1)


def month_list(every: int, now: datetime) -> list[int]:
    """Months (1-12) on whose 1st the task runs, starting with the next 1st."""
    first = now.month % 12 + 1  # next month's 1st is always ahead of 'now'
    return sorted({(first - 1 + k * every) % 12 + 1 for k in range(12 // every)})


def launch_command() -> tuple[str, str, str]:
    """(program, arguments, working dir) that starts this app in --auto mode."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        exe = Path(sys.executable).resolve()
        return str(exe), "--auto", str(exe.parent)
    main_py = Path(__file__).resolve().with_name("main.py")
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")  # no console window flashing up
    return str(pyw if pyw.exists() else py), f'"{main_py}" --auto', str(main_py.parent)


def build_task_xml(
    schedule: Schedule,
    command: str,
    arguments: str,
    workdir: str,
    now: datetime | None = None,
    enabled: bool = True,
) -> str:
    """Task Scheduler 1.2 XML for the given schedule. Pure; no system access."""
    schedule.validate()
    now = now or datetime.now()
    start = next_start(schedule, now)

    ET.register_namespace("", _NS)

    def el(parent, tag, text=None):
        e = ET.SubElement(parent, f"{{{_NS}}}{tag}")
        if text is not None:
            e.text = text
        return e

    task = ET.Element(f"{{{_NS}}}Task", {"version": "1.2"})
    reg = el(task, "RegistrationInfo")
    el(reg, "Description", f"Spotify Monthly Playlists: sync last month's and this month's playlists, {schedule.describe(now)}.")

    triggers = el(task, "Triggers")
    if schedule.unit == "hours":
        trig = el(triggers, "TimeTrigger")
        rep = el(trig, "Repetition")
        el(rep, "Interval", f"PT{schedule.every}H")
        el(rep, "StopAtDurationEnd", "false")
        el(trig, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
        el(trig, "Enabled", "true")
    else:
        trig = el(triggers, "CalendarTrigger")
        el(trig, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
        el(trig, "Enabled", "true")
        if schedule.unit == "days":
            el(el(trig, "ScheduleByDay"), "DaysInterval", str(schedule.every))
        elif schedule.unit == "weeks":
            by_week = el(trig, "ScheduleByWeek")
            el(by_week, "WeeksInterval", str(schedule.every))
            el(el(by_week, "DaysOfWeek"), _WEEKDAYS[start.weekday()])
        else:
            by_month = el(trig, "ScheduleByMonth")
            el(el(by_month, "DaysOfMonth"), "Day", "1")
            months = el(by_month, "Months")
            for mo in month_list(schedule.every, now):
                el(months, _MONTHS[mo - 1])

    principal = el(el(task, "Principals"), "Principal", None)
    principal.set("id", "Author")
    el(principal, "LogonType", "InteractiveToken")  # runs as the current user, no password stored
    el(principal, "RunLevel", "LeastPrivilege")

    settings = el(task, "Settings")
    el(settings, "MultipleInstancesPolicy", "IgnoreNew")
    el(settings, "DisallowStartIfOnBatteries", "false")
    el(settings, "StopIfGoingOnBatteries", "false")
    el(settings, "AllowHardTerminate", "true")
    el(settings, "StartWhenAvailable", "true")
    el(settings, "RunOnlyIfNetworkAvailable", "true")
    idle = el(settings, "IdleSettings")
    el(idle, "StopOnIdleEnd", "false")
    el(idle, "RestartOnIdle", "false")
    el(settings, "AllowStartOnDemand", "true")
    el(settings, "Enabled", "true" if enabled else "false")
    el(settings, "Hidden", "false")
    el(settings, "RunOnlyIfIdle", "false")
    el(settings, "WakeToRun", "false")
    el(settings, "ExecutionTimeLimit", "PT1H")
    el(settings, "Priority", "7")

    actions = el(task, "Actions")
    actions.set("Context", "Author")
    exe = el(actions, "Exec")
    el(exe, "Command", command)
    el(exe, "Arguments", arguments)
    el(exe, "WorkingDirectory", workdir)

    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode")


# ----- system integration (Windows only) -----------------------------------

def _schtasks(*args: str) -> subprocess.CompletedProcess:
    if not is_supported():
        raise ScheduleError("Automatic scheduling is only available on Windows. Use cron or launchd to run --auto.")
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError(f"Could not run Windows Task Scheduler: {exc}") from exc


def _fail(proc: subprocess.CompletedProcess, what: str) -> ScheduleError:
    detail = (proc.stderr or proc.stdout or "").strip()
    return ScheduleError(f"Could not {what}: {detail or f'schtasks exit code {proc.returncode}'}")


def install(schedule: Schedule, task_name: str = TASK_NAME, enabled: bool = True) -> None:
    """Create or replace the scheduled task, and remember the choice in config."""
    command, arguments, workdir = launch_command()
    xml = build_task_xml(schedule, command, arguments, workdir, enabled=enabled)
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(path).write_text(xml, encoding="utf-16")
        proc = _schtasks("/Create", "/TN", task_name, "/XML", path, "/F")
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise _fail(proc, "create the scheduled task")
    if task_name == TASK_NAME:
        cfg = config.load_config()
        cfg["schedule"] = schedule.to_dict()
        config.save_config(cfg)


def remove(task_name: str = TASK_NAME) -> bool:
    """Delete the task. Returns False if it didn't exist."""
    if not status(task_name).exists:
        return False
    proc = _schtasks("/Delete", "/TN", task_name, "/F")
    if proc.returncode != 0:
        raise _fail(proc, "remove the scheduled task")
    return True


def run_now(task_name: str = TASK_NAME) -> None:
    proc = _schtasks("/Run", "/TN", task_name)
    if proc.returncode != 0:
        raise _fail(proc, "start the scheduled task")


def status(task_name: str = TASK_NAME) -> TaskStatus:
    proc = _schtasks("/Query", "/TN", task_name, "/V", "/FO", "CSV")
    if proc.returncode != 0:
        return TaskStatus(exists=False)
    return parse_query_csv(proc.stdout)


def parse_query_csv(text: str) -> TaskStatus:
    """Parse `schtasks /Query /V /FO CSV`. Columns are positional (headers are localised):
    0 HostName, 1 TaskName, 2 Next Run Time, 3 Status, 4 Logon Mode, 5 Last Run Time, 6 Last Result."""
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    if len(rows) < 2 or len(rows[1]) < 7:
        return TaskStatus(exists=bool(rows))
    r = rows[1]
    return TaskStatus(True, status=r[3], next_run=r[2], last_run=r[5], last_result=describe_result(r[6]))


def describe_result(code: str) -> str:
    try:
        value = int(code)
    except ValueError:
        return code
    return {
        0: "OK",
        1: "error (see auto.log)",
        2: "not set up: open the app and Connect",
        267011: "has not run yet",   # 0x41303 SCHED_S_TASK_HAS_NOT_RUN
        267009: "running",           # 0x41301 SCHED_S_TASK_RUNNING
    }.get(value, f"code {value}")


def saved_schedule() -> Schedule:
    return Schedule.from_dict(config.load_config().get("schedule"))
