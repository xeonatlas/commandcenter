#!/usr/bin/env python3
"""Writes what node_exporter cannot see for itself, as a textfile, once a minute (as root).

- Scheduled jobs: for every timer matching --timers, the service's last result, when
  it last succeeded and how often it is meant to run. systemd forgets a success as
  soon as a run fails, so the last success is carried over from the previous file,
  or on first run looked up in the journal (0 when there is none: "never", honestly).
- WireGuard: each peer's latest handshake, per interface (`wg show` needs root).
- Whether the OS wants a reboot.
- When the collector last ran and whether each part worked, so a dead collector is
  never mistaken for an all-clear.

Stdlib only; it runs on Python 3.10 (Ubuntu 22.04) and up.
"""

import argparse
import contextlib
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = "/var/lib/node_exporter/textfile/cc_textfile.prom"
SPAN_UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600, "hr": 3600,
              "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800, "weeks": 604800,
              "month": 2629800, "months": 2629800, "y": 31557600, "year": 31557600, "years": 31557600}
UTC_STAMP = re.compile(r"\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC")
UTC_ENV = {**os.environ, "TZ": "UTC", "LC_ALL": "C"}


def parse_timespan(text: str) -> float:
    """systemd timespan ("10s", "2min", "1h 30min", "1d 2h") -> seconds."""
    total, found = 0.0, False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)?", text.strip()):
        total += float(number) * SPAN_UNITS.get(unit or "s", 0)
        found = True
    if not found:
        raise ValueError(f"not a timespan: {text!r}")
    return total


def calendar_interval(elapses: list[str]) -> float | None:
    """Seconds between the first two distinct UTC times in `systemd-analyze calendar` output."""
    seen = []
    for stamp in elapses:
        if stamp not in seen:
            seen.append(stamp)
    if len(seen) < 2:
        return None
    first, second = (datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) for s in seen[:2])
    return (second - first).total_seconds()


def parse_show(output: str) -> dict[str, str]:
    """`systemctl show` output; a property listed twice (one line per timer trigger) keeps both."""
    props = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = f"{props[key]}\n{value}" if key in props else value
    return props


def timer_interval(props: dict[str, str], calendar=None) -> float | None:
    """How often a timer fires: OnUnitActiveSec/OnUnitInactiveSec, else from its calendar."""
    mono = re.search(r"OnUnit(?:Active|Inactive)USec=([^;}]+)", props.get("TimersMonotonic", ""))
    if mono:
        return parse_timespan(mono.group(1))
    cal = re.search(r"OnCalendar=([^;}]+)", props.get("TimersCalendar", ""))
    if cal and calendar:
        return calendar(cal.group(1).strip())
    return None


def previous_successes(text: str) -> dict[str, float]:
    found = {}
    for unit, value in re.findall(r'^lightning_job_last_success_timestamp_seconds\{unit="([^"]+)"\} (\S+)$',
                                  text, re.M):
        found[unit] = float(value)
    return found


def parse_wg_dump(output: str) -> list[dict]:
    """`wg show all dump`: interface lines have 5 fields, peer lines 9."""
    peers = []
    for line in output.splitlines():
        f = line.split("\t")
        if len(f) != 9:
            continue
        interface, _key, _psk, _endpoint, allowed, handshake, rx, tx, _ka = f
        peer = allowed.split(",")[0].split("/")[0] if allowed != "(none)" else "none"
        peers.append({"interface": interface, "peer": peer, "handshake": int(handshake),
                      "rx": int(rx), "tx": int(tx)})
    return peers


def number(value) -> str:
    """Full precision: %g would turn a Unix timestamp into 1.79057e+09."""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metric(lines, name, help_text, kind, samples):
    if not samples:
        return
    lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    for labels, value in samples:
        label_text = ",".join(f'{k}="{escape(str(v))}"' for k, v in labels.items())
        lines.append(f"{name}{{{label_text}}} {number(value)}" if label_text else f"{name} {number(value)}")


def render(jobs, wg, reboot, ok, now) -> str:
    lines = []
    metric(lines, "lightning_job_last_result", "1 if the job's last run succeeded, 0 if it failed.", "gauge",
           [({"unit": j["unit"]}, 1 if j["result"] == "success" else 0) for j in jobs if j["ran"]])
    metric(lines, "lightning_job_last_success_timestamp_seconds",
           "When the job last finished successfully (0: no success on record).", "gauge",
           [({"unit": j["unit"]}, j["last_success"]) for j in jobs])
    metric(lines, "lightning_job_last_run_timestamp_seconds", "When the job's last run ended, whatever the result.",
           "gauge", [({"unit": j["unit"]}, j["last_run"]) for j in jobs if j["ran"]])
    metric(lines, "lightning_job_interval_seconds", "How often the job's timer fires.", "gauge",
           [({"unit": j["unit"]}, j["interval"]) for j in jobs if j["interval"]])
    metric(lines, "lightning_job_running", "1 while the job is running.", "gauge",
           [({"unit": j["unit"]}, 1 if j["running"] else 0) for j in jobs])
    metric(lines, "wireguard_latest_handshake_timestamp_seconds", "Latest handshake with the peer (0: never).",
           "gauge", [({"interface": p["interface"], "peer": p["peer"]}, p["handshake"]) for p in wg])
    metric(lines, "wireguard_received_bytes_total", "Bytes received from the peer.", "counter",
           [({"interface": p["interface"], "peer": p["peer"]}, p["rx"]) for p in wg])
    metric(lines, "wireguard_sent_bytes_total", "Bytes sent to the peer.", "counter",
           [({"interface": p["interface"], "peer": p["peer"]}, p["tx"]) for p in wg])
    if reboot is not None:
        metric(lines, "node_reboot_required", "1 if the OS has asked for a reboot.", "gauge", [({}, 1 if reboot else 0)])
    metric(lines, "cc_textfile_success", "1 if this part of the collector worked on its last run.", "gauge",
           [({"part": part}, 1 if good else 0) for part, good in ok.items()])
    metric(lines, "cc_textfile_last_run_timestamp_seconds", "When the collector last ran.", "gauge", [({}, now)])
    return "\n".join(lines) + "\n"


def run(*cmd) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=UTC_ENV, timeout=20).stdout


def systemd_calendar(expr: str) -> float | None:
    return calendar_interval(UTC_STAMP.findall(run("systemd-analyze", "calendar", "--iterations=2", expr)))


def mono_to_epoch(usec: str) -> float:
    """systemd's *TimestampMonotonic (µs on CLOCK_MONOTONIC) -> Unix time."""
    return time.clock_gettime(time.CLOCK_REALTIME) - time.clock_gettime(time.CLOCK_MONOTONIC) + int(usec) / 1e6


def journal_last_success(unit: str) -> float:
    with contextlib.suppress(Exception):
        out = run("journalctl", "-q", "-u", unit, "-o", "short-unix", "--no-pager", "-n", "1",
                  "-g", "Deactivated successfully|Succeeded\\.")
        if out.strip():
            return float(out.split()[0])
    return 0.0


def collect_jobs(pattern: str, previous: dict[str, float]) -> list[dict]:
    listing = run("systemctl", "list-timers", "--all", "--no-legend", "--no-pager", "--plain")
    timers = sorted({w for w in listing.split() if w.endswith(".timer") and fnmatch.fnmatch(w, pattern)})
    jobs = []
    for timer in timers:
        tprops = parse_show(run("systemctl", "show", timer, "-p", "Unit", "-p", "TimersMonotonic", "-p", "TimersCalendar"))
        unit = tprops.get("Unit") or timer.removesuffix(".timer") + ".service"
        sprops = parse_show(run("systemctl", "show", unit, "-p", "Result", "-p", "ActiveState",
                                "-p", "ExecMainExitTimestampMonotonic"))
        exit_mono = sprops.get("ExecMainExitTimestampMonotonic", "0") or "0"
        ran = exit_mono != "0"
        last_run = mono_to_epoch(exit_mono) if ran else 0.0
        result = sprops.get("Result", "")
        if ran and result == "success":
            last_success = last_run
        elif unit in previous:
            last_success = previous[unit]
        else:
            last_success = journal_last_success(unit)
        jobs.append({"unit": unit, "ran": ran, "result": result, "last_run": round(last_run),
                     "last_success": round(last_success), "interval": timer_interval(tprops, systemd_calendar),
                     "running": sprops.get("ActiveState") in ("active", "activating", "deactivating")})
    return jobs


def write_atomically(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cc_textfile.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?", default=DEFAULT_PATH)
    parser.add_argument("--timers", default="lightning-*.timer", help="glob of timers to report (default: %(default)s)")
    args = parser.parse_args(argv[1:])

    ok, jobs, wg, reboot = {}, [], [], None
    previous = {}
    with contextlib.suppress(FileNotFoundError):
        previous = previous_successes(Path(args.output).read_text())
    try:
        jobs = collect_jobs(args.timers, previous)
        ok["jobs"] = True
    except Exception as exc:  # report the failure; never leave a stale all-clear
        print(f"cc_textfile: jobs: {exc}", file=sys.stderr)
        ok["jobs"] = False
    if shutil.which("wg"):
        try:
            wg = parse_wg_dump(run("wg", "show", "all", "dump"))
            ok["wireguard"] = True
        except Exception as exc:
            print(f"cc_textfile: wireguard: {exc}", file=sys.stderr)
            ok["wireguard"] = False
    if Path("/etc/debian_version").exists():
        reboot = Path("/run/reboot-required").exists()
    write_atomically(args.output, render(jobs, wg, reboot, ok, round(time.time())))
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
