#!/usr/bin/env bash
# One-time setup of the command-center Pi, run by its owner:
#   sudo bash <copy of this repo>/scripts/bootstrap-pi.sh
# Installs Docker, spares the SD card, creates the directories the stack uses,
# copies the repo to /opt/command-center and starts the Pi throttle collector.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "bootstrap: run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run via sudo from the account that will own the stack}
src=$(cd "$(dirname "$0")/.." && pwd)

# Docker from Docker's repository: Debian's packages lack the compose plugin.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck source=/dev/null
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin rsync make
usermod -aG docker "$owner"
install -d /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "local", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
systemctl restart docker

# Keep the journal in RAM; the SD card has enough to do.
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n' > /etc/systemd/journald.conf.d/10-volatile.conf
systemctl restart systemd-journald

install -d -o "$owner" -g "$owner" /opt/command-center /srv/command-center \
  /srv/command-center/prometheus /srv/command-center/alertmanager /srv/command-center/grafana \
  /var/lib/node_exporter /var/lib/node_exporter/textfile
if [[ "$src" != /opt/command-center ]]; then
  cp -a "$src"/. /opt/command-center/
  chown -R "$owner:$owner" /opt/command-center
fi

install -m 0644 /opt/command-center/hosts/pi/pi-throttled.service /opt/command-center/hosts/pi/pi-throttled.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pi-throttled.timer

findmnt -no OPTIONS / | grep -q noatime || echo "bootstrap: WARNING: / is not mounted noatime; add it in /etc/fstab"
echo "bootstrap: done. Log out and back in so $owner picks up the docker group."
