#!/usr/bin/env python3
"""Writes the Raspberry Pi firmware's throttle flags for node_exporter.

`vcgencmd get_throttled` answers with a bitmask: the low bits say what is
happening now, the same bits shifted by 16 say what has happened since boot.
Both matter. A Pi that is under-volted right now is a warning, while one that was
under-volted at some point is a reason to look at its power supply.

The collector also reports whether it managed to read the flags, and when it last
ran. node_exporter serves the last file written forever, so without those two a
collector that dies would leave a permanent all-clear behind.
"""

import contextlib
import os
import subprocess
import sys
import tempfile
import time

DEFAULT_PATH = "/var/lib/node_exporter/textfile/pi_throttled.prom"
CONDITIONS = (("undervoltage", 0), ("freq_capped", 1), ("throttled", 2), ("soft_temp_limit", 3))
SINCE_BOOT_SHIFT = 16


def parse(output: str) -> int:
    key, _, value = output.strip().partition("=")
    if key != "throttled" or not value:
        raise ValueError(f"unexpected vcgencmd output: {output!r}")
    return int(value, 16)


def render(flags: int | None, now: float) -> str:
    lines = []
    if flags is not None:
        lines += [
            "# HELP pi_throttle_state Raspberry Pi throttle flag from vcgencmd get_throttled (1 = set).",
            "# TYPE pi_throttle_state gauge",
        ]
        for name, bit in CONDITIONS:
            lines.append(f'pi_throttle_state{{condition="{name}",when="now"}} {flags >> bit & 1}')
            lines.append(
                f'pi_throttle_state{{condition="{name}",when="since_boot"}} '
                f"{flags >> (bit + SINCE_BOOT_SHIFT) & 1}"
            )
    lines += [
        "# HELP pi_throttle_collector_success 1 if vcgencmd was read on the last run.",
        "# TYPE pi_throttle_collector_success gauge",
        f"pi_throttle_collector_success {0 if flags is None else 1}",
        "# HELP pi_throttle_collector_last_run_timestamp_seconds When the collector last ran.",
        "# TYPE pi_throttle_collector_last_run_timestamp_seconds gauge",
        f"pi_throttle_collector_last_run_timestamp_seconds {now:.0f}",
    ]
    return "\n".join(lines) + "\n"


def read_flags() -> int | None:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=10, check=True
        )
        return parse(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"pi_throttled: {exc}", file=sys.stderr)
        return None


def write_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    # node_exporter only reads *.prom, so the temporary name is never scraped half-written.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".pi_throttled.", suffix=".tmp")
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
    path = argv[1] if len(argv) > 1 else DEFAULT_PATH
    write_atomic(path, render(read_flags(), time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
