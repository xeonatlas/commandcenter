import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("cc_textfile", ROOT / "hosts" / "server" / "cc_textfile.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


@pytest.mark.parametrize("text, seconds", [("10s", 10), ("2min", 120), ("1h 30min", 5400), ("1d 2h", 93600),
                                           ("500ms", 0.5), ("3", 3)])
def test_timespans(text, seconds):
    assert cc.parse_timespan(text) == seconds


def test_monotonic_timer_interval_survives_a_second_trigger_line():
    # systemctl prints one TimersMonotonic line per trigger; OnBootUSec must not win.
    props = cc.parse_show("Unit=x.service\n"
                          "TimersMonotonic={ OnUnitActiveUSec=2min ; next_elapse=1month }\n"
                          "TimersMonotonic={ OnBootUSec=3min ; next_elapse=3min }\n")
    assert cc.timer_interval(props) == 120


def test_calendar_timer_interval_uses_the_first_two_distinct_elapses():
    props = {"TimersCalendar": "{ OnCalendar=*-*-* 03:00:00 ; next_elapse=Tue 2026-09-29 03:00:00 UTC }"}
    output = ("  Original form: *-*-* 03:00:00\n"
              "    Next elapse: Tue 2026-09-29 03:00:00 UTC\n"
              "       (in UTC): Tue 2026-09-29 03:00:00 UTC\n"
              "       Iter. #2: Wed 2026-09-30 03:00:00 UTC\n")
    assert cc.timer_interval(props, lambda expr: cc.calendar_interval(cc.UTC_STAMP.findall(output))) == 86400


def test_previous_successes_round_trip_through_render():
    job = {"unit": "lightning-backup.service", "ran": True, "result": "exit-code", "last_run": 1790564401,
           "last_success": 1789960813, "interval": 86400.0, "running": False}
    text = cc.render([job], [], None, {"jobs": True}, 1790565257)
    assert 'lightning_job_last_success_timestamp_seconds{unit="lightning-backup.service"} 1789960813' in text
    assert 'lightning_job_last_result{unit="lightning-backup.service"} 0' in text
    assert cc.previous_successes(text) == {"lightning-backup.service": 1789960813.0}


def test_timestamps_keep_full_precision():
    assert cc.number(1790565257) == "1790565257"
    assert cc.number(0.25) == "0.25"


def test_wireguard_dump_names_peers_by_address():
    dump = ("wg0\tPRIV\tPUB\t51820\toff\n"
            "wg0\tKEY1\t(none)\t1.2.3.4:51820\t10.99.0.2/32\t1790565200\t100\t200\t25\n"
            "wg-mon\tKEY2\t(none)\t(none)\t10.98.0.1/32\t0\t0\t0\toff\n")
    assert cc.parse_wg_dump(dump) == [
        {"interface": "wg0", "peer": "10.99.0.2", "handshake": 1790565200, "rx": 100, "tx": 200},
        {"interface": "wg-mon", "peer": "10.98.0.1", "handshake": 0, "rx": 0, "tx": 0},
    ]


def test_a_failed_part_is_reported_not_hidden():
    text = cc.render([], [], True, {"jobs": False, "wireguard": True}, 1)
    assert 'cc_textfile_success{part="jobs"} 0' in text
    assert "node_reboot_required 1" in text
    assert "cc_textfile_last_run_timestamp_seconds 1" in text
