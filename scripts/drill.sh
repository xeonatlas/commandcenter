#!/usr/bin/env bash
# The phase 1 fire drill (plan Task 9), run from the workstation.
#   scripts/drill.sh            every step in order, then write docs/drills.md
#   scripts/drill.sh <step>     one step: page | inhibit | hostdown | deadman | record
# Results collect in run/drill-results (git-ignored) until `record` writes them up.
# COMMAND_CENTER_HOST overrides the SSH host (default: command-center).
set -euo pipefail
cd "$(dirname "$0")/.."
host=${COMMAND_CENTER_HOST:-command-center}
remote=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation
results=run/drill-results

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
pause() { read -rp "$* [Enter to continue, Ctrl-C to stop] " _; }
countdown() {
  local secs=$1 msg=$2
  while (( secs > 0 )); do printf '\r%s: %d:%02d ' "$msg" $((secs / 60)) $((secs % 60)); sleep 1; secs=$((secs - 1)); done
  printf '\r%s: done    \n' "$msg"
}
on_pi() { "$remote" -o BatchMode=yes "$host" "cd /opt/command-center && $1"; }
amtool_add() { on_pi "docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add $1"; }

# record <path> <result>: keeps the latest result per path.
record() {
  mkdir -p run
  touch "$results"
  grep -vF "$1|" "$results" > "$results.tmp" || true
  printf '%s|%s\n' "$1" "$2" >> "$results.tmp"
  mv "$results.tmp" "$results"
}
# confirm <path> <question>: asks the user and records pass, or fail with what they saw.
confirm() {
  local a seen
  read -rp "$2 [y/n] " a
  if [[ $a == [yY]* ]]; then
    record "$1" pass
  else
    read -rp "What did you see instead? " seen
    record "$1" "fail: $seen"
  fi
}

do_page() {
  step "Critical and warning page"
  amtool_add 'alertname=DrillPage severity=critical service=drill --annotation=summary="Fire drill: critical page. Acknowledge it in Pushover."'
  amtool_add 'alertname=DrillNotice severity=warning service=drill --annotation=summary="Fire drill: quiet warning."'
  echo "Sent. Within about 30 s: a red DrillPage emergency alert that sounds through"
  echo "Do Not Disturb and repeats until acknowledged, and a yellow DrillNotice normal push."
  confirm "Critical page (emergency, through Do Not Disturb, repeats until acknowledged)" \
    "Did DrillPage arrive as an emergency, through Do Not Disturb, repeating until you acknowledged it?"
  confirm "Warning page (quiet)" "Did DrillNotice arrive as a normal, quiet push?"
  countdown 330 "waiting for both to resolve"
  confirm "Resolved messages" "Did both 'resolved' messages arrive?"
}

do_inhibit() {
  step "Inhibitions (drill-labelled, nothing pages)"
  local out expected
  out=$(on_pi 'add() { docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add drill=true "$@"; } && add alertname=HomeConnectivityLost severity=warning && add alertname=ProbeDown severity=critical service=drill-site && add alertname=HostDown severity=critical host=drill-host && add alertname=MemoryPressure severity=warning host=drill-host && add alertname=MemoryPressure severity=warning host=other-host && sleep 5 && curl -s "http://127.0.0.1:9093/api/v2/alerts?filter=drill=%22true%22"' \
    | python3 -c 'import json,sys; [print(a["labels"]["alertname"], a["labels"].get("host", a["labels"].get("service", "")), a["status"]["state"]) for a in sorted(json.load(sys.stdin), key=lambda a: (a["labels"]["alertname"], a["labels"].get("host", "")))]')
  echo "$out"
  expected=$'HomeConnectivityLost  active\nHostDown drill-host active\nMemoryPressure drill-host suppressed\nMemoryPressure other-host active\nProbeDown drill-site suppressed'
  if grep -qx 'ProbeDown drill-site suppressed' <<<"$out" && grep -qx 'HomeConnectivityLost  active' <<<"$out"; then
    record "HomeConnectivityLost inhibits ProbeDown" pass
  else
    record "HomeConnectivityLost inhibits ProbeDown" "fail: $(grep -E '^(ProbeDown|HomeConnectivityLost) ' <<<"$out" | paste -sd ';')"
  fi
  if grep -qx 'MemoryPressure drill-host suppressed' <<<"$out" && grep -qx 'MemoryPressure other-host active' <<<"$out" \
      && grep -qx 'HostDown drill-host active' <<<"$out"; then
    record "HostDown inhibits same-host alerts only" pass
  else
    record "HostDown inhibits same-host alerts only" "fail: $(grep -E '^(HostDown|MemoryPressure) ' <<<"$out" | paste -sd ';')"
  fi
  if [[ $out == "$expected" ]]; then echo "matches the expected output"; else echo "differs from the expected output"; fi
}

# The next two stop a real container. Whatever happens, start it again.
stopped=""
restart_stopped() {
  if [[ -n $stopped ]]; then
    echo; echo "starting $stopped again"
    on_pi "docker compose start $stopped" || echo "could not start $stopped; run: make deploy" >&2
    stopped=""
  fi
}
trap restart_stopped EXIT
trap 'exit 130' INT TERM

do_hostdown() {
  step "Real host-down path"
  on_pi 'docker compose stop node-exporter'
  stopped=node-exporter
  countdown 240 "node-exporter stopped, HostDown should page"
  confirm "Real HostDown (node-exporter stopped 4 min)" \
    "Did a red HostDown arrive, saying command-center is not answering scrapes?"
  restart_stopped
  echo "Expect 'HostDown resolved' within about 5 minutes; no need to wait here."
}

do_deadman() {
  step "Dead-man's switch"
  on_pi 'docker compose stop alertmanager'
  stopped=alertmanager
  countdown 420 "Alertmanager stopped, healthchecks.io should page"
  confirm "Dead-man's switch (Alertmanager stopped 7 min)" \
    "Did healthchecks.io mark the check down and page you through Pushover?"
  restart_stopped
  echo "The check should return to up within 2 minutes."
}

do_record() {
  step "Write docs/drills.md"
  [[ -s $results ]] || { echo "no results yet; run the drill first" >&2; exit 1; }
  local paths=(
    "Critical page (emergency, through Do Not Disturb, repeats until acknowledged)"
    "Warning page (quiet)"
    "Resolved messages"
    "HomeConnectivityLost inhibits ProbeDown"
    "HostDown inhibits same-host alerts only"
    "Real HostDown (node-exporter stopped 4 min)"
    "Dead-man's switch (Alertmanager stopped 7 min)"
  )
  local p r
  {
    [[ -f docs/drills.md ]] || printf '# Fire drills\n'
    printf '\n## %s: phase 1 (core stack)\n\n| Path | Result |\n|---|---|\n' "$(date +%F)"
    for p in "${paths[@]}"; do
      r=$(grep -F "$p|" "$results" | cut -d'|' -f2- || true)
      printf '| %s | %s |\n' "$p" "${r:-not run}"
    done
    printf '\nRun again after phase 4, and after any change to routing or receivers.\n'
  } >> docs/drills.md
  echo "appended to docs/drills.md:"; tail -n 13 docs/drills.md
}

case ${1:-all} in
  page) do_page ;;
  inhibit) do_inhibit ;;
  hostdown) do_hostdown ;;
  deadman) do_deadman ;;
  record) do_record ;;
  all)
    rm -f "$results"
    echo "The drill sends two real pages, then stops node-exporter for 4 minutes and"
    echo "Alertmanager for 7. It takes about 20 minutes. Keep your phone to hand."
    pause "Start?"
    do_page; do_inhibit; do_hostdown; do_deadman; do_record ;;
  *) echo "usage: $0 [page|inhibit|hostdown|deadman|record]" >&2; exit 2 ;;
esac
