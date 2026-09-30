# Handoff

## Phase 2: Lightning infrastructure (2026-09-28, branch `feat/phase2-lightning-infra`)

Phase 1 merged (PR #1). Phase 2 is live:

- Gateway targets for lightning-a/b (postgres, patroni, etcd, haproxy) and lapi-witness (etcd)
  on :9900; node_exporter everywhere on :9100. 30/30 targets up.
- `hosts/server/cc_textfile.py`, a root timer on every host: `lightning_job_*` (last result,
  last success carried across failures, interval from the timer), WireGuard handshakes per
  peer, `node_reboot_required`, collector health.
- postgres_exporter 0.20.1 as its own OS user over the socket with peer auth: role
  `postgres_exporter` (pg_monitor, statement_timeout 5s, lock_timeout 1s, 3 connections),
  created on the primary. No password exists.
- HAProxy promex frontend on 127.0.0.1:8405 (validated, hitless reload; previous config kept
  as `haproxy.cfg.before-command-center`).
- Rules: `prometheus/rules/lightning.yml` (HA, jobs) plus SystemdUnitFailed, TunnelStale,
  TextfileCollectorFailing; each with promtool tests. Board: Lightning HA (`lightning-ha`).

### Rulings made in phase 2

- node_exporter stays direct on :9100 (phase 1); the gateway serves only the exporters whose
  ports are shared with write APIs or are loopback-only. Hosts with no exporters get no nginx.
- The gateway listens on every address (`listen 9900`) so nginx never depends on wg-mon at
  boot; the `wg-mon-firewall` nft table (now `/etc/command-center/firewall.nft`, ports 9100
  and 9900) and nginx's allow list keep it to the Pi. On the witness, nginx was installed
  for this and its default site removed.
- The witness is reached over the LAN (`lan_ip`, Pi 10.0.0.249), not a tunnel, per the spec.
- Cluster rules take the best view among scraped members, so a home outage is silence.
  ReplicationBroken is judged from the primary (no streaming standby), which also covers a
  replica that is down. EtcdNoQuorumRisk counts active peers seen by the best member.
- Added beyond the spec: PatroniTwoPrimaries, FailoverHappened (timeline bump, warning),
  PostgresDown (warning), HAProxyNoDatabase (critical, per node), TextfileCollectorFailing.
- JobMissedRun is generic: latest success across nodes older than max(2 x interval, 15 min),
  the interval read from each timer. The backup is left to BackupStale (26 h).
- SystemdUnitFailed ignores `postgresql@*.service` (Debian's unit, always failed under Patroni).
- TunnelStale at 5 minutes, not 3: re-keys every 2-2.5 min plus collector and scrape delay
  reach 3.5 min on a healthy tunnel.
- ScrapeTargetDown groups by job and host, so HostDown mutes a down host's gateway targets;
  HomeConnectivityLost also mutes remote ScrapeTargetDown and the Pi's wg-mon TunnelStale.
- WitnessCardFailing uses a read-only root only: node_exporter has no I/O error counter.
- sql_exporter (data freshness, business queries) stays in phase 3 with the in-app metrics.

### Found in phase 2, not fixed

- **lightning-backup has failed every night since 2026-09-22** (last success 2026-09-21
  03:20 UTC): `pg_dump: permission denied for table ops_switchovers`. The backup's role needs
  `GRANT SELECT` on that table (and a default privilege for future ones). Not applied: a
  production permission change for the user to approve.
- lightning-a nginx loads `sites-enabled/api-lightningapi.conf.bak.20260903T002248Z`
  ("conflicting server name" on every reload). Remove it from sites-enabled.
- Both Lightning nodes report a pending reboot (`/run/reboot-required`).

## Phase 1 (merged)

Branch was: `feat/phase1-core-i5znpv`.
Plan: `docs/superpowers/plans/2026-09-27-command-center-phase1-core.md`
Spec: `docs/superpowers/specs/2026-09-27-command-center-design.md`

## Where it stands

- Tasks 1 to 7 are done and committed (T1 d3c1208, T2 33ba90e, T3 79fdb76,
  T4 d6b913b, T5 9db832b, T6 eb0603c, T7 cef63c5). `make check` is green:
  37 pytest, promtool rule tests, blackbox config check, amtool routes
  (7 cases), `dashboards: 3 clean`.
- Tasks 8 (bootstrap the Pi, first deploy) and 9 (fire drill) are NOT done.
  They need the command-center Pi at 10.0.0.249 on the home LAN, which the
  cloud session could not reach. Run them from the workstation.

## First steps on the workstation

```bash
git fetch origin && git checkout feat/phase1-core-i5znpv
make check          # builds .venv, fetches .tools/ if Docker is not usable
```

Then work Task 8 and Task 9 in the plan, in order.

## Open request from the user

The user asked to make the Task 8/9 actions simpler to run from their PC.
Suggested shape, before starting Task 8:

- `scripts/setup-pi.sh` (workstation) that walks Task 8 end to end: stage the
  repo on the Pi, run the bootstrap over `/usr/bin/ssh -t` (the user types the
  sudo password), verify it, create `.env` from `.env.example` if missing and
  open it in `nano` over `ssh -t`, run `make deploy`, then print the target and
  alert checks from Task 8 Step 7. Pause with a prompt at each point where the
  user has to act (accounts, `.env`, confirmations).
- `scripts/drill.sh` (workstation) for Task 9, one subcommand per step, and
  make targets `make setup-pi` / `make drill`.
- Keep `tests/test_scripts.py` passing: every new `.sh` must parse and must
  use `/usr/bin/ssh`, never bare `ssh` (plain `ssh` is kitty's kitten on the
  workstation). Even prose like "ssh session" inside code trips the guard.

## Done 2026-09-27 evening

- Task 8 and Task 9 complete: stack live on the Pi, fire drill passed on every path
  (`docs/drills.md`).
- Dashboards rebuilt and generated by `scripts/build_dashboards.py` (overview,
  per-product Service page, Machine deep-dive, Probes); probes carry a `product` label.
- Machine metrics for x3 (ns572860), lightning-a (ns104901) and lightning-b (ns107120)
  over wg-mon: `inventory/hosts.yml`, `make enroll`. Verified 9100 closed publicly,
  HA mesh (wg0, patroni, etcd, haproxy) unaffected.

## Rulings made this evening

- Grafana 13 file provisioning needs classic JSON; dashboards use schemaVersion 42.
- The spec's nginx metrics gateway is deferred to phase 2: node_exporter binds to
  loopback and the wg-mon address, and `wg-mon-firewall.service` (its own nft table)
  admits only 10.98.0.1 to 9100. Production nginx was not touched.
- The nft rules are a unit, not a wg-quick PostUp: Ubuntu 26.04's AppArmor profile for
  wg-quick stops its nft from reading rule files.
- Host labels are friendly names (x3, lightning-a, lightning-b); the machine's own name
  is in a `hostname` label.
- The Pi's sudo password is needed only when wg-mon peers change (`hosts/pi/setup-wg-mon.sh`).

## Whole-branch review, 2026-09-27 night

`make check` green (48 pytest, promtool, blackbox, amtool 7 routes, 4 dashboards);
live: 20/20 targets up, only Watchdog firing. Fixed in the review:

- HomeConnectivityLost now also mutes HostDown for hosts other than command-center:
  the servers are scraped over wg-mon, which rides the home line, so a home outage
  would otherwise page three critical HostDowns. `drill.sh inhibit` checks it; re-run
  live after the deploy, pass.
- Service page "HTTP status" is neutral, not red/orange: lightning-tiles,
  ddcloud-analytics, zelara and vaulterm-waitlist answer 4xx by design. Status says
  what is healthy. (Closes the "no colour thresholds" deferred minor the same way.)
- `setup-pi.sh` no longer quotes 17 targets / three dashboards; README documents
  `make enroll` and the wg-mon arrangement.

Phase 1 is finished. Next: phase 2 in the plan (Postgres/Patroni/etcd/HAProxy metrics,
the nginx gateway, SystemdUnitFailed excluding `postgresql@16-main`).

## Found, not fixed

- lightning-a: `lightning-backup.service` failed at 2026-09-27 03:00 UTC (pg_dump exit 1),
  and its traceback writes the database connection string, password included, to the journal.
- lightning-b: `lightning-api-weekly-restart.service` last run failed.
- Both: `postgresql@16-main.service` failed; likely expected under Patroni. Exclude it
  before adding the spec's SystemdUnitFailed alert.
- The Pi rebooted unprompted at about 20:22 (power-on reset, no under-voltage logged).

## Rules that still apply

- Never read or print `.env` values; never run `sudo` on the Pi yourself.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- After Task 9: whole-branch review, then finishing-a-development-branch. The
  final report must list "Rulings I made" and "Deferred minors".

## Rulings made so far

- `.superpowers/` and `.tools/` are git-ignored; `deploy.sh` rsync excludes
  both and `test_scripts.py` copytree ignores both.
- No Docker socket on a workstation: the Makefile uses Docker when
  `docker info` works, else sha256-verified release binaries in `.tools/` via
  `scripts/fetch_tools.sh`.
- Disk-forecast rule tests sample every 1m (10m fell outside the 5m lookback).
- Alertmanager template header reworded to avoid a literal placeholder.
- `bootstrap-pi.sh` final message now reads "Log out and back in so $owner
  picks up the docker group." (the plan's Task 8 Step 2 expected text is
  outdated on this point).

## Deferred minors (check during Task 8)

- Containers run as `1000:1000`: confirmed 2026-09-27, `atlas` is uid 1000 on
  the Pi (Debian 13, aarch64). `setup-pi.sh` re-checks it.
- Grafana listens on 0.0.0.0:3000 (LAN reachable by design, password only).
- The probes dashboard "HTTP status" panel has no colour thresholds: ruled neutral on
  purpose (see the review above).
- HostDown for a server is critical; a wg-mon tunnel fault on the Pi alone would page
  all three as down. Accepted: rare, and the summary names the host.
- `setup-monitoring.sh` assumes `nft` is present (true on every Ubuntu server here).
