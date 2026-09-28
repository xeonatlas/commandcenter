#!/usr/bin/env bash
# Runs as root on the command-center Pi: sudo bash /opt/command-center/hosts/pi/setup-wg-mon.sh
# Brings up wg-mon, the Pi's monitoring tunnel to every enrolled server, from the
# rendered hosts/pi/wg-mon-peers.conf, and the Pi's root collector. Re-run it after
# enrolling another host.
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
# The Pi's own collector (hosts/server/cc_textfile.py): wg-mon handshakes, which need root,
# and reboot-required. A root copy, so the timer never runs a file the stack owner can edit.
lib=/usr/local/lib/command-center
install -d -m 0755 "$lib"
install -m 0755 "$(dirname "$peers")/../server/cc_textfile.py" "$lib/cc_textfile.py"
cat > /etc/systemd/system/cc-textfile.service <<EOF
[Unit]
Description=Command center: write WireGuard and reboot state for node_exporter

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $lib/cc_textfile.py /var/lib/node_exporter/textfile/cc_textfile.prom
EOF
cat > /etc/systemd/system/cc-textfile.timer <<'EOF'
[Unit]
Description=Command center: write WireGuard and reboot state for node_exporter, every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable -q --now cc-textfile.timer
systemctl start cc-textfile.service || echo "setup-wg-mon: collector's first run reported a problem; see journalctl -u cc-textfile" >&2

echo "setup-wg-mon: up with $(grep -c '^\[Peer\]' "$peers") peer(s). Pi public key: $(cat /etc/wireguard/wg-mon.pub)"
