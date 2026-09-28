import subprocess

import pytest

import pi_throttled
from pi_throttled import parse, read_flags, render, write_atomic


def value(text: str, series: str) -> str:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return line.split()[-1]
    raise AssertionError(f"{series} not in output:\n{text}")


def test_parse_reads_hex_flags():
    assert parse("throttled=0x50005\n") == 0x50005


def test_parse_rejects_other_output():
    with pytest.raises(ValueError):
        parse("VCHI initialization failed\n")


def test_render_splits_current_and_past_conditions():
    # bits 0 and 2 (under-voltage, throttled now) and 16 and 18 (both since boot)
    text = render(0x50005, now=1000.0)
    assert value(text, 'pi_throttle_state{condition="undervoltage",when="now"}') == "1"
    assert value(text, 'pi_throttle_state{condition="freq_capped",when="now"}') == "0"
    assert value(text, 'pi_throttle_state{condition="throttled",when="now"}') == "1"
    assert value(text, 'pi_throttle_state{condition="soft_temp_limit",when="now"}') == "0"
    assert value(text, 'pi_throttle_state{condition="undervoltage",when="since_boot"}') == "1"
    assert value(text, 'pi_throttle_state{condition="freq_capped",when="since_boot"}') == "0"
    assert value(text, 'pi_throttle_state{condition="throttled",when="since_boot"}') == "1"
    assert value(text, "pi_throttle_collector_success") == "1"
    assert value(text, "pi_throttle_collector_last_run_timestamp_seconds") == "1000"


def test_render_without_flags_reports_failure_and_no_state():
    text = render(None, now=5.0)
    assert "pi_throttle_state{" not in text
    assert value(text, "pi_throttle_collector_success") == "0"
    assert value(text, "pi_throttle_collector_last_run_timestamp_seconds") == "5"


def test_render_declares_each_family_once():
    text = render(0, now=1.0)
    assert text.count("# TYPE pi_throttle_state gauge") == 1
    assert text.count("# TYPE pi_throttle_collector_success gauge") == 1


def test_read_flags_returns_none_when_vcgencmd_is_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("vcgencmd")

    monkeypatch.setattr(pi_throttled.subprocess, "run", missing)
    assert read_flags() is None


def test_read_flags_returns_none_on_unexpected_output(monkeypatch):
    def garbled(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="VCHI initialization failed\n", stderr="")

    monkeypatch.setattr(pi_throttled.subprocess, "run", garbled)
    assert read_flags() is None


def test_write_atomic_leaves_a_world_readable_file_and_no_temp(tmp_path):
    # node_exporter runs as nobody inside its container; a 0600 file is invisible to it.
    path = tmp_path / "pi_throttled.prom"
    path.write_text("old\n")
    write_atomic(str(path), "new\n")
    assert path.read_text() == "new\n"
    assert path.stat().st_mode & 0o777 == 0o644
    assert list(tmp_path.iterdir()) == [path]
