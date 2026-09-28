#!/usr/bin/env bash
# Puts every machine in inventory/hosts.yml on the Machine dashboard. Run from the
# workstation: `make enroll`. Each step is idempotent, so re-running is safe.
#   1. each server makes its wg-mon key; the public half is recorded in the inventory
#   2. the Pi's peers and Prometheus targets are rendered from the inventory
#   3. the Pi brings up wg-mon (needs your Pi sudo password once, when peers change)
#   4. each server brings up its end, its firewall rules and node_exporter
#   5. the Pi scrapes each one over the tunnel, then the new targets are deployed
# Servers need passwordless sudo for their SSH user.
set -euo pipefail
cd "$(dirname "$0")/.."
pi=${COMMAND_CENTER_HOST:-command-center}
remote=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
field() {  # one line per host: label, ssh alias, tunnel ip
  python3 - <<'EOF'
import yaml
for h in yaml.safe_load(open("inventory/hosts.yml"))["hosts"]:
    print(h["host"], h["ssh"], h["tunnel_ip"])
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
on_server() {  # ssh alias, args...: runs setup-monitoring.sh there as root
  local alias=$1; shift
  "$remote" -o BatchMode=yes "$alias" "sudo -n bash -s -- $*" < hosts/server/setup-monitoring.sh
}

step "1/5 Server keys"
while read -r host alias ip; do
  key=$(on_server "$alias" key)
  set_key "$host" "$key"
  echo "$host ($alias): $key"
done < <(field)

step "2/5 Render"
python3 scripts/render_inventory.py

step "3/5 Pi end of wg-mon"
rsync -az -e "$remote" hosts/pi/ "$pi:/opt/command-center/hosts/pi/"
if "$remote" -o BatchMode=yes "$pi" 'systemctl is-active -q wg-quick@wg-mon && cmp -s /etc/wireguard/wg-mon-peers.applied /opt/command-center/hosts/pi/wg-mon-peers.conf'; then
  echo "Pi peers already current"
else
  echo "The Pi needs root to change its tunnel. Type your Pi sudo password when asked."
  "$remote" -t "$pi" sudo bash /opt/command-center/hosts/pi/setup-wg-mon.sh
fi
pi_key=$("$remote" -o BatchMode=yes "$pi" cat /etc/wireguard/wg-mon.pub)
echo "Pi public key: $pi_key"

step "4/5 Server ends"
while read -r host alias ip; do
  echo "-- $host ($alias)"
  on_server "$alias" apply "$ip" "$pi_key"
done < <(field)

step "5/5 Scrape over the tunnel"
ok=yes
while read -r host alias ip; do
  for _ in $(seq 1 15); do
    # -n: ssh would otherwise read the rest of the host list from this loop's stdin.
    if "$remote" -n -o BatchMode=yes "$pi" "curl -fs -m 3 -o /dev/null http://$ip:9100/metrics"; then
      echo "$host: node_exporter reachable at $ip from the Pi"; continue 2
    fi
    sleep 2
  done
  echo "$host: NOT reachable at $ip from the Pi (check: sudo wg show wg-mon on both ends)" >&2
  ok=no
done < <(field)
[[ $ok == yes ]] || exit 1
make deploy
