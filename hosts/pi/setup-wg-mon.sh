#!/usr/bin/env bash
# Runs as root on the command-center Pi: sudo bash /opt/command-center/hosts/pi/setup-wg-mon.sh
# Brings up wg-mon, the Pi's monitoring tunnel to every enrolled server, from the
# rendered hosts/pi/wg-mon-peers.conf. Re-run it after enrolling another host.
# The Pi's private key never leaves /etc/wireguard; its public half goes to wg-mon.pub.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "setup-wg-mon: run with sudo" >&2; exit 1; }
peers=$(cd "$(dirname "$0")" && pwd)/wg-mon-peers.conf

command -v wg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q wireguard-tools >/dev/null
install -d -m 0700 /etc/wireguard
[[ -s /etc/wireguard/wg-mon.key ]] || (umask 077 && wg genkey > /etc/wireguard/wg-mon.key)
wg pubkey < /etc/wireguard/wg-mon.key > /etc/wireguard/wg-mon.pub
chmod 0644 /etc/wireguard/wg-mon.pub
# The directory is 0700; let the stack owner read the public key without sudo.
chmod 0711 /etc/wireguard

(umask 077 && {
  printf '# The command center monitoring tunnel. Peers come from inventory/hosts.yml.\n'
  printf '[Interface]\nAddress = 10.98.0.1/24\nPrivateKey = %s\n' "$(cat /etc/wireguard/wg-mon.key)"
  cat "$peers"
} > /etc/wireguard/wg-mon.conf)

# Public keys and addresses only: lets enroll-hosts.sh see, without sudo, whether a re-run is needed.
install -m 0644 "$peers" /etc/wireguard/wg-mon-peers.applied

systemctl enable -q wg-quick@wg-mon
if systemctl is-active -q wg-quick@wg-mon; then
  wg syncconf wg-mon <(wg-quick strip wg-mon)
else
  systemctl start wg-quick@wg-mon
fi
echo "setup-wg-mon: up with $(grep -c '^\[Peer\]' "$peers") peer(s). Pi public key: $(cat /etc/wireguard/wg-mon.pub)"
