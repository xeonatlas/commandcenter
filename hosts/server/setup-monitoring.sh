#!/usr/bin/env bash
# Runs as root on a monitored server; scripts/enroll-hosts.sh sends it. Two steps:
#   key                            make this server's wg-mon key, print the public half
#   apply TUNNEL_IP PI_PUBLIC_KEY  bring up the wg-mon tunnel, its firewall rules and node_exporter
# It adds only its own pieces: an existing WireGuard mesh, nginx and the apps are left alone.
# node_exporter listens on loopback and on the tunnel address, and only the Pi may reach it.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "setup-monitoring: run as root" >&2; exit 1; }

VERSION=1.12.1   # same as the Pi's image in compose.yml
declare -A SHA256=(
  [amd64]=b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63
  [arm64]=ad35b605f9954b9f1ffddf5ba054bdc5a98d790b9eae5291e1eeb83f1ecbd0e7
)
PORT=51821
PI=10.98.0.1
UNITS='(lightning-.*|patroni|etcd|haproxy|nginx|postgresql.*|wg-quick@.*|x3-gateway|x3-ops-dashboard)[.]service'
say() { echo "setup-monitoring: $*"; }

cmd_key() {
  command -v wg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q wireguard-tools >/dev/null
  install -d -m 0700 /etc/wireguard
  [[ -s /etc/wireguard/wg-mon.key ]] || (umask 077 && wg genkey > /etc/wireguard/wg-mon.key)
  wg pubkey < /etc/wireguard/wg-mon.key
}

install_node_exporter() {
  local arch tmp tarball
  case $(uname -m) in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) say "unsupported $(uname -m)"; exit 1 ;; esac
  if [[ $(/usr/local/bin/node_exporter --version 2>&1 | head -1) == *"version $VERSION "* ]]; then
    say "node_exporter $VERSION already installed"
  else
    tmp=$(mktemp -d)
    tarball=node_exporter-$VERSION.linux-$arch.tar.gz
    curl -fsSL -o "$tmp/$tarball" "https://github.com/prometheus/node_exporter/releases/download/v$VERSION/$tarball"
    echo "${SHA256[$arch]}  $tmp/$tarball" | sha256sum --check --quiet
    tar -xzf "$tmp/$tarball" -C "$tmp"
    install -m 0755 "$tmp/node_exporter-$VERSION.linux-$arch/node_exporter" /usr/local/bin/node_exporter
    rm -rf "$tmp"
    say "node_exporter $VERSION installed (checksum verified)"
  fi
  id node_exporter >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin node_exporter
  install -d -m 0755 /var/lib/node_exporter /var/lib/node_exporter/textfile
}

write_tunnel() {
  local ip=$1 pi_key=$2
  # Port 9100 answers loopback and the Pi, nothing else. Its own table, so a host
  # firewall manager (ufw, AMP) never rewrites it; create-then-delete first makes
  # loading it idempotent, in one atomic transaction. A unit of its own, not a wg-quick
  # hook: Ubuntu's AppArmor profile for wg-quick stops its nft from reading rule files.
  cat > /etc/wireguard/wg-mon.nft <<EOF
table inet wg_mon
delete table inet wg_mon
table inet wg_mon {
  chain input {
    type filter hook input priority -5; policy accept;
    tcp dport 9100 iifname "lo" accept
    tcp dport 9100 iifname "wg-mon" ip saddr $PI accept
    tcp dport 9100 drop
  }
}
EOF
  cat > /etc/systemd/system/wg-mon-firewall.service <<EOF
[Unit]
Description=Command center: node_exporter answers loopback and the Pi only
Before=node_exporter.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$(command -v nft) -f /etc/wireguard/wg-mon.nft
ExecStop=$(command -v nft) delete table inet wg_mon

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q wg-mon-firewall
  systemctl restart wg-mon-firewall
  (umask 077 && cat > /etc/wireguard/wg-mon.conf <<EOF
# The command center's monitoring tunnel. The Pi dials in from home and keeps it alive,
# so there is no Endpoint here: it is learnt from the handshake.
[Interface]
Address = $ip/32
ListenPort = $PORT
PrivateKey = $(cat /etc/wireguard/wg-mon.key)

[Peer]
# command-center Pi
PublicKey = $pi_key
AllowedIPs = $PI/32
EOF
  )
  if ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow "$PORT/udp" comment 'wg-mon: command-center Pi dials in' >/dev/null
    ufw allow in on wg-mon from "$PI" to any port 9100 proto tcp comment 'wg-mon: node_exporter for the command center' >/dev/null
    say "ufw: allowed $PORT/udp and 9100/tcp from $PI on wg-mon"
  fi
  systemctl enable -q wg-quick@wg-mon
  systemctl restart wg-quick@wg-mon
  say "wg-mon up at $ip"
}

write_unit() {
  local ip=$1
  cat > /etc/systemd/system/node_exporter.service <<EOF
[Unit]
Description=Prometheus node_exporter for the command center
After=network-online.target wg-quick@wg-mon.service wg-mon-firewall.service
Wants=network-online.target
# Never listen without the firewall that keeps the port to the Pi.
Requires=wg-mon-firewall.service

[Service]
User=node_exporter
Group=node_exporter
# The tunnel address fails to bind until wg-mon is up; Restart covers boot races.
# \$\$ is systemd's literal dollar; /tmp/.mount_* are AppImage mounts, always 100% full.
ExecStart=/usr/local/bin/node_exporter \\
  --web.listen-address=127.0.0.1:9100 \\
  --web.listen-address=$ip:9100 \\
  --collector.textfile.directory=/var/lib/node_exporter/textfile \\
  --collector.systemd \\
  "--collector.systemd.unit-include=$UNITS" \\
  "--collector.filesystem.mount-points-exclude=^/(dev|proc|run|sys|snap|var/lib/(docker|containers)/.+|tmp/[.]mount_.+)(\$\$|/)"
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q node_exporter
  systemctl restart node_exporter
}

cmd_apply() {
  local ip=${1:?tunnel ip} pi_key=${2:?Pi public key}
  [[ $ip =~ ^10\.98\.0\.[0-9]{1,3}$ ]] || { say "tunnel ip must be in 10.98.0.0/24"; exit 1; }
  [[ $pi_key =~ ^[A-Za-z0-9+/]{43}=$ ]] || { say "that is not a WireGuard public key"; exit 1; }
  cmd_key >/dev/null
  install_node_exporter
  write_tunnel "$ip" "$pi_key"
  write_unit "$ip"
  for _ in $(seq 1 10); do
    if curl -fs -o /dev/null http://127.0.0.1:9100/metrics && ss -Hltn "src $ip:9100" | grep -q .; then
      say "node_exporter answering on 127.0.0.1 and $ip"; return 0
    fi
    sleep 1
  done
  say "node_exporter is not answering; see: journalctl -u node_exporter"
  exit 1
}

case ${1:-} in
  key) cmd_key ;;
  apply) shift; cmd_apply "$@" ;;
  *) echo "usage: $0 key | apply TUNNEL_IP PI_PUBLIC_KEY" >&2; exit 2 ;;
esac
