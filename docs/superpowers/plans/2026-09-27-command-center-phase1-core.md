# Command Center Phase 1 (Core Stack on the Pi) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running Prometheus + Alertmanager + Grafana + blackbox stack on the `command-center` Pi that probes every public endpoint, watches the Pi itself, pages through Pushover by severity, and is watched in turn by a healthchecks.io dead-man's switch.

**Architecture:** Docker Compose on the Pi, every container on host networking, with every UI except Grafana bound to 127.0.0.1. All config lives in this repo and is shipped by `rsync`. On the Pi, `scripts/apply.sh` renders the Alertmanager secrets from a local `.env`, validates everything with the same pinned images that run it, and only then converges the stack. No server outside the Pi is touched in this phase.

**Tech Stack:** Prometheus v3.15.0, Alertmanager v0.34.1, blackbox_exporter v0.28.0, node_exporter v1.12.1, Grafana 13.2.2, Docker Compose, Python 3.13 (stdlib only on the Pi), pytest + PyYAML on the workstation, Pushover, healthchecks.io.

**Spec:** `docs/superpowers/specs/2026-09-27-command-center-design.md` (sections 3.1, 3.5, 3.6, 4.1, 5, 6, 7, 8 phase 1, 9, 10). Plans 2 (Lightning infrastructure), 3 (Lightning app metrics) and 4 (DD Cloud and mc.synstick) follow as separate plans. The spec's `inventory/hosts.yml` and `scripts/render.py` arrive in Plan 2 with the first remote host; in this phase the only node target is the Pi, in `prometheus/targets/node.yml`.

## Global Constraints

- Image pins, exactly: `prom/prometheus:v3.15.0`, `prom/alertmanager:v0.34.1`, `prom/blackbox-exporter:v0.28.0`, `prom/node-exporter:v1.12.1`, `grafana/grafana:13.2.2`. All verified to have arm64 builds.
- Retention 90 days, with the size cap from `.env` `PROM_RETENTION_SIZE` (default `12GB`). Scrape every 30 s; external probes every 60 s.
- Listeners: Prometheus 127.0.0.1:9090, Alertmanager 127.0.0.1:9093 (cluster listener disabled), blackbox 127.0.0.1:9115, node_exporter 127.0.0.1:9100, Grafana 0.0.0.0:3000 (home LAN only).
- Pi paths: repo `/opt/command-center`, data `/srv/command-center/{prometheus,alertmanager,grafana}`, textfile collector directory `/var/lib/node_exporter/textfile`. Containers that write data run as `1000:1000` (the `atlas` user on the Pi).
- Secrets live only in `/opt/command-center/.env` on the Pi. They are never committed, never typed into chat, and never read by the agent. The user edits that file.
- Always call `/usr/bin/ssh`. On this workstation, plain `ssh` is kitty's `kitten ssh` and fails in scripts.
- The agent never runs `sudo` on the Pi (it needs a password). The user runs the one-time bootstrap themselves.
- Anything that runs on the Pi uses only the Python standard library. pytest and PyYAML are for the workstation only.
- Severity labels are exactly `critical`, `warning`, `info`, or `none` (Watchdog). Pushover priority is `2` for critical (emergency) and `0` for warning.
- Probe targets and their expectations are exactly the table in spec section 3.1, confirmed live on 2026-09-27.
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A secret missing or empty on the Pi.** A deploy must stop before touching the running stack, and name every missing variable. It must never render `user_key: ''` and reload. Pinned by `test_render_reports_every_missing_variable` and `test_render_treats_empty_value_as_missing` (Task 1), and by `test_apply_stops_before_docker_when_a_secret_is_missing` (Task 7).
2. **A textfile node_exporter cannot read.** `mkstemp` creates files as 0600, but the node_exporter container runs as `nobody`, so metrics would vanish silently. Pinned by `test_write_atomic_leaves_a_world_readable_file_and_no_temp` (Task 2).
3. **A collector that stops or fails, leaving a stale all-clear.** The last reading stays on disk forever. Pinned by `test_render_without_flags_reports_failure_and_no_state` (Task 2) and the `PiCollectorStale` rule tests (Task 4).
4. **A home connection without working IPv6** must not make dual-stack sites look down. Pinned by `test_http_modules_prefer_ipv4_with_fallback` (Task 3).
5. **A config change that silently doesn't take effect**, because a single-file bind mount keeps serving the old inode after `mv`. Pinned by `test_alertmanager_mounts_the_run_directory_not_the_file` (Task 7).

---

## File Structure

```
.gitignore                                  # modified: .env, run/, .venv/, caches
.env.example                                # documented secret and tuning variables
Makefile                                    # test, check-configs (prom, blackbox, am, dashboards), deploy
pytest.ini                                  # test paths and import paths
compose.yml                                 # the five services, pinned
README.md                                   # what it is, how to deploy, how to add a probe
scripts/render_config.py                    # ${NAME} template + env file -> private rendered file
scripts/check_routes.sh                     # amtool routing table test
scripts/check_dashboards.py                 # Grafana dashboard lint
scripts/apply.sh                            # on the Pi: render, validate, converge, reload
scripts/deploy.sh                           # workstation: check, rsync, run apply.sh remotely
scripts/bootstrap-pi.sh                     # once, as root, run by the user: Docker, dirs, journald, collector timer
hosts/pi/pi_throttled.py                    # vcgencmd get_throttled -> textfile metrics
hosts/pi/pi-throttled.service               # oneshot collector
hosts/pi/pi-throttled.timer                 # every minute
prometheus/prometheus.yml                   # scrape jobs, rule files, alertmanager
prometheus/targets/node.yml                 # node_exporter targets (Pi only in phase 1)
prometheus/targets/probes.yml               # blackbox targets from spec 3.1
prometheus/rules/meta.yml                   # Watchdog, scrape, rule and notification failures
prometheus/rules/probes.yml                 # ProbeDown, HomeConnectivityLost, CertExpiringSoon
prometheus/rules/hosts.yml                  # HostDown, disk forecasts, memory, clock, Pi throttling
prometheus/tests/{meta,probes,hosts}_test.yml   # promtool unit tests
blackbox/blackbox.yml                       # probe modules
alertmanager/alertmanager.yml.tmpl          # routes, inhibitions, receivers with ${SECRET} placeholders
alertmanager/routes.test                    # label set => expected receiver
grafana/provisioning/datasources/prometheus.yml
grafana/provisioning/dashboards/command-center.yml
grafana/dashboards/{command-center,probes,hosts}.json
tests/test_render_config.py
tests/test_pi_throttled.py
tests/test_probe_config.py
tests/test_check_dashboards.py
tests/test_scripts.py                       # shell scripts parse, no bare ssh, compose mount shape, apply.sh ordering
docs/drills.md                              # fire drill record
```

---

### Task 1: Repo scaffold and secret rendering

**Files:**
- Modify: `.gitignore`
- Create: `.env.example`, `Makefile`, `pytest.ini`, `compose.yml`, `scripts/render_config.py`
- Test: `tests/test_render_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `render_config.py` with `parse_env(text: str) -> dict[str, str]`, `render(template: str, values: dict[str, str]) -> str` (raises `ValueError` naming every missing or empty `${NAME}`), and `main(argv: list[str]) -> int`. CLI: `python3 scripts/render_config.py TEMPLATE ENV_FILE OUTPUT`, where OUTPUT is written with mode 0600 only on success. Make targets `test`, `check-configs`, `check-prom`, `check-blackbox`, `check-am`, `check-dashboards` and `deploy`, where `check-am` takes `AM_CONFIG=` (default `run/alertmanager.check.yml`, rendered from `.env.example`). compose service names are `prometheus`, `alertmanager`, `blackbox`, `node-exporter` and `grafana`.

- [ ] **Step 1: Replace `.gitignore`**

```
.env
run/
.venv/
__pycache__/
.pytest_cache/
*.local.json
.directory
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
pythonpath = scripts hosts/pi
```

- [ ] **Step 3: Create `Makefile`**

```make
# Checks and deploys for the command center. `make check` runs on the workstation
# before a deploy; `make check-configs` runs again on the Pi before anything is
# applied, with the same pinned images that will run the config.

PROM_IMAGE := $(shell grep -oE 'prom/prometheus:v[0-9.]+' compose.yml)
AM_IMAGE   := $(shell grep -oE 'prom/alertmanager:v[0-9.]+' compose.yml)
BB_IMAGE   := $(shell grep -oE 'prom/blackbox-exporter:v[0-9.]+' compose.yml)
AS_ME      := --user $(shell id -u):$(shell id -g)
AM_CONFIG  ?= run/alertmanager.check.yml

.PHONY: check test check-configs check-prom check-blackbox check-am check-dashboards deploy

check: test check-configs

test: .venv/bin/pytest
	.venv/bin/pytest -q

.venv/bin/pytest:
	python3 -m venv .venv
	.venv/bin/pip install -q pytest pyyaml

check-configs: check-prom check-blackbox check-am check-dashboards

check-prom:
	docker run --rm $(AS_ME) -v $(CURDIR)/prometheus:/etc/prometheus:ro --entrypoint promtool $(PROM_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm $(AS_ME) -v $(CURDIR)/prometheus:/etc/prometheus:ro --entrypoint sh $(PROM_IMAGE) -c 'promtool test rules /etc/prometheus/tests/*.yml'

check-blackbox:
	docker run --rm $(AS_ME) -v $(CURDIR)/blackbox:/etc/blackbox:ro $(BB_IMAGE) --config.file=/etc/blackbox/blackbox.yml --config.check

check-am:
	@if [ "$(AM_CONFIG)" = "run/alertmanager.check.yml" ]; then \
		mkdir -p run && python3 scripts/render_config.py alertmanager/alertmanager.yml.tmpl .env.example $(AM_CONFIG); fi
	docker run --rm $(AS_ME) -v $(CURDIR):/w -w /w --entrypoint amtool $(AM_IMAGE) check-config $(AM_CONFIG)
	scripts/check_routes.sh $(AM_IMAGE) $(AM_CONFIG) alertmanager/routes.test

check-dashboards:
	python3 scripts/check_dashboards.py grafana/dashboards

deploy:
	scripts/deploy.sh
```

- [ ] **Step 4: Create `compose.yml`**

```yaml
# The command center. Host networking throughout: later phases scrape over the
# wg-mon interface on the host, and nothing here needs Docker's NAT.
name: command-center

x-service: &service
  restart: unless-stopped
  network_mode: host
  logging:
    driver: local
    options: {max-size: 10m, max-file: "3"}

services:
  prometheus:
    <<: *service
    image: prom/prometheus:v3.15.0
    user: "1000:1000"
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=90d
      - --storage.tsdb.retention.size=${PROM_RETENTION_SIZE:-12GB}
      - --web.listen-address=127.0.0.1:9090
      - --web.enable-lifecycle
    volumes:
      - ./prometheus:/etc/prometheus:ro
      - /srv/command-center/prometheus:/prometheus

  alertmanager:
    <<: *service
    image: prom/alertmanager:v0.34.1
    user: "1000:1000"
    command:
      - --config.file=/etc/alertmanager/run/alertmanager.yml
      - --storage.path=/alertmanager
      - --web.listen-address=127.0.0.1:9093
      - --cluster.listen-address=
    volumes:
      # The directory, not the file: apply.sh swaps the file by rename, and a
      # single-file bind mount would keep serving the old inode.
      - ./run:/etc/alertmanager/run:ro
      - /srv/command-center/alertmanager:/alertmanager

  blackbox:
    <<: *service
    image: prom/blackbox-exporter:v0.28.0
    command:
      - --config.file=/etc/blackbox/blackbox.yml
      - --web.listen-address=127.0.0.1:9115
    volumes:
      - ./blackbox:/etc/blackbox:ro

  node-exporter:
    <<: *service
    image: prom/node-exporter:v1.12.1
    pid: host
    command:
      - --path.rootfs=/host
      - --web.listen-address=127.0.0.1:9100
      - --collector.textfile.directory=/host/var/lib/node_exporter/textfile
    volumes:
      - /:/host:ro,rslave

  grafana:
    <<: *service
    image: grafana/grafana:13.2.2
    user: "1000:1000"
    environment:
      GF_SERVER_HTTP_ADDR: 0.0.0.0
      GF_SERVER_HTTP_PORT: "3000"
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD in .env}
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_ANALYTICS_REPORTING_ENABLED: "false"
      GF_ANALYTICS_CHECK_FOR_UPDATES: "false"
      GF_NEWS_NEWS_FEED_ENABLED: "false"
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/etc/grafana/dashboards:ro
      - /srv/command-center/grafana:/var/lib/grafana
```

- [ ] **Step 5: Create `.env.example`**

```
# Copy to /opt/command-center/.env on the Pi and fill in. Never commit the real one.

# Pushover: your user key (top right of the Pushover dashboard) and the token of
# an application named "Command Center" (Create an Application/API Token).
PUSHOVER_USER_KEY=uexampleexampleexampleexample00
PUSHOVER_APP_TOKEN=aexampleexampleexampleexample00

# healthchecks.io ping URL of the "command-center heartbeat" check.
HEALTHCHECKS_PING_URL=https://hc-ping.com/00000000-0000-0000-0000-000000000000

# Grafana admin password. Applied only when Grafana first creates its database;
# change it inside Grafana after that.
GRAFANA_ADMIN_PASSWORD=change-me

# Prometheus on-disk cap. 12GB while on the SD card; raise once the USB drive is fitted.
PROM_RETENTION_SIZE=12GB
```

- [ ] **Step 6: Write the failing tests** in `tests/test_render_config.py`

```python
import pytest

from render_config import main, parse_env, render


def test_parse_env_skips_comments_and_blank_lines():
    assert parse_env("# secret\n\nA=1\n  B = two  \n") == {"A": "1", "B": "two"}


def test_parse_env_strips_matching_quotes_only():
    text = "A=\"x y\"\nB='z'\nC=\"unbalanced'\n"
    assert parse_env(text) == {"A": "x y", "B": "z", "C": "\"unbalanced'"}


def test_parse_env_rejects_lines_without_equals():
    with pytest.raises(ValueError, match="line 2"):
        parse_env("A=1\nnot a pair\n")


def test_render_fills_placeholders():
    assert render("user: ${USER_KEY}\n", {"USER_KEY": "u123"}) == "user: u123\n"


def test_render_reports_every_missing_variable():
    with pytest.raises(ValueError) as exc:
        render("a: ${FIRST}\nb: ${SECOND}\n", {})
    assert "${FIRST}" in str(exc.value)
    assert "${SECOND}" in str(exc.value)


def test_render_treats_empty_value_as_missing():
    with pytest.raises(ValueError, match=r"\$\{TOKEN\}"):
        render("token: ${TOKEN}", {"TOKEN": ""})


def test_render_does_not_re_expand_dollars_inside_values():
    assert render("url: ${URL}", {"URL": "https://hc-ping.com/a$b"}) == "url: https://hc-ping.com/a$b"


def test_main_writes_a_private_file(tmp_path):
    (tmp_path / "t").write_text("k: ${K}\n")
    (tmp_path / "e").write_text("K=v\n")
    out = tmp_path / "out.yml"
    assert main(["render_config.py", str(tmp_path / "t"), str(tmp_path / "e"), str(out)]) == 0
    assert out.read_text() == "k: v\n"
    assert out.stat().st_mode & 0o777 == 0o600


def test_main_fails_without_writing_when_a_secret_is_missing(tmp_path, capsys):
    (tmp_path / "t").write_text("k: ${K}\n")
    (tmp_path / "e").write_text("OTHER=v\n")
    out = tmp_path / "out.yml"
    assert main(["render_config.py", str(tmp_path / "t"), str(tmp_path / "e"), str(out)]) == 1
    assert not out.exists()
    assert "${K}" in capsys.readouterr().err
```

- [ ] **Step 7: Run them to verify they fail**

Run: `make test`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'render_config'`. The first run also creates `.venv`.

- [ ] **Step 8: Implement `scripts/render_config.py`**

```python
#!/usr/bin/env python3
"""Fills ${NAME} placeholders in a config template from a KEY=VALUE env file.

Alertmanager does not expand environment variables in its config, and its
secrets (Pushover keys, the heartbeat URL) must not live in git. So the template
is committed and the secrets are poured in on the Pi at deploy time.

Only the env file is consulted, never the process environment, so a render is
reproducible from the two files alone. A placeholder with no value, or an empty
one, is an error naming every such variable: a half-rendered config must never
reach Alertmanager.
"""

import os
import string
import sys


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.isidentifier():
            raise ValueError(f"line {number}: expected KEY=VALUE, got {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def render(template: str, values: dict[str, str]) -> str:
    tmpl = string.Template(template)
    missing = [name for name in tmpl.get_identifiers() if not values.get(name)]
    if missing:
        names = ", ".join(f"${{{name}}}" for name in missing)
        raise ValueError(f"no value in the env file for {names}")
    return tmpl.substitute(values)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: render_config.py TEMPLATE ENV_FILE OUTPUT", file=sys.stderr)
        return 2
    template_path, env_path, output_path = argv[1:]
    try:
        with open(env_path) as f:
            values = parse_env(f.read())
        with open(template_path) as f:
            rendered = render(f.read(), values)
    except (OSError, ValueError) as exc:
        print(f"render_config: {exc}", file=sys.stderr)
        return 1
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Then: `chmod +x scripts/render_config.py`

- [ ] **Step 9: Run the tests to verify they pass**

Run: `make test`
Expected: `9 passed`

- [ ] **Step 10: Commit**

```bash
git add .gitignore .env.example Makefile pytest.ini compose.yml scripts/render_config.py tests/test_render_config.py
git commit -m "feat: repo scaffold, pinned compose stack and secret rendering" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pi throttle collector

**Files:**
- Create: `hosts/pi/pi_throttled.py`, `hosts/pi/pi-throttled.service`, `hosts/pi/pi-throttled.timer`
- Test: `tests/test_pi_throttled.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the metrics `pi_throttle_state{condition, when}`, where `condition` is one of `undervoltage`, `freq_capped`, `throttled`, `soft_temp_limit` and `when` is `now` or `since_boot`; `pi_throttle_collector_success` (1 or 0); and `pi_throttle_collector_last_run_timestamp_seconds`. The output file is `/var/lib/node_exporter/textfile/pi_throttled.prom`. Python functions: `parse(output: str) -> int`, `render(flags: int | None, now: float) -> str`, `read_flags() -> int | None`, `write_atomic(path: str, text: str) -> None` and `main(argv: list[str]) -> int`. Task 4's rules and Task 6's hosts dashboard use these metric names.

- [ ] **Step 1: Write the failing tests** in `tests/test_pi_throttled.py`

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_pi_throttled.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pi_throttled'`

- [ ] **Step 3: Implement `hosts/pi/pi_throttled.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pi_throttled.py -q`
Expected: `8 passed`

- [ ] **Step 5: Create the systemd units**

`hosts/pi/pi-throttled.service`:

```ini
[Unit]
Description=Write Raspberry Pi throttle flags for node_exporter

[Service]
Type=oneshot
User=atlas
ExecStart=/usr/bin/python3 /opt/command-center/hosts/pi/pi_throttled.py /var/lib/node_exporter/textfile/pi_throttled.prom
```

`hosts/pi/pi-throttled.timer`:

```ini
[Unit]
Description=Refresh Raspberry Pi throttle flags every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit**

```bash
git add hosts/pi tests/test_pi_throttled.py
git commit -m "feat: Pi throttle flags as textfile metrics, with collector health" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Prometheus config, probes and probe alerts

**Files:**
- Create: `prometheus/prometheus.yml`, `prometheus/targets/node.yml`, `prometheus/targets/probes.yml`, `blackbox/blackbox.yml`, `prometheus/rules/probes.yml`
- Test: `prometheus/tests/probes_test.yml`, `tests/test_probe_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: scrape jobs `prometheus`, `alertmanager`, `node` and `blackbox`. Every node target carries a `host` label; every probe carries `service` and `module` labels. Alerts `ProbeDown` (critical), `HomeConnectivityLost` (warning) and `CertExpiringSoon` (warning), whose names Task 5's inhibition rules match exactly.

- [ ] **Step 1: Write the failing promtool tests** in `prometheus/tests/probes_test.yml`

```yaml
rule_files:
  - ../rules/probes.yml

evaluation_interval: 30s

tests:
  - name: one failing probe pages after three minutes; home connectivity stays quiet
    interval: 1m
    input_series:
      - series: 'probe_success{job="blackbox",instance="https://a.example/",service="a",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://b.example/",service="b",module="http_2xx"}'
        values: '1x10'
    alert_rule_test:
      - eval_time: 2m
        alertname: ProbeDown
        exp_alerts: []
      - eval_time: 4m
        alertname: ProbeDown
        exp_alerts:
          - exp_labels: {severity: critical, job: blackbox, instance: "https://a.example/", service: a, module: http_2xx}
            exp_annotations: {summary: "a is unreachable: https://a.example/"}
      - eval_time: 4m
        alertname: HomeConnectivityLost
        exp_alerts: []

  - name: every probe failing raises one home connectivity warning
    interval: 1m
    input_series:
      - series: 'probe_success{job="blackbox",instance="https://a.example/",service="a",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://b.example/",service="b",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://c.example/",service="c",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://d.example/",service="d",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://e.example/",service="e",module="http_2xx"}'
        values: '0x10'
    alert_rule_test:
      - eval_time: 1m
        alertname: HomeConnectivityLost
        exp_alerts: []
      - eval_time: 3m
        alertname: HomeConnectivityLost
        exp_alerts:
          - exp_labels: {severity: warning}
            exp_annotations: {summary: "Most external probes are failing at once; the home connection is the likely cause."}

  - name: four of five failing is exactly 80 percent and is not home loss
    interval: 1m
    input_series:
      - series: 'probe_success{job="blackbox",instance="https://a.example/",service="a",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://b.example/",service="b",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://c.example/",service="c",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://d.example/",service="d",module="http_2xx"}'
        values: '0x10'
      - series: 'probe_success{job="blackbox",instance="https://e.example/",service="e",module="http_2xx"}'
        values: '1x10'
    alert_rule_test:
      - eval_time: 5m
        alertname: HomeConnectivityLost
        exp_alerts: []

  - name: a certificate inside 14 days warns after an hour; 30 days does not
    interval: 5m
    input_series:
      - series: 'probe_ssl_earliest_cert_expiry{job="blackbox",instance="https://soon.example/",service="soon",module="http_2xx"}'
        values: '864000x20'
      - series: 'probe_ssl_earliest_cert_expiry{job="blackbox",instance="https://fine.example/",service="fine",module="http_2xx"}'
        values: '2592000x20'
    alert_rule_test:
      - eval_time: 30m
        alertname: CertExpiringSoon
        exp_alerts: []
      - eval_time: 65m
        alertname: CertExpiringSoon
        exp_alerts:
          - exp_labels: {severity: warning, job: blackbox, instance: "https://soon.example/", service: soon, module: http_2xx}
            exp_annotations: {summary: "TLS certificate for https://soon.example/ expires within 14 days"}
```

- [ ] **Step 2: Write the failing config tests** in `tests/test_probe_config.py`

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(relative: str):
    return yaml.safe_load((ROOT / relative).read_text())


def seconds(duration: str) -> int:
    return int(duration[:-1]) * {"s": 1, "m": 60}[duration[-1]]


def test_every_probe_uses_a_module_that_exists():
    modules = load("blackbox/blackbox.yml")["modules"]
    for group in load("prometheus/targets/probes.yml"):
        assert group["labels"]["module"] in modules, group


def test_every_probe_group_names_its_service_and_has_targets():
    for group in load("prometheus/targets/probes.yml"):
        assert group["labels"].get("service"), group
        assert group["targets"], group


def test_http_modules_prefer_ipv4_with_fallback():
    # A home connection without working IPv6 must not make a dual-stack site look down.
    for name, module in load("blackbox/blackbox.yml")["modules"].items():
        assert module["http"]["preferred_ip_protocol"] == "ip4", name
        assert module["http"]["ip_protocol_fallback"] is True, name


def test_probe_timeouts_fit_inside_the_scrape_timeout():
    jobs = load("prometheus/prometheus.yml")["scrape_configs"]
    limit = seconds(next(j for j in jobs if j["job_name"] == "blackbox")["scrape_timeout"])
    for name, module in load("blackbox/blackbox.yml")["modules"].items():
        assert seconds(module["timeout"]) < limit, name
```

- [ ] **Step 3: Run both to verify they fail**

Run: `.venv/bin/pytest tests/test_probe_config.py -q`
Expected: FAIL with `FileNotFoundError` for `blackbox/blackbox.yml`

Run: `make check-prom`
Expected: FAIL, because `/etc/prometheus/prometheus.yml` does not exist

- [ ] **Step 4: Create `blackbox/blackbox.yml`**

```yaml
modules:
  # Must answer 2xx, following redirects (sites redirect apex to www).
  http_2xx:
    prober: http
    timeout: 15s
    http:
      preferred_ip_protocol: ip4
      ip_protocol_fallback: true
      follow_redirects: true
      valid_status_codes: []

  # The host answering at all is what matters (tiles, analytics, API roots).
  http_any_answer:
    prober: http
    timeout: 15s
    http:
      preferred_ip_protocol: ip4
      ip_protocol_fallback: true
      follow_redirects: true
      valid_status_codes: [200, 204, 301, 302, 307, 308, 401, 403, 404]

  # POST-only functions: 405 to a GET proves the function is deployed and running.
  http_405:
    prober: http
    timeout: 15s
    http:
      preferred_ip_protocol: ip4
      ip_protocol_fallback: true
      follow_redirects: true
      valid_status_codes: [405]
```

- [ ] **Step 5: Create `prometheus/targets/probes.yml`** (spec 3.1; statuses confirmed 2026-09-27)

```yaml
- labels: {module: http_2xx, service: lightning-api}
  targets:
    - https://api.lightningapi.dev/health
    - https://api.warpulse.com/health
- labels: {module: http_2xx, service: lightning-site}
  targets:
    - https://lightningapi.dev/
- labels: {module: http_any_answer, service: lightning-tiles}
  targets:
    - https://tiles.lightningapi.dev/
- labels: {module: http_2xx, service: ddcloud}
  targets:
    - https://cloud.niallmurray.com/health
- labels: {module: http_any_answer, service: ddcloud-analytics}
  targets:
    - https://analytics.niallmurray.com/
- labels: {module: http_2xx, service: dualdegrees-site}
  targets:
    - https://dualdegrees.io/
- labels: {module: http_2xx, service: warpulse-site}
  targets:
    - https://warpulse.com/
    - https://support.warpulse.com/
- labels: {module: http_2xx, service: vaulterm-site}
  targets:
    - https://vaulterm.com/
- labels: {module: http_405, service: vaulterm-waitlist}
  targets:
    - https://vaulterm.com/api/waitlist
- labels: {module: http_2xx, service: vaulterm-updates}
  targets:
    - https://github.com/xeonatlas/termora-releases/releases/latest/download/latest.yml
- labels: {module: http_any_answer, service: zelara}
  targets:
    - https://www.zelara.chat/
    - https://api.zelara.chat/
```

- [ ] **Step 6: Create `prometheus/targets/node.yml`**

```yaml
- labels: {host: command-center}
  targets:
    - 127.0.0.1:9100
```

- [ ] **Step 7: Create `prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s
  external_labels:
    monitor: command-center

rule_files:
  - /etc/prometheus/rules/*.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["127.0.0.1:9093"]

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:9090"]
        labels: {host: command-center}

  - job_name: alertmanager
    static_configs:
      - targets: ["127.0.0.1:9093"]
        labels: {host: command-center}

  - job_name: node
    file_sd_configs:
      - files: [/etc/prometheus/targets/node.yml]

  - job_name: blackbox
    scrape_interval: 60s
    scrape_timeout: 20s
    metrics_path: /probe
    file_sd_configs:
      - files: [/etc/prometheus/targets/probes.yml]
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [module]
        target_label: __param_module
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9115
```

- [ ] **Step 8: Create `prometheus/rules/probes.yml`**

```yaml
groups:
  - name: probes
    rules:
      - alert: ProbeDown
        expr: probe_success{job="blackbox"} == 0
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "{{ $labels.service }} is unreachable: {{ $labels.instance }}"

      # More than 80% failing together is the home line, not a dozen outages.
      # Alertmanager uses this to inhibit the individual ProbeDown pages.
      - alert: HomeConnectivityLost
        expr: count(probe_success{job="blackbox"} == 0) / count(probe_success{job="blackbox"}) > 0.8
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Most external probes are failing at once; the home connection is the likely cause."

      - alert: CertExpiringSoon
        expr: (probe_ssl_earliest_cert_expiry{job="blackbox"} - time()) / 86400 < 14
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "TLS certificate for {{ $labels.instance }} expires within 14 days"
```

- [ ] **Step 9: Create empty rule and test placeholders owned by Task 4** so `check-prom` can run

`prometheus/rules/meta.yml` and `prometheus/rules/hosts.yml` each contain:

```yaml
groups: []
```

Task 4 replaces both.

- [ ] **Step 10: Run both checks to verify they pass**

Run: `.venv/bin/pytest tests/test_probe_config.py -q`
Expected: `4 passed`

Run: `make check-prom check-blackbox`
Expected: `SUCCESS` from `promtool check config`, `Unit Testing: /etc/prometheus/tests/probes_test.yml  SUCCESS`, and the blackbox config check exits 0

- [ ] **Step 11: Commit**

```bash
git add prometheus blackbox tests/test_probe_config.py
git commit -m "feat: probe every public endpoint, with down, home-loss and certificate alerts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Host and meta alert rules

**Files:**
- Modify: `prometheus/rules/hosts.yml`, `prometheus/rules/meta.yml` (replace the Task 3 placeholders)
- Test: `prometheus/tests/hosts_test.yml`, `prometheus/tests/meta_test.yml`

**Interfaces:**
- Consumes: the Task 2 metric names `pi_throttle_state{condition, when}`, `pi_throttle_collector_success` and `pi_throttle_collector_last_run_timestamp_seconds`, and the `host` label from Task 3's node targets.
- Produces: alerts `HostDown` (critical, carries `host`), `DiskFullSoon` (critical), `DiskFullInDays`, `MemoryPressure`, `ClockDrift`, `PiThrottled`, `PiCollectorStale`, `ScrapeTargetDown`, `PrometheusRuleFailures` and `AlertNotificationsFailing` (all warning), plus `Watchdog` (severity `none`). Task 5 routes `Watchdog` by name and inhibits on `HostDown` by name.

- [ ] **Step 1: Write the failing tests** in `prometheus/tests/hosts_test.yml`

```yaml
rule_files:
  - ../rules/hosts.yml

evaluation_interval: 30s

tests:
  - name: HostDown pages after three minutes of failed scrapes
    interval: 1m
    input_series:
      - series: 'up{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '1 1 0 0 0 0 0 0 0'
    alert_rule_test:
      - eval_time: 4m
        alertname: HostDown
        exp_alerts: []
      - eval_time: 6m
        alertname: HostDown
        exp_alerts:
          - exp_labels: {severity: critical, job: node, instance: "127.0.0.1:9100", host: command-center}
            exp_annotations: {summary: "command-center is not answering scrapes"}

  - name: a steadily filling disk warns days ahead and pages hours ahead
    interval: 10m
    input_series:
      - series: 'node_filesystem_avail_bytes{job="node",instance="127.0.0.1:9100",host="command-center",device="/dev/mmcblk0p2",fstype="ext4",mountpoint="/"}'
        values: '24000000000-200000000x120'
      - series: 'node_filesystem_size_bytes{job="node",instance="127.0.0.1:9100",host="command-center",device="/dev/mmcblk0p2",fstype="ext4",mountpoint="/"}'
        values: '100000000000x120'
    alert_rule_test:
      - eval_time: 12h
        alertname: DiskFullSoon
        exp_alerts: []
      - eval_time: 12h
        alertname: DiskFullInDays
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "127.0.0.1:9100", host: command-center, device: /dev/mmcblk0p2, fstype: ext4, mountpoint: /}
            exp_annotations: {summary: "command-center / will fill within 3 days at the current rate"}
      - eval_time: 18h
        alertname: DiskFullSoon
        exp_alerts:
          - exp_labels: {severity: critical, job: node, instance: "127.0.0.1:9100", host: command-center, device: /dev/mmcblk0p2, fstype: ext4, mountpoint: /}
            exp_annotations: {summary: "command-center / will fill within 4 hours at the current rate"}

  - name: a nearly full but stable disk does not alert
    interval: 10m
    input_series:
      - series: 'node_filesystem_avail_bytes{job="node",instance="127.0.0.1:9100",host="command-center",device="/dev/mmcblk0p2",fstype="ext4",mountpoint="/"}'
        values: '5000000000x60'
      - series: 'node_filesystem_size_bytes{job="node",instance="127.0.0.1:9100",host="command-center",device="/dev/mmcblk0p2",fstype="ext4",mountpoint="/"}'
        values: '100000000000x60'
    alert_rule_test:
      - eval_time: 9h
        alertname: DiskFullSoon
        exp_alerts: []
      - eval_time: 9h
        alertname: DiskFullInDays
        exp_alerts: []

  - name: memory pressure needs fifteen minutes under 10 percent
    interval: 1m
    input_series:
      - series: 'node_memory_MemAvailable_bytes{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '300000000x20'
      - series: 'node_memory_MemTotal_bytes{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '4000000000x20'
    alert_rule_test:
      - eval_time: 10m
        alertname: MemoryPressure
        exp_alerts: []
      - eval_time: 16m
        alertname: MemoryPressure
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "127.0.0.1:9100", host: command-center}
            exp_annotations: {summary: "command-center has under 10% memory available"}

  - name: clock drift counts in either direction
    interval: 1m
    input_series:
      - series: 'node_timex_offset_seconds{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '-0.08x15'
    alert_rule_test:
      - eval_time: 9m
        alertname: ClockDrift
        exp_alerts: []
      - eval_time: 11m
        alertname: ClockDrift
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "127.0.0.1:9100", host: command-center}
            exp_annotations: {summary: "command-center clock is more than 50 ms off"}

  - name: a past under-voltage does not alert; a current one does
    interval: 1m
    input_series:
      - series: 'pi_throttle_state{job="node",instance="127.0.0.1:9100",host="command-center",condition="undervoltage",when="since_boot"}'
        values: '1x15'
      - series: 'pi_throttle_state{job="node",instance="10.0.0.205:9900",host="lapi-witness",condition="undervoltage",when="now"}'
        values: '0 0 0 0 1 1 1 1 1 1 1 1 1 1 1'
    alert_rule_test:
      - eval_time: 7m
        alertname: PiThrottled
        exp_alerts: []
      - eval_time: 12m
        alertname: PiThrottled
        exp_alerts:
          - exp_labels: {severity: warning, host: lapi-witness}
            exp_annotations: {summary: "lapi-witness is under-volted or throttled right now"}

  - name: a throttle collector that stops writing is noticed
    interval: 1m
    input_series:
      - series: 'pi_throttle_collector_last_run_timestamp_seconds{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '0x30'
      - series: 'pi_throttle_collector_success{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '1x30'
    alert_rule_test:
      - eval_time: 15m
        alertname: PiCollectorStale
        exp_alerts: []
      - eval_time: 21m
        alertname: PiCollectorStale
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "127.0.0.1:9100", host: command-center}
            exp_annotations: {summary: "command-center throttle collector is failing or has stopped"}

  - name: a throttle collector that cannot read vcgencmd is noticed
    interval: 1m
    input_series:
      - series: 'pi_throttle_collector_last_run_timestamp_seconds{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '0+60x30'
      - series: 'pi_throttle_collector_success{job="node",instance="127.0.0.1:9100",host="command-center"}'
        values: '0x30'
    alert_rule_test:
      - eval_time: 11m
        alertname: PiCollectorStale
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "127.0.0.1:9100", host: command-center}
            exp_annotations: {summary: "command-center throttle collector is failing or has stopped"}
```

- [ ] **Step 2: Write the failing tests** in `prometheus/tests/meta_test.yml`

```yaml
rule_files:
  - ../rules/meta.yml

evaluation_interval: 30s

tests:
  - name: the watchdog is always firing
    interval: 1m
    alert_rule_test:
      - eval_time: 1m
        alertname: Watchdog
        exp_alerts:
          - exp_labels: {severity: none}
            exp_annotations: {summary: "Always firing; proves the alerting pipeline is alive."}

  - name: non-node targets down for five minutes warn once per job
    interval: 1m
    input_series:
      - series: 'up{job="blackbox",instance="https://a.example/"}'
        values: '0x10'
      - series: 'up{job="blackbox",instance="https://b.example/"}'
        values: '0x10'
      - series: 'up{job="node",instance="127.0.0.1:9100"}'
        values: '0x10'
    alert_rule_test:
      - eval_time: 4m
        alertname: ScrapeTargetDown
        exp_alerts: []
      - eval_time: 6m
        alertname: ScrapeTargetDown
        exp_alerts:
          - exp_labels: {severity: warning, job: blackbox}
            exp_annotations: {summary: "2 blackbox target(s) are not answering scrapes"}

  - name: rule evaluation failures warn
    interval: 1m
    input_series:
      - series: 'prometheus_rule_evaluation_failures_total{rule_group="/etc/prometheus/rules/hosts.yml;hosts"}'
        values: '0 0 1 2 3'
    alert_rule_test:
      - eval_time: 4m
        alertname: PrometheusRuleFailures
        exp_alerts:
          - exp_labels: {severity: warning, rule_group: "/etc/prometheus/rules/hosts.yml;hosts"}
            exp_annotations: {summary: "Prometheus rule group /etc/prometheus/rules/hosts.yml;hosts is failing to evaluate"}

  - name: failed notifications warn
    interval: 1m
    input_series:
      - series: 'alertmanager_notifications_failed_total{integration="pushover"}'
        values: '0 0 1 1 1'
    alert_rule_test:
      - eval_time: 4m
        alertname: AlertNotificationsFailing
        exp_alerts:
          - exp_labels: {severity: warning, integration: pushover}
            exp_annotations: {summary: "Alertmanager failed to deliver to pushover"}
```

- [ ] **Step 3: Run them to verify they fail**

Run: `make check-prom`
Expected: FAIL. The unit tests report `expected ... got []` for HostDown, Watchdog and the rest, because the rule files are still `groups: []`.

- [ ] **Step 4: Replace `prometheus/rules/hosts.yml`**

```yaml
groups:
  - name: hosts
    rules:
      - alert: HostDown
        expr: up{job="node"} == 0
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "{{ $labels.host }} is not answering scrapes"

      # Forecasts only count when the disk is already fairly full, so a big
      # transient write on an empty disk does not page anyone.
      - alert: DiskFullSoon
        expr: >
          predict_linear(node_filesystem_avail_bytes{fstype!~"tmpfs|ramfs|squashfs|overlay"}[6h], 4 * 3600) < 0
          and on (instance, device, mountpoint)
          node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.25
        for: 30m
        labels:
          severity: critical
        annotations:
          summary: "{{ $labels.host }} {{ $labels.mountpoint }} will fill within 4 hours at the current rate"

      - alert: DiskFullInDays
        expr: >
          predict_linear(node_filesystem_avail_bytes{fstype!~"tmpfs|ramfs|squashfs|overlay"}[24h], 3 * 86400) < 0
          and on (instance, device, mountpoint)
          node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.40
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.host }} {{ $labels.mountpoint }} will fill within 3 days at the current rate"

      - alert: MemoryPressure
        expr: node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.10
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.host }} has under 10% memory available"

      - alert: ClockDrift
        expr: abs(node_timex_offset_seconds) > 0.05
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.host }} clock is more than 50 ms off"

      - alert: PiThrottled
        expr: max by (host) (pi_throttle_state{when="now"}) > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.host }} is under-volted or throttled right now"

      # The aggregation drops the metric name, so both sides of `or` carry the
      # same labels and a collector that is both stale and failing yields one alert.
      - alert: PiCollectorStale
        expr: >
          (time() - pi_throttle_collector_last_run_timestamp_seconds > 600)
          or (max by (job, instance, host) (pi_throttle_collector_success) == 0)
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "{{ $labels.host }} throttle collector is failing or has stopped"
```

- [ ] **Step 5: Replace `prometheus/rules/meta.yml`**

```yaml
groups:
  - name: meta
    rules:
      # Routed to healthchecks.io every minute; silence from it is the page.
      - alert: Watchdog
        expr: vector(1)
        labels:
          severity: none
        annotations:
          summary: "Always firing; proves the alerting pipeline is alive."

      # node targets are HostDown's; one alert per job, not per probe.
      - alert: ScrapeTargetDown
        expr: count by (job) (up{job!="node"} == 0) > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "{{ $value }} {{ $labels.job }} target(s) are not answering scrapes"

      - alert: PrometheusRuleFailures
        expr: increase(prometheus_rule_evaluation_failures_total[10m]) > 0
        labels:
          severity: warning
        annotations:
          summary: "Prometheus rule group {{ $labels.rule_group }} is failing to evaluate"

      - alert: AlertNotificationsFailing
        expr: increase(alertmanager_notifications_failed_total[15m]) > 0
        labels:
          severity: warning
        annotations:
          summary: "Alertmanager failed to deliver to {{ $labels.integration }}"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make check-prom`
Expected: `SUCCESS` for `hosts_test.yml`, `meta_test.yml` and `probes_test.yml`

- [ ] **Step 7: Commit**

```bash
git add prometheus/rules prometheus/tests
git commit -m "feat: host, Pi throttling and self-monitoring alerts with rule tests" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Alertmanager routing, inhibition and receivers

**Files:**
- Create: `alertmanager/alertmanager.yml.tmpl`, `alertmanager/routes.test`, `scripts/check_routes.sh`

**Interfaces:**
- Consumes: `scripts/render_config.py` (Task 1); the alert names `Watchdog`, `HostDown`, `HomeConnectivityLost` and `ProbeDown` and the severity labels (Tasks 3 and 4); `${PUSHOVER_USER_KEY}`, `${PUSHOVER_APP_TOKEN}` and `${HEALTHCHECKS_PING_URL}` from `.env.example`.
- Produces: receivers `heartbeat`, `pushover-critical`, `pushover-warning` and `blackhole`. Any alert labelled `drill="true"` routes to `blackhole`, and Task 9 relies on that. CLI: `scripts/check_routes.sh IMAGE CONFIG ROUTES_FILE` exits non-zero on the first mismatch it reports.

- [ ] **Step 1: Write the routing table (the test)** in `alertmanager/routes.test`

```
# labels => receiver they must reach. Checked by scripts/check_routes.sh.
alertname=Watchdog severity=none => heartbeat
alertname=ProbeDown severity=critical service=ddcloud => pushover-critical
alertname=HostDown severity=critical host=command-center => pushover-critical
alertname=CertExpiringSoon severity=warning service=vaulterm-site => pushover-warning
alertname=Anything severity=info => blackhole
alertname=ProbeDown severity=critical drill=true => blackhole
alertname=RuleWithoutSeverity => pushover-warning
```

- [ ] **Step 2: Create `scripts/check_routes.sh`**

```bash
#!/usr/bin/env bash
# Checks that each label set in a routes file reaches the receiver it names.
# Usage: check_routes.sh ALERTMANAGER_IMAGE CONFIG ROUTES_FILE
set -euo pipefail
image=$1 config=$2 routes=$3
failures=0 cases=0
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  labels=${line%% => *}
  expected=${line##* => }
  cases=$((cases + 1))
  # shellcheck disable=SC2086  # labels are deliberately split into amtool arguments
  actual=$(docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w --entrypoint amtool "$image" \
    config routes test --config.file="$config" $labels)
  if [[ "$actual" != "$expected" ]]; then
    echo "route mismatch: {$labels} reached '$actual', expected '$expected'" >&2
    failures=$((failures + 1))
  fi
done < "$routes"
if (( failures > 0 )); then
  exit 1
fi
echo "routes: all $cases cases reach the expected receiver"
```

Then: `chmod +x scripts/check_routes.sh`

- [ ] **Step 3: Run it to verify it fails**

Run: `make check-am`
Expected: FAIL. `render_config: [Errno 2] No such file or directory: 'alertmanager/alertmanager.yml.tmpl'`

- [ ] **Step 4: Create `alertmanager/alertmanager.yml.tmpl`**

```yaml
# Rendered on the Pi by scripts/render_config.py from .env. The ${NAME}
# placeholders are secrets; everything else is plain Alertmanager config.
global:
  resolve_timeout: 5m

route:
  receiver: pushover-warning
  group_by: [alertname, service, host]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # One webhook a minute to healthchecks.io; its silence is the page.
    - matchers: ['alertname="Watchdog"']
      receiver: heartbeat
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 1m
    # Fire drills exercise routing and inhibition without paging.
    - matchers: ['drill="true"']
      receiver: blackhole
    - matchers: ['severity="critical"']
      receiver: pushover-critical
      repeat_interval: 1h
    - matchers: ['severity="warning"']
      receiver: pushover-warning
    - matchers: ['severity="info"']
      receiver: blackhole

inhibit_rules:
  # One home-connection warning instead of a page per site.
  - source_matchers: ['alertname="HomeConnectivityLost"']
    target_matchers: ['alertname="ProbeDown"']
  # A host that cannot be scraped says nothing reliable about anything on it.
  - source_matchers: ['alertname="HostDown"']
    target_matchers: ['alertname!="HostDown"']
    equal: [host]

receivers:
  - name: heartbeat
    webhook_configs:
      - url: '${HEALTHCHECKS_PING_URL}'
        send_resolved: false

  - name: pushover-critical
    pushover_configs:
      - user_key: '${PUSHOVER_USER_KEY}'
        token: '${PUSHOVER_APP_TOKEN}'
        priority: '2'
        retry: 1m
        expire: 1h
        title: '🔴 {{ .CommonLabels.alertname }}{{ if eq .Status "resolved" }} resolved{{ end }}'
        message: |-
          {{ range .Alerts }}{{ .Annotations.summary }}
          {{ end }}
        url: 'http://10.0.0.249:3000/d/command-center'
        send_resolved: true

  - name: pushover-warning
    pushover_configs:
      - user_key: '${PUSHOVER_USER_KEY}'
        token: '${PUSHOVER_APP_TOKEN}'
        priority: '0'
        title: '🟡 {{ .CommonLabels.alertname }}{{ if eq .Status "resolved" }} resolved{{ end }}'
        message: |-
          {{ range .Alerts }}{{ .Annotations.summary }}
          {{ end }}
        url: 'http://10.0.0.249:3000/d/command-center'
        send_resolved: true

  - name: blackhole
```

- [ ] **Step 5: Run it to verify it passes**

Run: `make check-am`
Expected: `amtool check-config` prints `SUCCESS` and lists 4 receivers, followed by `routes: all 7 cases reach the expected receiver`

- [ ] **Step 6: Commit**

```bash
git add alertmanager scripts/check_routes.sh
git commit -m "feat: severity routing to Pushover, heartbeat to healthchecks, outage inhibitions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Grafana provisioning and dashboards

**Files:**
- Create: `grafana/provisioning/datasources/prometheus.yml`, `grafana/provisioning/dashboards/command-center.yml`, `grafana/dashboards/command-center.json`, `grafana/dashboards/probes.json`, `grafana/dashboards/hosts.json`, `scripts/check_dashboards.py`
- Test: `tests/test_check_dashboards.py`

**Interfaces:**
- Consumes: the metric and label names from Tasks 2 and 3 (`probe_*` with `service`; node metrics and `pi_throttle_state` with `host`).
- Produces: the data source uid `prometheus`, and dashboard uids `command-center` (Pushover links point at `/d/command-center`), `probes` and `hosts`. `check_dashboards.py` provides `lint_dashboard(doc: dict) -> list[str]`, `lint_dir(directory: Path) -> list[str]` and `main(argv: list[str]) -> int`.

- [ ] **Step 1: Write the failing tests** in `tests/test_check_dashboards.py`

```python
import copy
import json
from pathlib import Path

from check_dashboards import lint_dashboard, lint_dir

DS = {"type": "prometheus", "uid": "prometheus"}


def good(**overrides):
    doc = {
        "uid": "x",
        "title": "X",
        "panels": [
            {
                "id": 1,
                "type": "stat",
                "title": "p",
                "datasource": dict(DS),
                "targets": [{"refId": "A", "expr": "up", "datasource": dict(DS)}],
            }
        ],
    }
    doc.update(overrides)
    return doc


def test_good_dashboard_passes():
    assert lint_dashboard(good()) == []


def test_missing_uid_is_reported():
    assert "missing uid" in lint_dashboard(good(uid=""))


def test_unprovisioned_datasource_is_reported():
    doc = good()
    doc["panels"][0]["targets"][0]["datasource"]["uid"] = "P1809F7CD0C75ACF3"
    assert any("not provisioned" in p for p in lint_dashboard(doc))


def test_duplicate_panel_ids_are_reported():
    doc = good()
    doc["panels"].append(copy.deepcopy(doc["panels"][0]))
    assert any("duplicate panel ids [1]" in p for p in lint_dashboard(doc))


def test_target_without_expr_is_reported():
    doc = good()
    doc["panels"][0]["targets"][0]["expr"] = ""
    assert any("without expr" in p for p in lint_dashboard(doc))


def test_uid_shared_across_files_is_reported(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(good()))
    (tmp_path / "b.json").write_text(json.dumps(good(title="Y")))
    assert any("already used by a.json" in p for p in lint_dir(tmp_path))


def test_repo_dashboards_are_clean():
    assert lint_dir(Path(__file__).resolve().parent.parent / "grafana" / "dashboards") == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_check_dashboards.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'check_dashboards'`

- [ ] **Step 3: Implement `scripts/check_dashboards.py`**

```python
#!/usr/bin/env python3
"""Lints the provisioned Grafana dashboards before they reach the Pi.

Grafana loads a broken dashboard without complaint. A missing uid gives it a new
URL on every restart, which breaks the links in Pushover messages, and a
datasource uid that is not provisioned renders every panel as "datasource not
found". Both are cheap to catch here and tedious to diagnose from a phone.
"""

import json
import sys
from pathlib import Path

DATASOURCE_UID = "prometheus"


def _datasource_uids(node):
    if isinstance(node, dict):
        ds = node.get("datasource")
        if isinstance(ds, dict) and "uid" in ds:
            yield ds["uid"]
        for value in node.values():
            yield from _datasource_uids(value)
    elif isinstance(node, list):
        for item in node:
            yield from _datasource_uids(item)


def lint_dashboard(doc: dict) -> list[str]:
    problems = [f"missing {key}" for key in ("uid", "title") if not doc.get(key)]
    panels = doc.get("panels", [])
    ids = [panel.get("id") for panel in panels]
    if None in ids:
        problems.append("a panel has no id")
    dupes = sorted({i for i in ids if i is not None and ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate panel ids {dupes}")
    for uid in _datasource_uids([panels, doc.get("templating", {})]):
        if uid != DATASOURCE_UID:
            problems.append(f"datasource uid {uid!r} is not provisioned (expected {DATASOURCE_UID!r})")
    for panel in panels:
        for target in panel.get("targets", []):
            if not target.get("expr") or not target.get("refId"):
                problems.append(f"panel {panel.get('title')!r} has a target without expr or refId")
    return problems


def lint_dir(directory: Path) -> list[str]:
    files = sorted(directory.glob("*.json"))
    if not files:
        return [f"no dashboards in {directory}"]
    problems, seen = [], {}
    for path in files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: invalid JSON: {exc}")
            continue
        problems += [f"{path.name}: {problem}" for problem in lint_dashboard(doc)]
        uid = doc.get("uid")
        if uid and uid in seen:
            problems.append(f"{path.name}: uid {uid!r} already used by {seen[uid]}")
        elif uid:
            seen[uid] = path.name
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_dashboards.py DIRECTORY", file=sys.stderr)
        return 2
    directory = Path(argv[1])
    problems = lint_dir(directory)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"dashboards: {len(list(directory.glob('*.json')))} clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests. All but the repo test pass**

Run: `.venv/bin/pytest tests/test_check_dashboards.py -q`
Expected: `6 passed, 1 failed`. `test_repo_dashboards_are_clean` fails with `no dashboards in .../grafana/dashboards`.

- [ ] **Step 5: Create the provisioning files**

`grafana/provisioning/datasources/prometheus.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://127.0.0.1:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: 30s
```

`grafana/provisioning/dashboards/command-center.yml`:

```yaml
apiVersion: 1
providers:
  - name: command-center
    folder: Command Center
    type: file
    disableDeletion: true
    allowUiUpdates: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/dashboards
```

- [ ] **Step 6: Create `grafana/dashboards/command-center.json`**

```json
{
  "uid": "command-center",
  "title": "Command Center",
  "tags": ["command-center"],
  "timezone": "browser",
  "schemaVersion": 39,
  "refresh": "30s",
  "time": {"from": "now-6h", "to": "now"},
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "Firing alerts",
      "gridPos": {"x": 0, "y": 0, "w": 6, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "count(ALERTS{alertstate=\"firing\", alertname!=\"Watchdog\"}) or vector(0)",
          "instant": true
        }
      ],
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 1}]}
        },
        "overrides": []
      },
      "options": {"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}}
    },
    {
      "id": 2,
      "type": "stat",
      "title": "Services",
      "gridPos": {"x": 6, "y": 0, "w": 18, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "min by (service) (probe_success{job=\"blackbox\"})",
          "legendFormat": "{{service}}",
          "instant": true
        }
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}],
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": null}, {"color": "green", "value": 1}]}
        },
        "overrides": []
      },
      "options": {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}}
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Response time by service",
      "gridPos": {"x": 0, "y": 8, "w": 12, "h": 9},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "max by (service) (probe_duration_seconds{job=\"blackbox\"})",
          "legendFormat": "{{service}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "s"}, "overrides": []}
    },
    {
      "id": 4,
      "type": "bargauge",
      "title": "Certificate days left",
      "gridPos": {"x": 12, "y": 8, "w": 12, "h": 9},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "min by (service) ((probe_ssl_earliest_cert_expiry{job=\"blackbox\"} - time()) / 86400)",
          "legendFormat": "{{service}}",
          "instant": true
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "d",
          "min": 0,
          "max": 90,
          "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": null}, {"color": "orange", "value": 14}, {"color": "green", "value": 30}]}
        },
        "overrides": []
      },
      "options": {"orientation": "horizontal", "displayMode": "gradient", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}}
    }
  ]
}
```

- [ ] **Step 7: Create `grafana/dashboards/probes.json`**

```json
{
  "uid": "probes",
  "title": "Probes and certificates",
  "tags": ["command-center"],
  "timezone": "browser",
  "schemaVersion": 39,
  "refresh": "1m",
  "time": {"from": "now-24h", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "service",
        "label": "Service",
        "type": "query",
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "definition": "label_values(probe_success{job=\"blackbox\"}, service)",
        "query": {"query": "label_values(probe_success{job=\"blackbox\"}, service)", "refId": "service"},
        "includeAll": true,
        "multi": true,
        "refresh": 2,
        "current": {"text": "All", "value": "$__all"}
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "type": "state-timeline",
      "title": "Up or down",
      "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "probe_success{job=\"blackbox\", service=~\"$service\"}",
          "legendFormat": "{{instance}}"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
        },
        "overrides": []
      }
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Where the time goes",
      "gridPos": {"x": 0, "y": 8, "w": 16, "h": 9},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "sum by (phase) (probe_http_duration_seconds{job=\"blackbox\", service=~\"$service\"})",
          "legendFormat": "{{phase}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "s", "custom": {"stacking": {"mode": "normal"}, "fillOpacity": 30}}, "overrides": []}
    },
    {
      "id": 3,
      "type": "stat",
      "title": "HTTP status",
      "gridPos": {"x": 16, "y": 8, "w": 8, "h": 9},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "probe_http_status_code{job=\"blackbox\", service=~\"$service\"}",
          "legendFormat": "{{instance}}",
          "instant": true
        }
      ],
      "options": {"colorMode": "none", "graphMode": "none", "textMode": "value_and_name", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}}
    },
    {
      "id": 4,
      "type": "table",
      "title": "Certificate days left",
      "gridPos": {"x": 0, "y": 17, "w": 24, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "sort((probe_ssl_earliest_cert_expiry{job=\"blackbox\"} - time()) / 86400)",
          "format": "table",
          "instant": true
        }
      ],
      "fieldConfig": {"defaults": {"unit": "d", "decimals": 0}, "overrides": []}
    }
  ]
}
```

- [ ] **Step 8: Create `grafana/dashboards/hosts.json`**

```json
{
  "uid": "hosts",
  "title": "Hosts",
  "tags": ["command-center"],
  "timezone": "browser",
  "schemaVersion": 39,
  "refresh": "1m",
  "time": {"from": "now-24h", "to": "now"},
  "templating": {
    "list": [
      {
        "name": "host",
        "label": "Host",
        "type": "query",
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "definition": "label_values(node_uname_info, host)",
        "query": {"query": "label_values(node_uname_info, host)", "refId": "host"},
        "includeAll": true,
        "multi": true,
        "refresh": 2,
        "current": {"text": "All", "value": "$__all"}
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "CPU saturation (load per core)",
      "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "node_load5{host=~\"$host\"} / on (host) count by (host) (node_cpu_seconds_total{mode=\"idle\", host=~\"$host\"})",
          "legendFormat": "{{host}}"
        }
      ]
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Memory available",
      "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "100 * node_memory_MemAvailable_bytes{host=~\"$host\"} / node_memory_MemTotal_bytes{host=~\"$host\"}",
          "legendFormat": "{{host}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}, "overrides": []}
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Filesystem space available",
      "gridPos": {"x": 0, "y": 8, "w": 12, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "100 * node_filesystem_avail_bytes{host=~\"$host\", fstype!~\"tmpfs|ramfs|squashfs|overlay\"} / node_filesystem_size_bytes{host=~\"$host\", fstype!~\"tmpfs|ramfs|squashfs|overlay\"}",
          "legendFormat": "{{host}} {{mountpoint}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}, "overrides": []}
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "Disk write latency",
      "gridPos": {"x": 12, "y": 8, "w": 12, "h": 8},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "rate(node_disk_write_time_seconds_total{host=~\"$host\"}[5m]) / rate(node_disk_writes_completed_total{host=~\"$host\"}[5m])",
          "legendFormat": "{{host}} {{device}}"
        }
      ],
      "fieldConfig": {"defaults": {"unit": "s"}, "overrides": []}
    },
    {
      "id": 5,
      "type": "state-timeline",
      "title": "Pi throttling (now)",
      "gridPos": {"x": 0, "y": 16, "w": 24, "h": 7},
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "pi_throttle_state{host=~\"$host\", when=\"now\"}",
          "legendFormat": "{{host}} {{condition}}"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [{"type": "value", "options": {"0": {"text": "ok", "color": "green"}, "1": {"text": "ACTIVE", "color": "red"}}}]
        },
        "overrides": []
      }
    }
  ]
}
```

- [ ] **Step 9: Run the tests and the lint to verify they pass**

Run: `.venv/bin/pytest tests/test_check_dashboards.py -q && make check-dashboards`
Expected: `7 passed`, then `dashboards: 3 clean`

- [ ] **Step 10: Commit**

```bash
git add grafana scripts/check_dashboards.py tests/test_check_dashboards.py
git commit -m "feat: provisioned Grafana with command center, probes and hosts dashboards" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Apply, deploy and bootstrap scripts

**Files:**
- Create: `scripts/apply.sh`, `scripts/deploy.sh`, `scripts/bootstrap-pi.sh`, `README.md`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: `render_config.py` (Task 1); `make check-configs AM_CONFIG=...` (Task 1 Makefile); compose service names (Task 1); `hosts/pi/pi-throttled.{service,timer}` (Task 2).
- Produces: `scripts/deploy.sh` (workstation; honours `COMMAND_CENTER_HOST`, default `command-center`); `scripts/apply.sh` (Pi; exits non-zero before any `make`, `docker` or `curl` call when rendering fails); `scripts/bootstrap-pi.sh` (Pi, root, run once by the user from any copy of the repo).

- [ ] **Step 1: Write the failing tests** in `tests/test_scripts.py`

```python
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "scripts").glob("*.sh"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_scripts_never_call_bare_ssh():
    # On the workstation `ssh` is kitty's kitten and fails outside a kitty window.
    for script in SCRIPTS:
        for line in script.read_text().splitlines():
            code = line.split("#", 1)[0]
            assert not re.search(r"(^|[\s;|&(\"])ssh\s", code), f"{script.name}: use /usr/bin/ssh: {line}"


def test_alertmanager_mounts_the_run_directory_not_the_file():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text())
    volumes = compose["services"]["alertmanager"]["volumes"]
    assert "./run:/etc/alertmanager/run:ro" in volumes
    assert not any(v.split(":")[0].endswith(".yml") for v in volumes)


def stage(tmp_path, env_text):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".venv", "run", "__pycache__", ".pytest_cache"))
    (repo / ".env").write_text(env_text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("docker", "make", "curl"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return repo, env, log


def test_apply_stops_before_docker_when_a_secret_is_missing(tmp_path):
    repo, env, log = stage(tmp_path, "PUSHOVER_USER_KEY=u\nGRAFANA_ADMIN_PASSWORD=p\n")
    result = subprocess.run(["bash", "scripts/apply.sh"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "${PUSHOVER_APP_TOKEN}" in result.stderr
    assert "${HEALTHCHECKS_PING_URL}" in result.stderr
    assert not log.exists(), "nothing may be validated, pulled or restarted"
    assert not (repo / "run" / "alertmanager.yml").exists()


def test_apply_validates_then_converges_then_reloads(tmp_path):
    repo, env, log = stage(tmp_path, (ROOT / ".env.example").read_text())
    result = subprocess.run(["bash", "scripts/apply.sh"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()

    def first(prefix):
        return next(i for i, call in enumerate(calls) if call.startswith(prefix))

    assert first("make check-configs AM_CONFIG=run/alertmanager.yml.new") < first("docker compose config")
    assert first("docker compose config") < first("docker compose up -d")
    assert first("docker compose up -d") < first("curl -fsS -X POST http://127.0.0.1:9090/-/reload")
    rendered = repo / "run" / "alertmanager.yml"
    assert rendered.stat().st_mode & 0o777 == 0o600
    assert not (repo / "run" / "alertmanager.yml.new").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_scripts.py -q`
Expected: the parse and bare-ssh tests pass for the existing `check_routes.sh`, and the compose mount test passes. Both `test_apply_*` fail: `bash: scripts/apply.sh: No such file or directory`.

- [ ] **Step 3: Create `scripts/apply.sh`**

```bash
#!/usr/bin/env bash
# Runs on the Pi. Renders the secrets, validates everything with the pinned
# images, and only then touches the running stack. Any failure before
# `docker compose up` leaves the stack exactly as it was.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "apply: no .env in $PWD (copy .env.example and fill it in)" >&2; exit 1; }
mkdir -p run
python3 scripts/render_config.py alertmanager/alertmanager.yml.tmpl .env run/alertmanager.yml.new
make check-configs AM_CONFIG=run/alertmanager.yml.new
docker compose config --quiet
mv run/alertmanager.yml.new run/alertmanager.yml
docker compose up -d --remove-orphans

wait_ready() {
  for _ in $(seq 1 30); do
    curl -fsS -o /dev/null "$1" && return 0
    sleep 2
  done
  echo "apply: $1 not ready after 60s" >&2
  return 1
}
wait_ready http://127.0.0.1:9090/-/ready
wait_ready http://127.0.0.1:9093/-/ready
# Containers that were not recreated are still running the old config.
curl -fsS -X POST http://127.0.0.1:9090/-/reload
curl -fsS -X POST http://127.0.0.1:9093/-/reload
docker compose ps
```

Then: `chmod +x scripts/apply.sh`

- [ ] **Step 4: Create `scripts/deploy.sh`**

```bash
#!/usr/bin/env bash
# Ships this repo to the command-center Pi and applies it there.
# COMMAND_CENTER_HOST overrides the ssh host (default: command-center).
set -euo pipefail
cd "$(dirname "$0")/.."
host=${COMMAND_CENTER_HOST:-command-center}
remote_shell=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation

make check
# .env and run/ are excluded, which also protects the Pi's copies from --delete.
rsync -az --delete -e "$remote_shell" \
  --exclude=.git/ --exclude=.venv/ --exclude=run/ --exclude=.env \
  --exclude=__pycache__/ --exclude=.pytest_cache/ \
  ./ "$host:/opt/command-center/"
"$remote_shell" "$host" /opt/command-center/scripts/apply.sh
```

Then: `chmod +x scripts/deploy.sh`

- [ ] **Step 5: Create `scripts/bootstrap-pi.sh`**

```bash
#!/usr/bin/env bash
# One-time setup of the command-center Pi, run by its owner:
#   sudo bash <copy of this repo>/scripts/bootstrap-pi.sh
# Installs Docker, spares the SD card, creates the directories the stack uses,
# copies the repo to /opt/command-center and starts the Pi throttle collector.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "bootstrap: run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run via sudo from the account that will own the stack}
src=$(cd "$(dirname "$0")/.." && pwd)

# Docker from Docker's repository: Debian's packages lack the compose plugin.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck source=/dev/null
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin rsync make
usermod -aG docker "$owner"
install -d /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "local", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
systemctl restart docker

# Keep the journal in RAM; the SD card has enough to do.
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n' > /etc/systemd/journald.conf.d/10-volatile.conf
systemctl restart systemd-journald

install -d -o "$owner" -g "$owner" /opt/command-center /srv/command-center \
  /srv/command-center/prometheus /srv/command-center/alertmanager /srv/command-center/grafana \
  /var/lib/node_exporter /var/lib/node_exporter/textfile
if [[ "$src" != /opt/command-center ]]; then
  cp -a "$src"/. /opt/command-center/
  chown -R "$owner:$owner" /opt/command-center
fi

install -m 0644 /opt/command-center/hosts/pi/pi-throttled.service /opt/command-center/hosts/pi/pi-throttled.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pi-throttled.timer

findmnt -no OPTIONS / | grep -q noatime || echo "bootstrap: WARNING: / is not mounted noatime; add it in /etc/fstab"
echo "bootstrap: done. Open a new ssh session so $owner picks up the docker group."
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scripts.py -q`
Expected: `8 passed` (four scripts parse, bare ssh, compose mount, two apply tests)

- [ ] **Step 7: Create `README.md`**

````markdown
# Command Center

Grafana + Prometheus on the `command-center` Pi (10.0.0.249), watching every
service from outside and, from phase 2, from inside. Design:
`docs/superpowers/specs/2026-09-27-command-center-design.md`.

## Using it

- Dashboards: http://10.0.0.249:3000 (home network only), folder **Command Center**.
- Alerts arrive through Pushover: 🔴 breaks through Do Not Disturb and repeats
  until acknowledged, 🟡 is a quiet push. If the Pi or the home connection goes
  silent, healthchecks.io pages instead.

## Changing it

Everything is in this repo; nothing is edited in Grafana.

```bash
make check    # unit tests, promtool rule tests, amtool routing tests, dashboard lint
make deploy   # check, rsync to the Pi, validate again there, converge, reload
```

- **Add a probe:** add the URL under the right `service` in
  `prometheus/targets/probes.yml`, choosing `http_2xx`, `http_any_answer` or
  `http_405` from `blackbox/blackbox.yml`.
- **Add an alert:** add the rule in `prometheus/rules/`, with a firing and a
  non-firing case in `prometheus/tests/`.
- **Secrets** live only in `/opt/command-center/.env` on the Pi (see
  `.env.example`). Edit them there with
  `/usr/bin/ssh -t command-center nano /opt/command-center/.env`, then `make deploy`.

## Pi layout

| Path | What |
|---|---|
| `/opt/command-center` | this repo, plus `.env` and the rendered `run/alertmanager.yml` |
| `/srv/command-center/{prometheus,alertmanager,grafana}` | data; mount the USB drive here when it is fitted |
| `/var/lib/node_exporter/textfile` | textfile collector output |
````

- [ ] **Step 8: Run the full check**

Run: `make check`
Expected: pytest `36 passed`, all three promtool unit tests `SUCCESS`, the blackbox config check exits 0, amtool `SUCCESS`, `routes: all 7 cases reach the expected receiver`, and `dashboards: 3 clean`

- [ ] **Step 9: Commit**

```bash
git add scripts/apply.sh scripts/deploy.sh scripts/bootstrap-pi.sh tests/test_scripts.py README.md
git commit -m "feat: deploy, apply-on-Pi and one-time bootstrap scripts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Bootstrap the Pi, connect alerting, first deploy

This task is operational: there is no code, and it needs the user at three points. The agent never runs `sudo` on the Pi, never creates accounts, and never sees secret values.

**Interfaces:**
- Consumes: everything above.
- Produces: a running stack on the Pi; healthchecks.io receiving a ping every minute.

- [ ] **Step 1: Stage a copy of the repo on the Pi**

Run: `rsync -az -e /usr/bin/ssh --exclude=.git/ --exclude=.venv/ --exclude=run/ ./ command-center:command-center-bootstrap/`
Expected: exits 0

- [ ] **Step 2: USER runs the bootstrap** (it prompts for their sudo password)

Ask the user to run:

```bash
/usr/bin/ssh -t command-center sudo bash command-center-bootstrap/scripts/bootstrap-pi.sh
```

Expected: the last line is `bootstrap: done. Open a new ssh session so atlas picks up the docker group.` with no noatime warning.

- [ ] **Step 3: Verify the bootstrap and remove the staging copy**

Run: `/usr/bin/ssh command-center 'docker run --rm hello-world >/dev/null && echo docker-ok; docker compose version; systemctl is-active pi-throttled.timer; grep collector_success /var/lib/node_exporter/textfile/pi_throttled.prom; rm -rf ~/command-center-bootstrap'`
Expected: `docker-ok`, a `Docker Compose version v2...` line, `active`, and `pi_throttle_collector_success 1` (the timer's first run is 30 s after enable; if the file is missing, wait 30 s and re-run)

- [ ] **Step 4: USER creates the alerting accounts** (in their own browser)

Ask the user to:
1. **Pushover** (pushover.net): sign in, note the **User Key**, and create an application named **Command Center**, noting its **API Token**. In the Pushover iOS app, allow **Critical Alerts** (Settings → Notifications) so emergency priority breaks through Do Not Disturb.
2. **healthchecks.io**: create a check named **command-center heartbeat** with **Period 1 minute** and **Grace 5 minutes**. Add a **Pushover** integration at **emergency** priority for "down", and note the check's **ping URL**.

- [ ] **Step 5: Create `.env` on the Pi, then USER fills it in**

Run: `/usr/bin/ssh command-center 'cp -n /opt/command-center/.env.example /opt/command-center/.env && chmod 600 /opt/command-center/.env && echo created'`
Expected: `created`

Ask the user to run the following and set `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`, `HEALTHCHECKS_PING_URL` and `GRAFANA_ADMIN_PASSWORD` (leave `PROM_RETENTION_SIZE=12GB`):

```bash
/usr/bin/ssh -t command-center nano /opt/command-center/.env
```

Do not print or read the file afterwards.

- [ ] **Step 6: First deploy**

Run: `make deploy`
Expected: the local checks pass, the Pi pulls five images, the Pi-side checks pass, and `docker compose ps` lists `prometheus`, `alertmanager`, `blackbox`, `node-exporter` and `grafana` as `running`. If apply reports `no value in the env file for ...`, the user fixes `.env` (Step 5) and this step is re-run.

- [ ] **Step 7: Verify every target is up and only the Watchdog fires**

Run:

```bash
/usr/bin/ssh command-center 'curl -s http://127.0.0.1:9090/api/v1/targets' | python3 -c 'import json,sys; [print(t["health"], t["labels"]["job"], t["labels"].get("instance")) for t in json.load(sys.stdin)["data"]["activeTargets"]]'
/usr/bin/ssh command-center 'curl -s http://127.0.0.1:9090/api/v1/alerts' | python3 -c 'import json,sys; print(sorted(a["labels"]["alertname"] for a in json.load(sys.stdin)["data"]["alerts"]))'
```

Expected: 17 lines, all starting `up` (prometheus, alertmanager, node, and 14 blackbox probes), then `['Watchdog']`. Wait 90 s after the deploy so each probe has run once. If a probe is `up` but a ProbeDown is pending, check `probe_http_status_code` for that instance before changing anything.

- [ ] **Step 8: USER confirms the heartbeat and Grafana**

Ask the user to confirm:
- healthchecks.io shows **command-center heartbeat** as **up**, with pings about a minute apart.
- http://10.0.0.249:3000 accepts the admin password, the **Command Center** folder holds three dashboards, and **Command Center → Services** shows every service **UP**.

---

### Task 9: Fire drill

Proves every path end to end: a real page, the inhibitions, the host-down path and the dead-man's switch. The results are recorded in `docs/drills.md`. Run each command on the Pi from `/opt/command-center`.

**Files:**
- Create: `docs/drills.md`

**Interfaces:**
- Consumes: the running stack from Task 8; the `drill="true"` → `blackhole` route (Task 5).
- Produces: a dated drill record.

- [ ] **Step 1: A real critical and warning page**

Run:

```bash
/usr/bin/ssh command-center 'cd /opt/command-center && docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add alertname=DrillPage severity=critical service=drill --annotation=summary="Fire drill: critical page. Acknowledge it in Pushover." && docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add alertname=DrillNotice severity=warning service=drill --annotation=summary="Fire drill: quiet warning."'
```

Expected: within about 30 s the user gets 🔴 DrillPage as an emergency alert that sounds through Do Not Disturb and repeats until acknowledged, and 🟡 DrillNotice as a normal push. About 5 minutes later, both "resolved" messages arrive. **Ask the user to confirm all four.**

- [ ] **Step 2: Inhibitions suppress the right alerts** (drill-labelled, so nothing pages)

Run:

```bash
/usr/bin/ssh command-center 'cd /opt/command-center && add() { docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add drill=true "$@"; } && add alertname=HomeConnectivityLost severity=warning && add alertname=ProbeDown severity=critical service=drill-site && add alertname=HostDown severity=critical host=drill-host && add alertname=MemoryPressure severity=warning host=drill-host && add alertname=MemoryPressure severity=warning host=other-host && sleep 5 && curl -s "http://127.0.0.1:9093/api/v2/alerts?filter=drill=%22true%22"' | python3 -c 'import json,sys; [print(a["labels"]["alertname"], a["labels"].get("host", a["labels"].get("service", "")), a["status"]["state"]) for a in sorted(json.load(sys.stdin), key=lambda a: (a["labels"]["alertname"], a["labels"].get("host", "")))]'
```

Expected:

```
HomeConnectivityLost  active
HostDown drill-host active
MemoryPressure drill-host suppressed
MemoryPressure other-host active
ProbeDown drill-site suppressed
```

- [ ] **Step 3: The real host-down path**

Run: `/usr/bin/ssh command-center 'cd /opt/command-center && docker compose stop node-exporter'`
Wait 4 minutes. Expected: 🔴 **HostDown** reaches the user, saying "command-center is not answering scrapes". **Ask the user to confirm.**
Run: `/usr/bin/ssh command-center 'cd /opt/command-center && docker compose start node-exporter'`
Expected: "HostDown resolved" within about 5 minutes.

- [ ] **Step 4: The dead-man's switch**

Run: `/usr/bin/ssh command-center 'cd /opt/command-center && docker compose stop alertmanager'`
Wait 7 minutes. Expected: healthchecks.io marks the check **down** and pages the user through Pushover. **Ask the user to confirm.**
Run: `/usr/bin/ssh command-center 'cd /opt/command-center && docker compose start alertmanager'`
Expected: the check returns to **up** within 2 minutes.

- [ ] **Step 5: Record the drill** in `docs/drills.md`

```markdown
# Fire drills

## 2026-09-27: phase 1 (core stack)

| Path | Result |
|---|---|
| Critical page (emergency, through Do Not Disturb, repeats until acknowledged) | <pass/fail as the user confirmed> |
| Warning page (quiet) | <pass/fail> |
| Resolved messages | <pass/fail> |
| HomeConnectivityLost inhibits ProbeDown | <pass/fail, from Step 2 output> |
| HostDown inhibits same-host alerts only | <pass/fail, from Step 2 output> |
| Real HostDown (node-exporter stopped 4 min) | <pass/fail> |
| Dead-man's switch (Alertmanager stopped 7 min) | <pass/fail> |

Run again after phase 4, and after any change to routing or receivers.
```

Fill each result from what was observed and confirmed. A failure is recorded as a failure, together with what was seen.

- [ ] **Step 6: Commit**

```bash
git add docs/drills.md
git commit -m "docs: phase 1 fire drill record" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
