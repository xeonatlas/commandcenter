#!/usr/bin/env bash
# Runs as root on a monitored server; scripts/enroll-hosts.sh unpacks hosts/server (and the Pi
# throttle collector) into a temporary directory there and runs this from it. Two steps:
#   key                                make this server's wg-mon key, print the public half
#   apply ADDRESS PI EXPORTERS         set up everything the command center scrapes here
#     ADDRESS    the address the Pi scrapes: this server's wg-mon IP, or its LAN IP
#     PI         the Pi's wg-mon public key, or "lan" when the Pi reaches this host on the home LAN
#     EXPORTERS  comma list of postgres, patroni, etcd, haproxy, or "none" (then no gateway)
# It adds only its own pieces, each marked "command center": an existing WireGuard mesh, the
# apps and their nginx sites are left alone. Everything listens on loopback except
# node_exporter (:9100) and the metrics gateway (:9900), and only the Pi may reach those two.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "setup-monitoring: run as root" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)

NODE_VERSION=1.12.1   # same as the Pi's image in compose.yml
declare -A NODE_SHA256=(
  [amd64]=b51d8a76aa2a9156a55d501aca6276fae09e262259a5e4e831d2c2222f084e63
  [arm64]=ad35b605f9954b9f1ffddf5ba054bdc5a98d790b9eae5291e1eeb83f1ecbd0e7
)
PG_VERSION=0.20.1
declare -A PG_SHA256=(
  [amd64]=89d4f7e7920cad48fdc3133f789556ef5253c330a9f5fdace3bdb6344c0a8b5a
  [arm64]=d5d86fb98bb1f26b088d1a6fda07fd6b6f035cb5d40492f75ec3bfebb5ddfe9d
)
PORT=51821
PI_TUNNEL=10.98.0.1
PI_LAN=10.0.0.249
UNITS='(lightning-.*|patroni|etcd|haproxy|nginx|postgresql.*|wg-quick@.*|x3-gateway|x3-ops-dashboard)[.]service'
LIB=/usr/local/lib/command-center
say() { echo "setup-monitoring: $*"; }

cmd_key() {
  command -v wg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q wireguard-tools >/dev/null
  install -d -m 0700 /etc/wireguard
  [[ -s /etc/wireguard/wg-mon.key ]] || (umask 077 && wg genkey > /etc/wireguard/wg-mon.key)
  wg pubkey < /etc/wireguard/wg-mon.key
}

arch() {
  case $(uname -m) in x86_64) echo amd64 ;; aarch64) echo arm64 ;; *) say "unsupported $(uname -m)"; exit 1 ;; esac
}

# install_release NAME VERSION SHA256 URL_BASE: a Prometheus-style release tarball, checksum verified.
install_release() {
  local name=$1 version=$2 sum=$3 base=$4 tmp dir
  if [[ $(/usr/local/bin/"$name" --version 2>&1 | head -1) == *"version $version "* ]]; then
    say "$name $version already installed"; return
  fi
  tmp=$(mktemp -d)
  dir=$name-$version.linux-$(arch)
  curl -fsSL -o "$tmp/$dir.tar.gz" "$base/v$version/$dir.tar.gz"
  echo "$sum  $tmp/$dir.tar.gz" | sha256sum --check --quiet
  tar -xzf "$tmp/$dir.tar.gz" -C "$tmp"
  install -m 0755 "$tmp/$dir/$name" /usr/local/bin/"$name"
  rm -rf "$tmp"
  say "$name $version installed (checksum verified)"
}

system_user() { id "$1" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$1"; }

install_node_exporter() {
  install_release node_exporter "$NODE_VERSION" "${NODE_SHA256[$(arch)]}" \
    https://github.com/prometheus/node_exporter/releases/download
  system_user node_exporter
  install -d -m 0755 /var/lib/node_exporter /var/lib/node_exporter/textfile
}

write_firewall() {
  local mode=$1 from
  if [[ $mode == lan ]]; then from="ip saddr $PI_LAN"; else from="iifname \"wg-mon\" ip saddr $PI_TUNNEL"; fi
  # 9100 and 9900 answer loopback and the Pi, nothing else. Its own table, so a host
  # firewall manager (ufw, AMP) never rewrites it; create-then-delete first makes
  # loading it idempotent, in one atomic transaction. A unit of its own, not a wg-quick
  # hook: Ubuntu's AppArmor profile for wg-quick stops its nft from reading rule files.
  command -v nft >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q nftables >/dev/null
  install -d -m 0755 /etc/command-center
  cat > /etc/command-center/firewall.nft <<EOF
table inet wg_mon
delete table inet wg_mon
table inet wg_mon {
  chain input {
    type filter hook input priority -5; policy accept;
    tcp dport { 9100, 9900 } iifname "lo" accept
    tcp dport { 9100, 9900 } $from accept
    tcp dport { 9100, 9900 } drop
  }
}
EOF
  rm -f /etc/wireguard/wg-mon.nft   # its old home, before the witness (no tunnel) needed it too
  cat > /etc/systemd/system/wg-mon-firewall.service <<EOF
[Unit]
Description=Command center: node_exporter and the metrics gateway answer loopback and the Pi only
Before=node_exporter.service nginx.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$(command -v nft) -f /etc/command-center/firewall.nft
ExecStop=$(command -v nft) delete table inet wg_mon

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q wg-mon-firewall
  systemctl restart wg-mon-firewall
  if ufw status 2>/dev/null | grep -q '^Status: active'; then
    if [[ $mode == lan ]]; then
      ufw allow from "$PI_LAN" to any port 9100,9900 proto tcp comment 'command center: node_exporter and gateway' >/dev/null
    else
      ufw allow "$PORT/udp" comment 'wg-mon: command-center Pi dials in' >/dev/null
      ufw allow in on wg-mon from "$PI_TUNNEL" to any port 9100 proto tcp comment 'wg-mon: node_exporter for the command center' >/dev/null
      ufw allow in on wg-mon from "$PI_TUNNEL" to any port 9900 proto tcp comment 'wg-mon: metrics gateway for the command center' >/dev/null
    fi
    say "ufw: allowed the Pi to 9100 and 9900"
  fi
}

write_tunnel() {
  local ip=$1 pi_key=$2
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
AllowedIPs = $PI_TUNNEL/32
EOF
  )
  systemctl enable -q wg-quick@wg-mon
  systemctl restart wg-quick@wg-mon
  say "wg-mon up at $ip"
}

write_node_unit() {
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

# A root timer, once a minute: scheduled jobs, WireGuard handshakes, reboot-required
# (cc_textfile.py), and on a Raspberry Pi the firmware's throttle flags.
install_collectors() {
  install -d -m 0755 "$LIB"
  install -m 0755 "$here/cc_textfile.py" "$LIB/cc_textfile.py"
  timer cc-textfile "Write jobs, WireGuard and reboot state for node_exporter" \
    "/usr/bin/python3 $LIB/cc_textfile.py /var/lib/node_exporter/textfile/cc_textfile.prom"
  if command -v vcgencmd >/dev/null && [[ -f $here/../pi/pi_throttled.py ]]; then
    install -m 0755 "$here/../pi/pi_throttled.py" "$LIB/pi_throttled.py"
    timer pi-throttled "Write Raspberry Pi throttle flags for node_exporter" \
      "/usr/bin/python3 $LIB/pi_throttled.py /var/lib/node_exporter/textfile/pi_throttled.prom"
  fi
}

# timer NAME DESCRIPTION COMMAND: a oneshot service and a one-minute timer for it.
timer() {
  cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=Command center: $2

[Service]
Type=oneshot
ExecStart=$3
EOF
  cat > "/etc/systemd/system/$1.timer" <<EOF
[Unit]
Description=Command center: $2, every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable -q --now "$1.timer"
  systemctl start "$1.service" || say "$1: first run reported a problem; see journalctl -u $1"
}

# postgres_exporter as its own OS user, over the Unix socket with peer authentication
# (pg_hba: local all all peer), so no password exists anywhere. The role is created on
# the primary only; replication carries it to the replica.
install_postgres_exporter() {
  install_release postgres_exporter "$PG_VERSION" "${PG_SHA256[$(arch)]}" \
    https://github.com/prometheus-community/postgres_exporter/releases/download
  system_user postgres_exporter
  local sql
  if [[ $(runuser -u postgres -- psql -XAtc 'select pg_is_in_recovery()') == f ]]; then
    sql="DO \$\$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres_exporter') THEN
        CREATE ROLE postgres_exporter LOGIN CONNECTION LIMIT 3;
      END IF;
    END \$\$;
    GRANT pg_monitor TO postgres_exporter;
    ALTER ROLE postgres_exporter SET statement_timeout = '5s';
    ALTER ROLE postgres_exporter SET lock_timeout = '1s';"
    runuser -u postgres -- psql -XAq -v ON_ERROR_STOP=1 -c "$sql"
    say "postgres: role postgres_exporter (pg_monitor, 5 s statement timeout) on the primary"
  elif ! runuser -u postgres -- psql -XAtc "select 1 from pg_roles where rolname = 'postgres_exporter'" | grep -q 1; then
    say "postgres: replica without the postgres_exporter role yet; enrol the primary, it replicates here"
  fi
  cat > /etc/systemd/system/postgres_exporter.service <<'EOF'
[Unit]
Description=Prometheus postgres_exporter for the command center
After=network-online.target postgresql.service patroni.service

[Service]
User=postgres_exporter
Group=postgres_exporter
Environment="DATA_SOURCE_NAME=user=postgres_exporter host=/var/run/postgresql dbname=postgres sslmode=disable"
ExecStart=/usr/local/bin/postgres_exporter \
  --web.listen-address=127.0.0.1:9187 \
  --collector.long_running_transactions
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q postgres_exporter
  systemctl restart postgres_exporter
}

# HAProxy's built-in Prometheus exporter on a loopback frontend: validated, then a
# hitless reload. The block sits between markers so re-runs replace it.
enable_haproxy_metrics() {
  local cfg=/etc/haproxy/haproxy.cfg new
  new=$(mktemp)
  sed '/^# command center begin/,/^# command center end/d' "$cfg" > "$new"
  cat >> "$new" <<'EOF'
# command center begin: metrics for the command center, loopback only (via the :9900 gateway)
frontend command_center_metrics
    bind 127.0.0.1:8405
    mode http
    no log
    http-request use-service prometheus-exporter if { path /metrics }
    http-request return status 404
# command center end
EOF
  if cmp -s "$new" "$cfg"; then rm -f "$new"; say "haproxy: metrics frontend already in place"; return; fi
  haproxy -c -q -f "$new" || { rm -f "$new"; say "haproxy: new config does not validate; left unchanged"; exit 1; }
  cp -p "$cfg" "$cfg.before-command-center"
  cat "$new" > "$cfg" && rm -f "$new"
  systemctl reload haproxy
  say "haproxy: metrics frontend on 127.0.0.1:8405 (reloaded; previous config in $cfg.before-command-center)"
}

patroni_api() {
  awk '/^restapi:/{f=1; next} f && /^[^ ]/{f=0} f && $1 == "listen:" {print $2; exit}' /etc/patroni/config.yml
}

# The only thing the Pi talks to besides node_exporter: fixed GET paths to each local
# /metrics, so etcd's and Patroni's write APIs stay out of reach.
write_gateway() {
  local mode=$1 exporters=$2 pi=$PI_TUNNEL locations="" api
  [[ $mode == lan ]] && pi=$PI_LAN
  if ! command -v nginx >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends nginx >/dev/null
    rm -f /etc/nginx/sites-enabled/default   # installed only for the gateway: no port 80
    say "nginx installed for the gateway (default site disabled)"
  fi
  location() { locations+="    location = /metrics/$1 { proxy_pass $2; }"$'\n'; }
  location node http://127.0.0.1:9100/metrics
  for e in ${exporters//,/ }; do
    case $e in
      postgres) location postgres http://127.0.0.1:9187/metrics ;;
      patroni) api=$(patroni_api); location patroni "http://${api:?no restapi listen in /etc/patroni/config.yml}/metrics" ;;
      etcd) location etcd http://127.0.0.1:2379/metrics ;;
      haproxy) location haproxy http://127.0.0.1:8405/metrics ;;
      none) ;;
      *) say "unknown exporter $e"; exit 1 ;;
    esac
  done
  cat > /etc/nginx/conf.d/command-center-gateway.conf <<EOF
# command center: metrics gateway. Written by setup-monitoring.sh; re-run it, do not edit.
# Listens on every address so nginx never waits for wg-mon at boot; the wg-mon-firewall
# unit and the allow list below keep it to the Pi.
server {
    listen 9900;
    server_name _;
    access_log off;
    allow $pi;
    allow 127.0.0.1;
    deny all;
    if (\$request_method !~ ^(GET|HEAD)\$) { return 405; }
    proxy_connect_timeout 3s;
    proxy_read_timeout 15s;
$locations    location / { return 404; }
}
EOF
  nginx -t -q || { say "nginx: config does not validate; removing the gateway"; rm -f /etc/nginx/conf.d/command-center-gateway.conf; exit 1; }
  systemctl enable -q nginx
  if systemctl is-active -q nginx; then systemctl reload nginx; else systemctl start nginx; fi
  say "gateway: :9900 serves node${exporters:+,$exporters} to $pi"
}

wait_for() {  # URL: 200 within 15 s
  for _ in $(seq 1 15); do curl -fs -o /dev/null "$1" && return 0; sleep 1; done
  say "no answer from $1"; return 1
}

cmd_apply() {
  local ip=${1:?address} pi=${2:?Pi public key, or lan} exporters=${3:-none} mode=tunnel bad=0
  [[ $pi == lan ]] && mode=lan
  if [[ $mode == tunnel ]]; then
    [[ $ip =~ ^10\.98\.0\.[0-9]{1,3}$ ]] || { say "tunnel address must be in 10.98.0.0/24"; exit 1; }
    [[ $pi =~ ^[A-Za-z0-9+/]{43}=$ ]] || { say "that is not a WireGuard public key"; exit 1; }
    cmd_key >/dev/null
  else
    ip -4 -o addr show | grep -q " $ip/" || { say "$ip is not an address of this host"; exit 1; }
  fi
  install_node_exporter
  write_firewall "$mode"
  [[ $mode == tunnel ]] && write_tunnel "$ip" "$pi"
  write_node_unit "$ip"
  install_collectors
  for e in ${exporters//,/ }; do
    case $e in postgres) install_postgres_exporter ;; haproxy) enable_haproxy_metrics ;; esac
  done
  [[ $exporters == none ]] || write_gateway "$mode" "$exporters"

  wait_for http://127.0.0.1:9100/metrics || bad=1
  ss -Hltn "src $ip:9100" | grep -q . || { say "node_exporter is not listening on $ip"; bad=1; }
  for e in ${exporters//,/ }; do
    [[ $e == none ]] && continue
    if wait_for "http://127.0.0.1:9900/metrics/$e"; then say "gateway /metrics/$e answers"; else bad=1; fi
  done
  (( bad == 0 )) || { say "something is not answering; see journalctl -u node_exporter -u postgres_exporter -u nginx"; exit 1; }
  if [[ $exporters == none ]]; then say "done: node_exporter on 127.0.0.1 and $ip"
  else say "done: node_exporter on 127.0.0.1 and $ip, gateway on :9900 for $exporters"; fi
}

case ${1:-} in
  key) cmd_key ;;
  apply) shift; cmd_apply "$@" ;;
  *) echo "usage: $0 key | apply ADDRESS PI_PUBLIC_KEY|lan EXPORTERS" >&2; exit 2 ;;
esac
