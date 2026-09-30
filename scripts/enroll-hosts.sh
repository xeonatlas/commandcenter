#!/usr/bin/env bash
# Puts every machine in inventory/hosts.yml on the dashboards. Run from the workstation:
# `make enroll`. Each step is idempotent, so re-running is safe.
#   1. each tunnel server makes its wg-mon key; the public half is recorded in the inventory
#   2. the Pi's peers and Prometheus targets are rendered from the inventory
#   3. the Pi brings up wg-mon and its own collector (needs your Pi sudo password, once)
#   4. each server gets its tunnel end (or LAN firewall), node_exporter, the collectors
#      and the :9900 metrics gateway for its exporters (hosts/server/setup-monitoring.sh)
#   5. the Pi scrapes each one, then the new targets are deployed
# Servers need passwordless sudo for their SSH user.
set -euo pipefail
cd "$(dirname "$0")/.."
pi=${COMMAND_CENTER_HOST:-command-center}
remote=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
field() {  # one line per host: label, ssh alias, address, "tunnel" or "lan", exporters (comma list or none)
  python3 - <<'EOF'
import yaml
for h in yaml.safe_load(open("inventory/hosts.yml"))["hosts"]:
    mode = "tunnel" if h.get("tunnel_ip") else "lan"
    print(h["host"], h["ssh"], h.get("tunnel_ip") or h["lan_ip"], mode, ",".join(h.get("exporters") or []) or "none")
EOF
}
set_key() {  # host, public key: records it on that host's inventory line
  python3 - "$1" "$2" <<'EOF'
import re, sys
from pathlib import Path
host, key = sys.argv[1:]
path = Path("inventory/hosts.yml")
text, n = re.subn(rf'(\{{host: {re.escape(host)},.*wg_public_key: )"[^"]*"', rf'\g<1>"{key}"', path.read_text())
assert n == 1, f"{host} not found once in {path}"
path.write_text(text)
EOF
}
# on_server ALIAS ARGS...: unpacks hosts/server (and the Pi throttle collector) into a
# temporary directory there and runs setup-monitoring.sh from it as root.
on_server() {
  local alias=$1; shift
  tar -C hosts -cz server pi/pi_throttled.py \
    | "$remote" -o BatchMode=yes "$alias" \
      "d=\$(mktemp -d) && tar -xz -C \"\$d\" && sudo -n bash \"\$d/server/setup-monitoring.sh\" $*; rc=\$?; rm -rf \"\$d\"; exit \$rc"
}

step "1/5 Server keys"
while read -r host alias addr mode _; do
  [[ $mode == tunnel ]] || { echo "$host: on the LAN, no tunnel"; continue; }
  key=$(on_server "$alias" key < /dev/null)
  set_key "$host" "$key"
  echo "$host ($alias): $key"
done < <(field)

step "2/5 Render"
python3 scripts/render_inventory.py

step "3/5 Pi end of wg-mon, and the Pi's collector"
rsync -az -e "$remote" hosts/ "$pi:/opt/command-center/hosts/"
if "$remote" -o BatchMode=yes "$pi" 'systemctl is-active -q wg-quick@wg-mon && systemctl is-active -q cc-textfile.timer \
    && cmp -s /etc/wireguard/wg-mon-peers.applied /opt/command-center/hosts/pi/wg-mon-peers.conf \
    && cmp -s /usr/local/lib/command-center/cc_textfile.py /opt/command-center/hosts/server/cc_textfile.py'; then
  echo "Pi already current"
else
  echo "The Pi needs root to change its tunnel. Type your Pi sudo password when asked."
  "$remote" -t "$pi" sudo bash /opt/command-center/hosts/pi/setup-wg-mon.sh
fi
pi_key=$("$remote" -o BatchMode=yes "$pi" cat /etc/wireguard/wg-mon.pub)
echo "Pi public key: $pi_key"

step "4/5 Servers"
while read -r host alias addr mode exporters; do
  echo "-- $host ($alias)"
  if [[ $mode == tunnel ]]; then pi_arg=$pi_key; else pi_arg=lan; fi
  on_server "$alias" apply "$addr" "$pi_arg" "$exporters" < /dev/null
done < <(field)

step "5/5 Scrape from the Pi"
ok=yes
while read -r host alias addr mode exporters; do
  urls=("http://$addr:9100/metrics")
  for e in ${exporters//,/ }; do [[ $e == none ]] || urls+=("http://$addr:9900/metrics/$e"); done
  for url in "${urls[@]}"; do
    for _ in $(seq 1 15); do
      # -n: ssh would otherwise read the rest of the host list from this loop's stdin.
      if "$remote" -n -o BatchMode=yes "$pi" "curl -fs -m 5 -o /dev/null $url"; then
        echo "$host: $url answers the Pi"; continue 2
      fi
      sleep 2
    done
    echo "$host: $url does NOT answer the Pi (tunnel hosts: sudo wg show wg-mon on both ends)" >&2
    ok=no
  done
done < <(field)
[[ $ok == yes ]] || exit 1
make deploy
