# Command Center: design

Date: 2026-09-27. Status: approved in conversation, awaiting written-spec review.

## 1. Intent

One Grafana + Prometheus command center covering every live service, so that:

- you know each service is **up and doing its job**, not just answering `/health`;
- you are **warned early** when things degrade (latency, errors, stale data,
  disks filling, failover health), on your phone;
- **90 days of history** answer "is this worse than usual?" and "when did it start?".

It is for one operator. Alerts must be rare and always actionable.

The founding lesson is the 2026-09-01 Lightning incident: `/health` was 200 for
fifteen hours while the API served a database nobody was writing to. Liveness and
freshness are different questions; this system asks both.

## 2. Decisions

| Topic | Decision |
|---|---|
| Host | Dedicated Pi 5 `command-center` (10.0.0.249, 4 GB, Debian 13) on the home LAN |
| Runtime | Docker Compose on the Pi; all config in this repo, nothing hand-edited in Grafana |
| Network | Pull over a dedicated WireGuard tunnel `wg-mon`, 10.98.0.0/24, separate from the Patroni mesh (10.99.0.0/24) |
| Retention | 90 days, 30 s scrape interval, 60 s for external probes |
| Storage | Prometheus data on the Pi's USB drive once fitted; until then the SD card with a 12 GB size cap |
| Alerting | Pushover (supports iOS Critical Alerts, which bypass Do Not Disturb) plus a healthchecks.io dead-man's switch |
| Instrumentation | Tiered: Lightning API and DD Cloud get in-app `/metrics`; every other host gets exporters and external probes |
| Grafana access | Home LAN only in v1; alerts reach you anywhere by push |

## 3. What is measured

Every service is asked five questions, outside in.

### 3.1 Can users reach it? (blackbox exporter on the Pi, every 60 s)

HTTP probes record success, status, total time, time split into DNS, connect,
TLS and server phases, and TLS certificate expiry.

| Target | Expectation |
|---|---|
| `https://api.lightningapi.dev/health` | 200 |
| `https://api.warpulse.com/health` | 200 |
| `https://tiles.lightningapi.dev/` | any 2xx/3xx/404 (the host answers) |
| `https://lightningapi.dev/` | 2xx |
| `https://cloud.niallmurray.com/health` | 200 |
| `https://analytics.niallmurray.com/` | 2xx/3xx/404 |
| `https://dualdegrees.io/` | 2xx |
| `https://warpulse.com/`, `https://support.warpulse.com/` | 2xx |
| `https://vaulterm.com/` | 2xx |
| `https://vaulterm.com/api/waitlist` (GET) | 405 (a POST-only function answering means it is up) |
| `https://github.com/xeonatlas/termora-releases/releases/latest/download/latest.yml` | 2xx after redirects (the Vaulterm update feed) |
| `https://api.vaulterm.com/v1/{activate,checkout,stripe/webhook}` (POST `{}`) | 400 with the app's `bad-request`/`bad-signature` body (the license server is up and has its secrets; 503 means one is missing) |
| `https://www.zelara.chat/`, `https://api.zelara.chat/` | 2xx/3xx/404 |

Probes run from a single home vantage point, so a home internet outage makes every
probe fail at once. Section 5.3 handles that.

### 3.2 Is it serving well? (in-app metrics)

**Lightning API** (FastAPI, one uvicorn process on 127.0.0.1:8000 per node), via
`prometheus_client`:

- `lightning_http_requests_total{route, method, status_class}`, where `route` is the
  route template (`/v1/flashes`, not the raw path). Unmatched paths collapse to
  `route="other"` to bound cardinality.
- `lightning_http_request_duration_seconds{route, method}`, a histogram with buckets
  from 5 ms to 10 s.
- `lightning_http_rate_limited_total{route}`, counting 429s.
- `/v1/stream`: `lightning_stream_connections` (gauge),
  `lightning_stream_messages_sent_total`, `lightning_stream_disconnects_total{reason}`.
- `/metrics` is served on the app's existing loopback port. The public nginx vhosts
  deny `/metrics`, and it is reached only through the metrics gateway (4.3).

**DD Cloud** (Express under PM2), via `prom-client` on a separate listener
127.0.0.1:9464, so the public port never serves metrics:

- `ddcloud_http_requests_total{route, method, status_class}` and
  `ddcloud_http_request_duration_seconds{route, method}`, with the route template
  taken from `req.route.path`.
- `ddcloud_sync_operations_total{result}` and `ddcloud_auth_attempts_total{result}`.
- prom-client default Node metrics: event-loop lag, heap, GC.

### 3.3 Is it doing its job?

- **Lightning data freshness** (sql_exporter, read-only role, connecting through
  the node's local HAProxy leader port, so both nodes report the leader's view): `lightning_newest_flash_age_seconds`, from
  `max(flash_timestamp_utc)`, the same measure as the freshness watchdog's 600 s
  rule. Also `lightning_flashes_last_5m`, the count of flashes in the last five
  minutes.
- **Scheduled jobs** (a textfile collector on each Lightning node, refreshed every
  minute from `systemctl show`): `lightning_job_last_success_timestamp_seconds{unit}`
  and `lightning_job_last_result{unit}` for every timer-driven unit: backup, storm
  tracker, rotation tracker, radar capture, radar archive, radar tracker, stereo
  altitude, PAYG settlement, nonprofit expiry, freshness watchdog, watchdog,
  failover IP reconcile. Dashboards and rules aggregate with `max` across nodes,
  because a job runs on whichever node holds the role.
- **Collection units**: the systemd state of `lightning-ingest` and
  `lightning-ingest-west`. They run on the leader only, so rules ask whether any
  node has each unit active.
- **DD Cloud processes** (a textfile collector from `pm2 jlist`):
  `pm2_up{app}`, `pm2_restarts_total{app}`, `pm2_memory_bytes{app}` for
  `ddcloud` and `localdb-analytics`. At survey time `localdb-analytics` had
  restarted 63 times in 5 days; this alert fires on day one by design.
- **Business**: `lightning_developer_accounts{plan}` and
  `lightning_developer_accounts_created_24h{plan}` (sql_exporter, from
  `developer_accounts`; `created_at` is a double epoch). Dashboard only, apart from
  the signup route's error ratio, which is covered by 3.2.

### 3.4 Are its dependencies healthy?

- **Postgres** (postgres_exporter, `pg_monitor` role with `statement_timeout`) on A,
  B and DD Cloud: up, connections vs `max_connections`, longest transaction age,
  deadlocks, cache hit ratio, database size, replication lag in seconds and bytes,
  WAL retained by slots.
- **Patroni** (built-in `/metrics`): leader identity, member state, timeline. A
  timeline change means a failover happened.
- **etcd** (built-in `/metrics`, all three members): health, leader identity,
  leader changes, `etcd_disk_wal_fsync_duration_seconds`, peer round-trip time.
- **HAProxy** (built-in Prometheus exporter, a new frontend bound to loopback):
  backend up/down, which backend is the leader, sessions, errors.
- **nginx** is not scraped directly in v1. Request metrics come from the apps
  behind it, and the external probes cover nginx itself.

### 3.5 Are the machines healthy? (node_exporter)

On A, B, the witness, DD Cloud, mc.synstick and the command-center Pi: CPU
saturation (load ÷ cores), available memory, filesystem space and a
`predict_linear` fill forecast, disk I/O utilisation and average write latency,
network errors, clock offset, systemd units (limited to `lightning-*`, `patroni`,
`etcd`, `haproxy`, `nginx`, `postgresql*`, `wg-quick@*`, `x3-gateway`,
`x3-ops-dashboard`), and reboot-required.

On the Pis: a textfile collector for `vcgencmd get_throttled` (under-voltage,
throttling, both current and since boot).

**Witness SD card** (an unbranded card that stalls up to 907 ms under load; see section 11):
bytes written per day and since install, average write latency, read-only
filesystem and I/O errors, and the etcd fsync histogram above. A dedicated
"Witness SD card" panel shows all of these together.

### 3.6 Is the monitoring alive?

Prometheus scrape failures and rule-evaluation failures, the WireGuard
latest-handshake age per peer, and the dead-man's switch (5.2).

## 4. Architecture

### 4.1 On the Pi (`compose.yml`)

- `prometheus`: `--storage.tsdb.retention.time=90d`,
  `--storage.tsdb.retention.size=12GB` (raised when the drive is fitted),
  `--web.enable-lifecycle` so deploys reload without restarting.
- `alertmanager`: Pushover receivers by severity, plus the heartbeat webhook.
- `grafana`: provisioned data source, folders and dashboards from `grafana/`.
  The admin password comes from `.env`.
- `blackbox_exporter`.
- `node_exporter` with the host filesystem mounted read-only, plus the throttling
  textfile collector.

The Pi's OS mounts with `noatime` and keeps journald in memory to spare the SD card.

### 4.2 `wg-mon` tunnel

- 10.98.0.1 is the Pi. Remote hosts: A 10.98.0.11, B 10.98.0.12,
  DD Cloud 10.98.0.13, mc.synstick 10.98.0.14.
- The Pi initiates and keeps each tunnel alive (`PersistentKeepalive = 25`). The
  servers have no `Endpoint` for the Pi and learn it from the handshake: the same
  pattern as the witness mesh, with no router configuration.
- Each server allows only 10.98.0.1/32 as the Pi's AllowedIPs and routes nothing
  else.
- The witness is on the home LAN and is reached directly, with no tunnel.

### 4.3 Metrics gateway on each monitored host

Every exporter and built-in metrics endpoint binds to loopback (or stays where it
is). One nginx server block per host is the only thing Prometheus talks to:

- It listens on the host's `wg-mon` address, or on the witness's LAN address,
  port 9900.
- `allow 10.98.0.1; allow 10.0.0.249; deny all;`, GET only.
- Fixed paths: `/metrics/node`, `/metrics/postgres`, `/metrics/sql`,
  `/metrics/patroni`, `/metrics/etcd`, `/metrics/haproxy`, `/metrics/app`,
  each proxying to the local endpoint's `/metrics`.

This keeps etcd's and Patroni's write APIs unreachable from the Pi, since only
their `/metrics` paths are proxied. It needs no etcd or Patroni config change or
restart, and a single firewall rule per host (9900/tcp from the Pi's address on
the tunnel interface). The witness gets `nginx-light` for this.

### 4.4 Inventory drives everything

`inventory/hosts.yml` lists each host, its tunnel IP, its role, and which gateway
paths it serves. `scripts/render.py` generates Prometheus `file_sd` targets and the
WireGuard peer blocks from it. Adding a host means editing one file and running
`scripts/add-host.sh <name>`.

| Host | Reached at | Gateway paths |
|---|---|---|
| A `ns104901` (Patroni leader at time of writing) | 10.98.0.11:9900 | node, postgres, sql, patroni, etcd, haproxy, app |
| B `ns107120` | 10.98.0.12:9900 | node, postgres, sql, patroni, etcd, haproxy, app |
| Witness `LAPI-witness` | 10.0.0.205:9900 | node, etcd |
| DD Cloud `vmi3275162` | 10.98.0.13:9900 | node, postgres, app |
| mc.synstick `ns572860` | 10.98.0.14:9900 | node |
| command-center | local | node (in-compose) |

Out of scope: odin, WarStash (not found running), the old `lightning-vps`.

## 5. Alerting

### 5.1 Severity

| Level | Pushover | Behaviour |
|---|---|---|
| 🔴 critical | priority 2 (emergency, iOS Critical Alert) | Repeats until acknowledged |
| 🟡 warning | priority 0 | Grouped by service, repeats every 4 h while firing |
| ⚪ info | none | Dashboard only |

### 5.2 Dead-man's switch

An always-firing `Watchdog` rule routes to a webhook that pings healthchecks.io
every minute. healthchecks.io pages through its own Pushover integration if pings
stop for 5 minutes. This covers home internet or power loss, a Pi failure, and
Prometheus or Alertmanager dying.

### 5.3 Noise control

- **Home outage**: if more than 80% of external probes fail at once,
  `HomeConnectivityLost` fires (🟡) and inhibits every individual probe alert.
- **Host down**: `HostDown` (a node scrape failing for 3 minutes, 🔴) inhibits every
  other alert carrying the same `host` label.
- **Watchdog owns repair**: nothing here restarts or changes anything. The
  existing freshness and health watchdogs keep their jobs; this system observes.

### 5.4 Rules

🔴 critical:

| Alert | Condition |
|---|---|
| `ProbeDown` | Probe failing for 3 minutes |
| `HostDown` | node scrape failing for 3 minutes |
| `HighErrorRatio` | 5xx ÷ total > 5% for 5 minutes, with at least 0.2 req/s |
| `LightningDataStale` | `lightning_newest_flash_age_seconds` > 600 for 2 minutes |
| `PatroniNoLeader` | No member reports leader for 1 minute |
| `ReplicationBroken` | Replica not streaming for 2 minutes |
| `EtcdNoQuorumRisk` | Fewer than 3 etcd members healthy for 5 minutes |
| `DiskFullSoon` | `predict_linear` over 6 h says full within 4 h |
| `WitnessCardFailing` | Witness root filesystem read-only, or I/O errors |
| `EtcdFsyncVerySlow` | etcd WAL fsync p99 > 100 ms for 10 minutes on any member |

🟡 warning:

| Alert | Condition |
|---|---|
| `LatencyAboveBaseline` | Route p95 > 2× the same window one week earlier, **and** > 250 ms, for 15 minutes, with at least 0.05 req/s |
| `CertExpiringSoon` | TLS certificate < 14 days |
| `DiskFullInDays` | `predict_linear` over 24 h says full within 3 days |
| `ReplicationLagHigh` | Lag > 30 s for 5 minutes |
| `SystemdUnitFailed` | Any watched unit in `failed` |
| `IngestUnitInactive` | No node has an ingest unit active for 5 minutes |
| `JobMissedRun` | Time since last success > 2× the job's interval (per-job table in the rules file) |
| `BackupStale` | Last successful backup > 26 h |
| `PM2CrashLoop` | `increase(pm2_restarts_total[1h])` > 3 |
| `EtcdFsyncSlow` | Witness WAL fsync p99 > 25 ms for 15 minutes |
| `EtcdLeaderOnWitness` | The witness is etcd leader for 5 minutes (run `etcdctl move-leader`) |
| `EtcdLeaderChurn` | > 3 leader changes in 1 hour |
| `PiThrottled` | Under-voltage or throttling active on any Pi |
| `MemoryPressure` | Available memory < 10% for 15 minutes |
| `ClockDrift` | Clock offset > 50 ms for 10 minutes |
| `TunnelStale` | WireGuard handshake older than 3 minutes |
| `HomeConnectivityLost` | See 5.3 |

## 6. Dashboards

All provisioned from JSON in `grafana/dashboards/`.

- **Command Center**: one row per service (Lightning API, Lightning data, Lightning
  HA, DD Cloud, Vaulterm, websites, mc.synstick). Each row has a status light
  (up / degraded / down), p95 latency, error ratio, freshness where relevant, and
  certificate days left.
- **Lightning API**: RED per route, streaming, rate limits, job timeline.
- **Lightning HA**: Patroni roles and timeline, replication lag, etcd health and
  fsync, HAProxy backends, the Witness SD card panel.
- **DD Cloud**: RED per route, sync and auth outcomes, PM2 restarts and memory,
  Postgres.
- **Hosts**: USE per host, disk forecasts, systemd units, Pi throttling.
- **Probes and certificates**: every probe's latency by phase, certificate expiry.
- **Business**: developer accounts by plan, signups per day, stream connections.

## 7. Repo layout

```
compose.yml
.env.example              # PUSHOVER_*, HEALTHCHECKS_URL, GRAFANA_ADMIN_PASSWORD
Makefile                  # make check, make deploy
inventory/hosts.yml
prometheus/prometheus.yml
prometheus/rules/*.yml
prometheus/tests/*.yml    # promtool unit tests, one file per rules file
alertmanager/alertmanager.yml
blackbox/blackbox.yml
grafana/provisioning/
grafana/dashboards/*.json
hosts/                    # per-host install material: gateway nginx conf, textfile collectors, systemd units
scripts/render.py
scripts/deploy.sh
scripts/add-host.sh
docs/
```

Secrets never enter the repo. Pi-side secrets are in `.env` on the Pi. Host-side
database credentials for exporters live in root-owned env files on each host.

## 8. Rollout

Each phase is useful on its own and is verified before the next begins.

1. **Core on the Pi**: compose stack, blackbox probes for every URL in 3.1,
   Pushover, dead-man's switch, Probes and Command Center dashboards (probe rows
   only). No server changes.
2. **Lightning infrastructure**: `wg-mon` peers on A and B, gateways on A, B and the
   witness, node, postgres, Patroni, etcd and HAProxy metrics, the job and
   throttling textfile collectors, the Lightning HA and Hosts dashboards.
   Exporters touch neither the API nor the database role, so this is outside the
   Friday window; the HAProxy frontend addition is a hitless reload.
3. **Lightning app**: in-app metrics and the sql_exporter freshness and business
   queries. This ships through the Lightning repo's normal `scripts/deploy.sh`
   in the Friday window, with the public vhost's `/metrics` denial in the same
   change.
4. **DD Cloud and mc.synstick**: tunnels, gateways, node and postgres exporters,
   PM2 collector, prom-client in DD Cloud (its own deploy).
5. **Fire drill**, as described in section 9.

## 9. Testing

- `make check` runs `promtool check config`, `promtool test rules` (every alert has
  a firing and a non-firing case, including a quiet-night baseline that must not
  fire and a stale flash that must), `amtool check-config`, a JSON lint of every
  dashboard, and `render.py` against the inventory. `deploy.sh` refuses to ship if
  it fails, and re-runs the checks on the Pi.
- In-app metrics get unit tests in their own repos: route-template labelling,
  `other` collapse, and the stream gauge returning to zero after disconnects.
- **Fire drill** after phase 1 and again after phase 4:
  1. A test 🔴 and 🟡 via `amtool`; confirm the 🔴 breaks through Do Not Disturb.
  2. Pause the heartbeat; confirm healthchecks.io pages within 6 minutes.
  3. Stop node_exporter on B; confirm `HostDown` fires and B's other alerts are
     inhibited, then restore it.
  4. Block outbound probes on the Pi; confirm one `HomeConnectivityLost` and no
     storm.

## 10. Failure handling

| Failure | Result |
|---|---|
| Pi or its storage dies | Reflash, restore `.env`, run `deploy.sh`: about 15 minutes. Metric history is lost; accepted in v1 (no off-box snapshot target exists since the NAS was retired). |
| Home internet or power | Dead-man's switch pages |
| One tunnel down | `HostDown` + `TunnelStale`, with that host's alerts inhibited |
| Load on production | Exporters are light; SQL queries are indexed lookups with a statement timeout, sent through each node's local HAProxy to the leader |
| Exposure | Gateways listen only on tunnel/LAN addresses to one allowed IP; Grafana is LAN only; `/metrics` is denied on public vhosts |

## 11. Hardware

- The Pi 5 runs on a 3 A supply, which caps total USB current at 600 mA. The
  low-power drive being fitted must stay within that; `PiThrottled` and kernel
  USB disconnects will show it if it does not.
- The witness now boots from an unbranded SD card with etcd timeouts raised to
  250 ms / 2.5 s cluster-wide (see the Lightning HA runbook, 2026-09-27). The
  witness alerts in 5.4 exist to tell you when to replace it.

## 12. Out of scope for v1

Grafana access away from home, off-box metric snapshots, log aggregation (Loki),
tracing, WebSocket end-to-end probes, and instrumenting anything beyond Lightning
API and DD Cloud.
