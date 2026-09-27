# Command Center

Grafana + Prometheus on the `command-center` Pi (10.0.0.249), watching every
service from outside and, from phase 2, from inside. Design:
`docs/superpowers/specs/2026-09-27-command-center-design.md`.

## Using it

- Dashboards: http://10.0.0.249:3000 (home network only), folder **Command Center**.
- Alerts arrive through Pushover: 🔴 breaks through Do Not Disturb and repeats
  until acknowledged, 🟡 is a quiet push. If the Pi or the home connection goes
  silent, healthchecks.io pages instead.

## Changing it

Everything is in this repo; nothing is edited in Grafana.

```bash
make check    # unit tests, promtool rule tests, amtool routing tests, dashboard lint
make deploy   # check, rsync to the Pi, validate again there, converge, reload
```

- **Add a probe:** add the URL under the right `service` in
  `prometheus/targets/probes.yml`, choosing `http_2xx`, `http_any_answer` or
  `http_405` from `blackbox/blackbox.yml`.
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
