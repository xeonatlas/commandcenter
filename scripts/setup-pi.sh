#!/usr/bin/env bash
# Takes the command-center Pi from a fresh install to a running, alerting stack
# (plan Task 8). Run from the workstation: `make setup-pi`. Every step checks
# what is already done, so it is safe to re-run after fixing whatever stopped it.
# COMMAND_CENTER_HOST overrides the SSH host (default: command-center).
set -euo pipefail
cd "$(dirname "$0")/.."
host=${COMMAND_CENTER_HOST:-command-center}
remote=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[32mok\033[0m  %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[31mxx\033[0m  %s\n' "$*" >&2; exit 1; }
pause() { read -rp "$* [Enter to continue, Ctrl-C to stop] " _; }
yes_no() { local a; read -rp "$1 [y/N] " a; [[ $a == [yY]* ]]; }
countdown() {
  local secs=$1 msg=$2
  while (( secs > 0 )); do printf '\r%s: %3ds ' "$msg" "$secs"; sleep 1; secs=$((secs - 1)); done
  printf '\r%s: done   \n' "$msg"
}
on_pi() { "$remote" -o BatchMode=yes "$host" "$@"; }

step "1/7 Reach the Pi"
on_pi true 2>/dev/null || die "cannot reach $host over SSH with the key in ~/.ssh/config"
ok "$host answers"
[[ $(on_pi id -u) == 1000 ]] || die "the Pi account is not uid 1000; compose.yml runs containers as 1000:1000"
ok "account is uid 1000, matching compose.yml"

step "2/7 Bootstrap (Docker, directories, throttle collector)"
if on_pi 'test -d /opt/command-center && command -v docker >/dev/null && systemctl is-active -q pi-throttled.timer'; then
  ok "already bootstrapped, skipping"
else
  rsync -az -e "$remote" --exclude=.git/ --exclude=.venv/ --exclude=run/ --exclude=.env \
    --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.superpowers/ --exclude=.tools/ \
    ./ "$host:command-center-bootstrap/"
  ok "repo staged in ~/command-center-bootstrap on the Pi"
  echo "The bootstrap needs root. Type your Pi sudo password when asked."
  "$remote" -t "$host" sudo bash command-center-bootstrap/scripts/bootstrap-pi.sh \
    || die "bootstrap failed; read the output above, fix it, and run make setup-pi again"
fi

step "3/7 Verify the bootstrap"
# Each SSH login is fresh, so the new docker group membership already applies.
on_pi 'docker run --rm hello-world >/dev/null' || die "docker does not run for this account"
ok "docker runs"
on_pi 'docker compose version'
for _ in 1 2 3 4; do
  on_pi 'grep -q "^pi_throttle_collector_success 1" /var/lib/node_exporter/textfile/pi_throttled.prom 2>/dev/null' && break
  countdown 20 "waiting for the throttle collector's first run"
done
on_pi 'grep -q "^pi_throttle_collector_success 1" /var/lib/node_exporter/textfile/pi_throttled.prom' \
  || die "pi_throttle_collector_success is not 1; check: journalctl -u pi-throttled on the Pi"
ok "throttle collector reports success"
on_pi 'rm -rf ~/command-center-bootstrap'

step "4/7 Alerting accounts (in your browser)"
if on_pi 'test -f /opt/command-center/.env'; then
  ok ".env already exists on the Pi, so the accounts are presumably set up"
else
  cat <<'EOF'
1. Pushover (pushover.net): note your User Key, then Create an Application
   named "Command Center" and note its API Token. In the Pushover iOS app,
   allow Critical Alerts so emergency pages break through Do Not Disturb.
2. healthchecks.io: create a check named "command-center heartbeat",
   Period 1 minute, Grace 5 minutes. Add a Pushover integration at
   emergency priority for "down". Note the check's ping URL.
EOF
  pause "Have the User Key, API Token and ping URL to hand?"
fi

step "5/7 Secrets in /opt/command-center/.env"
on_pi 'cd /opt/command-center && if [ -f .env ]; then echo exists; else cp .env.example .env && chmod 600 .env && echo created; fi'
# Key names only: a line identical to .env.example still holds the example value.
placeholders() {
  on_pi 'cd /opt/command-center && grep -Fxf .env.example .env | grep "=" | grep -v "^#" | grep -v "^PROM_RETENTION_SIZE=" | cut -d= -f1' || true
}
left=$(placeholders)
if [[ -n $left ]] || yes_no "Edit .env anyway?"; then
  while :; do
    [[ -n $left ]] && echo "Still set to the example value: $(echo $left)"
    echo "Opening nano. Set PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN, HEALTHCHECKS_PING_URL and"
    echo "GRAFANA_ADMIN_PASSWORD; leave PROM_RETENTION_SIZE=12GB. Save with Ctrl-O, quit with Ctrl-X."
    pause "Ready?"
    "$remote" -t "$host" nano /opt/command-center/.env
    left=$(placeholders)
    [[ -z $left ]] && break
    yes_no "Some keys still hold example values. Edit again?" || break
  done
fi
[[ -z $left ]] && ok "every secret has been changed from its example value"

step "6/7 Deploy"
make deploy || die "deploy failed; if it says 'no value in the env file', re-run make setup-pi and fix .env"

step "7/7 Verify targets and alerts"
countdown 90 "letting every probe run once"
check_targets() {
  on_pi 'curl -s http://127.0.0.1:9090/api/v1/targets' | python3 -c '
import json, sys
ts = json.load(sys.stdin)["data"]["activeTargets"]
down = [t for t in ts if t["health"] != "up"]
for t in down:
    print("   down:", t["labels"]["job"], t["labels"].get("instance"), t.get("lastError", ""))
print(f"   {len(ts) - len(down)}/{len(ts)} targets up")
sys.exit(1 if down or not ts else 0)'
}
check_alerts() {
  on_pi 'curl -s http://127.0.0.1:9090/api/v1/alerts' | python3 -c '
import json, sys
names = sorted(a["labels"]["alertname"] for a in json.load(sys.stdin)["data"]["alerts"])
print("   alerts:", names)
sys.exit(0 if names == ["Watchdog"] else 1)'
}
healthy=no
for attempt in 1 2 3; do
  t=0; a=0
  check_targets || t=1
  check_alerts || a=1
  (( t == 0 && a == 0 )) && { healthy=yes; break; }
  (( attempt < 3 )) && countdown 30 "not settled yet, checking again"
done
if [[ $healthy == yes ]]; then
  ok "every target is up and only the Watchdog fires"
else
  warn "not all green. For a probe that is up but alerting, check probe_http_status_code for it before changing anything."
fi

cat <<'EOF'

Last checks, by you:
  - healthchecks.io shows "command-center heartbeat" as up, pings about a minute apart.
  - http://10.0.0.249:3000 accepts the admin password; the Command Center folder
    holds four dashboards and the Command Center page shows every service UP.

Then run the fire drill: make drill
EOF
