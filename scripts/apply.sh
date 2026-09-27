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
