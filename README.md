# Command Center

Grafana + Prometheus on the `command-center` Pi (10.0.0.249), watching every
service from outside and each server's disks, CPU, memory and systemd units from inside. Design:
`docs/superpowers/specs/2026-09-27-command-center-design.md`.

## Using it

- Dashboards: http://10.0.0.249:3000 (home network only), folder **Command Center**:
  - **Command Center**: every service and machine at a glance; click one to drill in.
  - **Service**: one product (Lightning, Vaulterm, ...) per page, each endpoint's
    uptime, response time split into DNS, connect, TLS, server and transfer, and certificates.
  - **Machine**: one host in depth: CPU, memory, every filesystem's free space and fill
    trend, disk I/O, network, temperatures, Pi throttling and whether it needs a reboot.
  - **Lightning HA**: who is primary, replication lag, Postgres, etcd (fsync, leader, quorum),
    each node's HAProxy route to the database, every scheduled job's last result and last
    success, the witness's SD card, and every WireGuard tunnel's handshake.
  - **Probes and certificates**: every URL side by side.
- Alerts arrive through Pushover: 🔴 breaks through Do Not Disturb and repeats
  until acknowledged, 🟡 is a quiet push. If the Pi or the home connection goes
  silent, healthchecks.io pages instead.

## Changing it

Everything is in this repo; nothing is edited in Grafana.

```bash
make check    # unit tests, promtool rule tests, amtool routing tests, dashboard lint
make deploy   # check, rsync to the Pi, validate again there, converge, reload
make setup-pi # first time only: bootstrap a fresh Pi, fill in .env, deploy, verify
make drill    # fire drill: real pages, inhibitions, host down, dead-man's switch
make enroll   # put the servers in inventory/hosts.yml on the Machine dashboard
```

- **Add a probe:** add the URL under the right `service` (and `product`) in
  `prometheus/targets/probes.yml`, choosing `http_2xx`, `http_any_answer`, `http_405` or
  `http_post_400` (a JSON API that refuses an empty POST, like vaulterm-api) from
  `blackbox/blackbox.yml`.
- **Change a dashboard:** edit `scripts/build_dashboards.py`, run `make dashboards`, and
  commit both; `make check` fails if the JSON and the generator disagree.
- **Watch another server:** add a line to `inventory/hosts.yml` (friendly `host` name,
  SSH alias, a free `tunnel_ip` in 10.98.0.0/24 and its public `endpoint`, or `lan_ip` for a
  machine on the home network, and which `exporters` it runs), then `make enroll`. It needs
  passwordless sudo on the server, and your Pi sudo password when the tunnel's peers change.
  The server gets node_exporter (:9100), a root collector for its scheduled jobs, WireGuard
  handshakes and reboot-required, and, for Postgres/Patroni/etcd/HAProxy, a metrics gateway
  (nginx on :9900, fixed GET paths only). Both ports answer the Pi and nothing else.
- **Add an alert:** add the rule in `prometheus/rules/`, with a firing and a
  non-firing case in `prometheus/tests/`.
- **Secrets** live only in `/opt/command-center/.env` on the Pi (see
  `.env.example`). Edit them there with
  `/usr/bin/ssh -t command-center nano /opt/command-center/.env`, then `make deploy`.

## Pi layout

| Path | What |
|---|---|
| `/opt/command-center` | this repo, plus `.env` and the rendered `run/alertmanager.yml` |
| `/srv/command-center/{prometheus,alertmanager,grafana}` | data; mount the USB drive here when it is fitted |
| `/var/lib/node_exporter/textfile` | textfile collector output |
