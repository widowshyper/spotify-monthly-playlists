import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

import scheduler as sch
from scheduler import Schedule, ScheduleError

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
NOW = datetime(2026, 10, 13, 14, 30)  # a Tuesday afternoon


def xml_for(schedule, now=NOW, enabled=True):
    text = sch.build_task_xml(schedule, r"C:\Apps\SMP.exe", "--auto", r"C:\Apps", now=now, enabled=enabled)
    assert text.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    return ET.fromstring(text.split("\n", 1)[1])


def find(root, path):
    return root.find(path, NS)


# ----- validation ----------------------------------------------------------------

@pytest.mark.parametrize(
    "s",
    [Schedule(0, "days"), Schedule(24, "hours"), Schedule(5, "months"), Schedule(1, "fortnights"),
     Schedule(1, "days", "25:00"), Schedule(1, "days", "9am"), Schedule(1, "days", "")],
)
def test_invalid_schedules_rejected(s):
    with pytest.raises(ScheduleError):
        s.validate()


@pytest.mark.parametrize("s", [Schedule(1, "hours"), Schedule(23, "hours"), Schedule(3, "days", "00:00"),
                               Schedule(2, "weeks", "23:59"), Schedule(6, "months")])
def test_valid_schedules_accepted(s):
    s.validate()


def test_from_dict_tolerates_junk():
    assert Schedule.from_dict(None) == Schedule()
    assert Schedule.from_dict({"every": "x"}) == Schedule()
    assert Schedule.from_dict({"every": "3", "unit": "hours", "at": "08:15"}) == Schedule(3, "hours", "08:15")


# ----- timing --------------------------------------------------------------------

def test_next_start_today_if_ahead_else_tomorrow():
    assert sch.next_start(Schedule(at="15:00"), NOW) == datetime(2026, 10, 13, 15, 0)
    assert sch.next_start(Schedule(at="09:00"), NOW) == datetime(2026, 10, 14, 9, 0)
    assert sch.next_start(Schedule(at="14:30"), NOW) == datetime(2026, 10, 14, 14, 30)  # not "now"


def test_month_list_starts_next_month_and_spreads_evenly():
    assert sch.month_list(1, NOW) == list(range(1, 13))
    assert sch.month_list(3, NOW) == [2, 5, 8, 11]       # Nov, Feb, May, Aug
    assert sch.month_list(12, NOW) == [11]
    assert sch.month_list(6, datetime(2026, 12, 5)) == [1, 7]  # wraps the year


def test_describe():
    assert Schedule(1, "hours", "09:00").describe(NOW) == "every hour (from 09:00)"
    assert Schedule(6, "hours", "09:00").describe(NOW) == "every 6 hours (from 09:00)"
    assert Schedule(1, "days", "09:00").describe(NOW) == "every day at 09:00"
    assert Schedule(1, "weeks", "18:00").describe(NOW) == "every Tuesday at 18:00"
    assert Schedule(2, "weeks", "09:00").describe(NOW) == "every 2 weeks on Wednesday at 09:00"
    assert Schedule(1, "months", "09:00").describe(NOW) == "on the 1st of every month at 09:00"


# ----- XML -------------------------------------------------------------------------

def test_hourly_xml_uses_repeating_time_trigger():
    root = xml_for(Schedule(6, "hours", "15:00"))
    trig = find(root, "t:Triggers/t:TimeTrigger")
    assert find(trig, "t:Repetition/t:Interval").text == "PT6H"
    assert find(trig, "t:StartBoundary").text == "2026-10-13T15:00:00"


def test_daily_xml():
    root = xml_for(Schedule(2, "days", "09:00"))
    trig = find(root, "t:Triggers/t:CalendarTrigger")
    assert find(trig, "t:ScheduleByDay/t:DaysInterval").text == "2"
    assert find(trig, "t:StartBoundary").text == "2026-10-14T09:00:00"


def test_weekly_xml_runs_on_start_weekday():
    root = xml_for(Schedule(1, "weeks", "18:00"))
    week = find(root, "t:Triggers/t:CalendarTrigger/t:ScheduleByWeek")
    assert find(week, "t:WeeksInterval").text == "1"
    assert find(week, "t:DaysOfWeek/t:Tuesday") is not None


def test_monthly_xml_runs_on_the_1st_of_selected_months():
    root = xml_for(Schedule(3, "months", "09:00"))
    month = find(root, "t:Triggers/t:CalendarTrigger/t:ScheduleByMonth")
    assert find(month, "t:DaysOfMonth/t:Day").text == "1"
    assert [m.tag.split("}")[1] for m in find(month, "t:Months")] == ["February", "May", "August", "November"]


def test_xml_settings_action_and_principal():
    root = xml_for(Schedule())
    s = find(root, "t:Settings")
    assert find(s, "t:StartWhenAvailable").text == "true"
    assert find(s, "t:MultipleInstancesPolicy").text == "IgnoreNew"
    assert find(s, "t:DisallowStartIfOnBatteries").text == "false"
    assert find(s, "t:Enabled").text == "true"
    assert find(root, "t:Principals/t:Principal/t:LogonType").text == "InteractiveToken"
    assert find(root, "t:Actions/t:Exec/t:Command").text == r"C:\Apps\SMP.exe"
    assert find(root, "t:Actions/t:Exec/t:Arguments").text == "--auto"
    assert find(xml_for(Schedule(), enabled=False), "t:Settings/t:Enabled").text == "false"


def test_xml_escapes_special_characters_in_paths():
    text = sch.build_task_xml(Schedule(), r"C:\R&D <apps>\SMP.exe", "--auto", r"C:\R&D <apps>", now=NOW)
    root = ET.fromstring(text.split("\n", 1)[1])
    assert find(root, "t:Actions/t:Exec/t:Command").text == r"C:\R&D <apps>\SMP.exe"


def test_launch_command_from_source_runs_main_py_with_auto():
    prog, args, workdir = sch.launch_command()
    assert args.endswith('main.py" --auto')
    assert os.path.isfile(args.split('"')[1])
    assert os.path.isdir(workdir)


def test_launch_command_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "SpotifyMonthlyPlaylists.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert sch.launch_command() == (str(exe.resolve()), "--auto", str(tmp_path.resolve()))


# ----- schtasks output parsing ------------------------------------------------------

def test_parse_query_csv_is_positional():
    text = (
        '"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author"\n'
        '"PC","\\SpotifyMonthlyPlaylists\\Auto Sync","14/10/2026 9:00:00 AM","Ready","Interactive only",'
        '"30/11/1999 12:00:00 AM","267011","me"\n'
    )
    st = sch.parse_query_csv(text)
    assert st.exists and st.status == "Ready"
    assert st.next_run == "14/10/2026 9:00:00 AM"
    assert st.last_result == "has not run yet"


def test_describe_result_codes():
    assert sch.describe_result("0") == "OK"
    assert sch.describe_result("2").startswith("not set up")
    assert sch.describe_result("-2147024894") == "code -2147024894"


# ----- real Task Scheduler (opt-in) -------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("SMP_TEST_SCHTASKS") != "1",
    reason="set SMP_TEST_SCHTASKS=1 on Windows to test against the real Task Scheduler",
)
@pytest.mark.parametrize("s", [Schedule(6, "hours"), Schedule(2, "days"), Schedule(1, "weeks"), Schedule(3, "months")])
def test_real_task_scheduler_round_trip(s):
    name = r"SpotifyMonthlyPlaylists\pytest-disabled"
    try:
        sch.install(s, task_name=name, enabled=False)  # disabled: can never actually fire
        st = sch.status(name)
        assert st.exists
        assert st.status.lower() == "disabled"
    finally:
        sch.remove(name)
    assert not sch.status(name).exists
