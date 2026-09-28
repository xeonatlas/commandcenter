import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted([*(ROOT / "scripts").glob("*.sh"), *(ROOT / "hosts").glob("**/*.sh")])


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
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".venv", "run", "__pycache__", ".pytest_cache", ".superpowers", ".tools"))
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
