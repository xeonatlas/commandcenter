#!/usr/bin/env python3
"""Builds grafana/dashboards/*.json. Edit this file, run `make dashboards`, commit both.

Hand-written dashboard JSON drifts: every panel ends up with its own colours,
units and thresholds. Generating them keeps one look across every page, and
tests/test_build_dashboards.py fails if the committed JSON is stale.

Grafana 13 file provisioning still needs the classic JSON model (schemaVersion,
panels[]); it refuses the v2 model its own UI exports.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grafana" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
BB = 'job="blackbox"'
# Real filesystems only: no RAM-backed, container or pseudo mounts.
FS = 'fstype!~"tmpfs|ramfs|devtmpfs|squashfs|overlay|nsfs|fuse.*", mountpoint!~"/run.*"'
# Whole devices only: partitions would count the same I/O twice.
DISK = 'device!~"loop.*|z?ram.*|dm-.*|mmcblk[0-9]+p[0-9]+|nvme[0-9]+n[0-9]+p[0-9]+|[shv]d[a-z]+[0-9]+"'
NIC = 'device!~"lo|veth.*|docker.*|br-.*|virbr.*"'

GREEN, YELLOW, ORANGE, RED, BLUE, PURPLE, GREY = (
    "green", "#EAB839", "orange", "red", "blue", "purple", "text")
UP_DOWN = [{"type": "value", "options": {
    "1": {"text": "UP", "color": GREEN, "index": 0},
    "0": {"text": "DOWN", "color": RED, "index": 1}}}]
PHASES = [("resolve", "DNS", PURPLE), ("connect", "Connect", BLUE), ("tls", "TLS", ORANGE),
          ("processing", "Server", GREEN), ("transfer", "Transfer", YELLOW)]


def steps(*pairs):
    """steps(GREEN, 0.8, ORANGE, 0.9, RED) -> Grafana absolute thresholds."""
    out = [{"color": pairs[0], "value": None}]
    for i in range(1, len(pairs), 2):
        out.append({"color": pairs[i + 1], "value": pairs[i]})
    return {"mode": "absolute", "steps": out}


# Shared thresholds, so "slow" or "nearly full" means the same on every page.
T_RESPONSE = steps(GREEN, 1, ORANGE, 3, RED)
T_UPTIME = steps(RED, 0.99, ORANGE, 0.999, GREEN)
T_CERT = steps(RED, 7, ORANGE, 14, GREEN)
T_USED = steps(GREEN, 0.75, ORANGE, 0.9, RED)
T_TEMP = steps(GREEN, 65, ORANGE, 80, RED)
T_DOWN_COUNT = steps(GREEN, 1, RED)
DAYS = "suffix: days"


def q(expr, legend="", instant=False, table=False):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto"}
    if instant or table:
        t["instant"], t["range"] = True, False
    if table:
        t["format"] = "table"
    return t


def override(name, **props):
    """override("Status", unit="s") -> a by-name field override."""
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


def color(hex_or_name):
    return {"mode": "fixed", "fixedColor": hex_or_name}


class Board:
    """Lays panels out left to right on Grafana's 24-column grid, wrapping as needed."""

    def __init__(self, uid, title, description, tags=(), time="now-24h", refresh="1m"):
        self.doc = {
            "uid": uid, "title": title, "description": description,
            "tags": ["command-center", *tags], "timezone": "browser", "editable": False,
            "graphTooltip": 1, "time": {"from": time, "to": "now"}, "refresh": refresh,
            "schemaVersion": 42, "version": 1, "fiscalYearStartMonth": 0, "liveNow": False,
            "links": [{"title": "Dashboards", "type": "dashboards", "tags": ["command-center"],
                       "asDropdown": True, "includeVars": False, "keepTime": True, "icon": "external link"}],
            "annotations": {"list": [{
                "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts", "type": "dashboard"}]},
            "templating": {"list": []}, "panels": [],
        }
        self.x = self.y = self.row_h = 0

    def var(self, name, label, query, multi=False, include_all=False, default=None):
        v = {"name": name, "label": label, "type": "query", "datasource": DS,
             "query": {"query": query, "refId": name}, "definition": query,
             "refresh": 2, "sort": 1, "multi": multi, "includeAll": include_all,
             "hide": 0, "options": [], "regex": ""}
        if default is not None:
            v["current"] = {"text": default, "value": default}
        elif include_all:
            v["current"] = {"text": ["All"], "value": ["$__all"]}
        self.doc["templating"]["list"].append(v)

    def _place(self, w, h):
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title, repeat=None):
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        p = {"type": "row", "title": title, "collapsed": False, "panels": [],
             "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}}
        if repeat:
            p["repeat"] = repeat
        self.doc["panels"].append(p)
        self.y += 1

    def add(self, kind, title, w, h, targets, description="", unit=None, decimals=None,
            thresholds=None, mappings=None, color_mode=None, minv=None, maxv=None,
            options=None, custom=None, overrides=(), transformations=(), links=(), no_value=None):
        defaults = {"custom": custom or {}, "mappings": mappings or [],
                    "thresholds": thresholds or steps(GREEN)}
        for key, val in (("unit", unit), ("decimals", decimals), ("min", minv),
                         ("max", maxv), ("noValue", no_value)):
            if val is not None:
                defaults[key] = val
        if color_mode:
            defaults["color"] = {"mode": color_mode}
        if links:
            defaults["links"] = list(links)
        panel = {"type": kind, "title": title, "description": description, "datasource": DS,
                 "gridPos": self._place(w, h),
                 "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
                 "fieldConfig": {"defaults": defaults, "overrides": list(overrides)},
                 "options": options or {}}
        if transformations:
            panel["transformations"] = list(transformations)
        self.doc["panels"].append(panel)
        return panel

    # Panel kinds, each with this project's house style.

    def stat(self, title, expr, w=3, h=4, legend="", text_mode="value", graph=False,
             background=True, **kw):
        kw.setdefault("color_mode", "thresholds")
        return self.add("stat", title, w, h, [q(expr, legend, instant=not graph)], options={
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background" if background else "value",
            "graphMode": "area" if graph else "none", "justifyMode": "center",
            "textMode": text_mode, "orientation": "auto", "wideLayout": True,
            "showPercentChange": False}, **kw)

    def gauge(self, title, expr, w=3, h=4, **kw):
        kw.setdefault("color_mode", "thresholds")
        return self.add("gauge", title, w, h, [q(expr, instant=True)], options={
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdMarkers": True, "showThresholdLabels": False, "minVizHeight": 75,
            "minVizWidth": 75, "sizing": "auto"}, minv=0, **kw)

    def series(self, title, targets, w=12, h=9, stack=False, fill=10, bars=False,
               legend_calcs=("mean", "max", "lastNotNull"), legend_table=True, **kw):
        custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1,
                  "fillOpacity": 70 if bars else (60 if stack else fill),
                  "gradientMode": "none" if stack or bars else "opacity",
                  "showPoints": "never", "spanNulls": False, "lineInterpolation": "smooth",
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                  "axisPlacement": "auto", "axisSoftMin": 0, "barAlignment": 0,
                  "thresholdsStyle": {"mode": "off"}}
        custom.update(kw.pop("custom", {}))
        kw.setdefault("color_mode", "palette-classic")
        return self.add("timeseries", title, w, h, targets, custom=custom, options={
            "legend": {"displayMode": "table" if legend_table else "list",
                       "placement": "right" if w == 24 and legend_table else "bottom",
                       "showLegend": True, "calcs": list(legend_calcs)},
            "tooltip": {"mode": "multi", "sort": "desc"}}, **kw)

    def timeline(self, title, targets, w=24, h=6, **kw):
        kw.setdefault("color_mode", "thresholds")
        return self.add("state-timeline", title, w, h, targets, options={
            "mergeValues": True, "showValue": "never", "alignValue": "left", "rowHeight": 0.85,
            "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "single", "sort": "none"}}, custom={"fillOpacity": 80, "lineWidth": 0}, **kw)

    def table(self, title, columns, by, w=24, h=8, sort=None, extra_overrides=(), hide=(), **kw):
        """columns: [(header, expr, field overrides)]; one row per distinct `by` labels."""
        if by[0] == "product":
            columns = [(header, with_product(expr, by[1:]), props) for header, expr, props in columns]
        targets = [q(expr, table=True) for _, expr, _ in columns]
        rename = {f"Value #{chr(65 + i)}": header for i, (header, _, _) in enumerate(columns)}
        order = {name: i for i, name in enumerate([*by, *(h for h, _, _ in columns)])}
        exclude = {"Time": True, **{name: True for name in hide}}
        overrides = [override(header, **props) for header, _, props in columns if props]
        return self.add("table", title, w, h, targets, overrides=[*overrides, *extra_overrides],
                        transformations=[
                            {"id": "merge", "options": {}},
                            {"id": "organize", "options": {"excludeByName": exclude,
                                                           "renameByName": rename,
                                                           "indexByName": order}}],
                        options={"showHeader": True, "cellHeight": "sm",
                                 "footer": {"show": False, "reducer": ["sum"], "fields": ""},
                                 "sortBy": [{"displayName": sort or by[0], "desc": False}]},
                        custom={"align": "auto", "cellOptions": {"type": "auto"},
                                "inspect": False, "filterable": False}, **kw)

    def bars(self, title, expr, legend, w=12, h=8, **kw):
        return self.add("bargauge", title, w, h, [q(expr, legend, instant=True)], options={
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "horizontal", "displayMode": "gradient", "valueMode": "color",
            "namePlacement": "left", "showUnfilled": True, "sizing": "auto", "minVizHeight": 16,
            "maxVizHeight": 28, "text": {"titleSize": 13, "valueSize": 13},
            "legend": {"showLegend": False}}, **kw)

    def text(self, title, markdown, w=24, h=3):
        panel = self.add("text", title, w, h, [], options={"mode": "markdown", "content": markdown})
        del panel["datasource"], panel["targets"]
        return panel

    def done(self):
        for i, panel in enumerate(self.doc["panels"], start=1):
            panel["id"] = i
        return self.doc


# Table cell styles.
UP_CELL = dict(mappings=UP_DOWN, custom__cellOptions={"type": "color-background", "mode": "basic"},
               custom__width=80)
def gauge_cell(thresholds):
    return dict(unit="percentunit", decimals=1, min=0, max=1, thresholds=thresholds,
                color={"mode": "thresholds"}, custom__width=150,
                custom__cellOptions={"type": "gauge", "mode": "basic", "valueDisplayMode": "text"})
def text_cell(unit, thresholds, decimals=None):
    props = dict(unit=unit, thresholds=thresholds, color={"mode": "thresholds"},
                 custom__cellOptions={"type": "color-text"})
    if decimals is not None:
        props["decimals"] = decimals
    return props


def with_product(expr, keys):
    """Takes product from the live probes rather than from history.

    A 7-day window still holds series from before a probe changed product, and
    grouping them by product would add a duplicate, product-less row to the table.
    """
    inner = f"by (product, {', '.join(keys)})"
    assert inner in expr, expr
    by = ", ".join(keys)
    return (f"({expr.replace(inner, f'by ({by})')}) + on ({by}) group_left (product) "
            f"(0 * max by ({by}, product) (probe_success{{{BB}}}))")


def phase_overrides():
    return [override(label, color=color(c)) for _, label, c in PHASES]


def phase_targets(selector, agg="avg"):
    return [q(f'{agg}(probe_http_duration_seconds{{{selector}, phase="{phase}"}})', label)
            for phase, label, _ in PHASES]


# ---------------------------------------------------------------------------

def command_center():
    b = Board("command-center", "Command Center",
              "Everything at once: every service from outside, every machine from inside, and what is firing.",
              time="now-24h")

    b.row("At a glance")
    b.stat("Endpoints down", f"count(probe_success{{{BB}}} == 0) or vector(0)", w=4,
           thresholds=T_DOWN_COUNT, description="Probed URLs failing right now.")
    b.stat("Firing alerts", 'count(ALERTS{alertstate="firing", alertname!="Watchdog"}) or vector(0)',
           w=4, thresholds=T_DOWN_COUNT)
    b.stat("Machines down", 'count(up{job="node"} == 0) or vector(0)', w=4, thresholds=T_DOWN_COUNT)
    b.stat("Availability, 24 h", f"avg(avg_over_time(probe_success{{{BB}}}[24h]))", w=4,
           unit="percentunit", decimals=2, thresholds=T_UPTIME,
           description="Share of all probes that succeeded over the last day.")
    b.stat("Slowest response now", f"max(probe_duration_seconds{{{BB}}})", w=4, unit="s",
           decimals=2, thresholds=T_RESPONSE)
    b.stat("Soonest certificate expiry", f"min((probe_ssl_earliest_cert_expiry{{{BB}}} - time()) / 86400)",
           w=4, unit=DAYS, decimals=0, thresholds=T_CERT)

    b.row("Services")
    service_link = {"title": "Open ${__data.fields.product}",
                    "url": "/d/service/service?var-product=${__data.fields.product}&${__url_time_range}"}
    b.table("Services", [
        ("Status", f"min by (product, service) (probe_success{{{BB}}})", UP_CELL),
        ("Endpoints", f"count by (product, service) (probe_success{{{BB}}})", dict(custom__width=90)),
        ("Response", f"max by (product, service) (probe_duration_seconds{{{BB}}})", text_cell("s", T_RESPONSE, 2)),
        ("p95, 24 h", f"max by (product, service) (quantile_over_time(0.95, probe_duration_seconds{{{BB}}}[24h]))",
         text_cell("s", T_RESPONSE, 2)),
        ("Uptime, 24 h", f"avg by (product, service) (avg_over_time(probe_success{{{BB}}}[24h]))",
         text_cell("percentunit", T_UPTIME, 2)),
        ("Uptime, 7 d", f"avg by (product, service) (avg_over_time(probe_success{{{BB}}}[7d]))",
         text_cell("percentunit", T_UPTIME, 2)),
        ("Certificate", f"min by (product, service) ((probe_ssl_earliest_cert_expiry{{{BB}}} - time()) / 86400)",
         text_cell(DAYS, T_CERT, 0)),
    ], by=["product", "service"], h=13, sort="Product",
        extra_overrides=[override("product", links=[service_link], displayName="Product"),
                         override("service", links=[service_link], displayName="Service")],
        description="Click a product or service to open its page.")
    b.timeline("Up or down", [q(f"min by (service) (probe_success{{{BB}}})", "{{service}}")],
               h=9, mappings=UP_DOWN, thresholds=steps(RED, 1, GREEN))
    b.series("Response time by service", [q(f"max by (service) (probe_duration_seconds{{{BB}}})", "{{service}}")],
             w=24, h=9, unit="s", legend_calcs=("mean", "max", "lastNotNull"))

    b.row("Machines")
    host_link = {"title": "Open ${__data.fields.host}",
                 "url": "/d/hosts/machine?var-host=${__data.fields.host}&${__url_time_range}"}
    b.table("Machines", [
        ("Status", 'max by (host) (up{job="node"})', UP_CELL),
        ("CPU busy", '1 - avg by (host) (rate(node_cpu_seconds_total{mode="idle"}[5m]))', gauge_cell(T_USED)),
        ("Memory used", "1 - sum by (host) (node_memory_MemAvailable_bytes) / sum by (host) (node_memory_MemTotal_bytes)",
         gauge_cell(T_USED)),
        ("Root disk used", '1 - max by (host) (node_filesystem_avail_bytes{mountpoint="/"}) / max by (host) (node_filesystem_size_bytes{mountpoint="/"})',
         gauge_cell(T_USED)),
        ("Root disk free", 'max by (host) (node_filesystem_avail_bytes{mountpoint="/"})', dict(unit="bytes", decimals=1)),
        ("Fullest disk", f"max by (host) (1 - node_filesystem_avail_bytes{{{FS}}} / node_filesystem_size_bytes{{{FS}}})",
         gauge_cell(T_USED)),
        ("Load per core", 'max by (host) (node_load5) / count by (host) (node_cpu_seconds_total{mode="idle"})',
         text_cell("none", steps(GREEN, 0.8, ORANGE, 1.5, RED), 2)),
        ("Temperature", "max by (host) (node_hwmon_temp_celsius)", text_cell("celsius", T_TEMP, 0)),
        ("Up for", "max by (host) (time() - node_boot_time_seconds)", dict(unit="dtdurations", decimals=0)),
    ], by=["host"], h=6, extra_overrides=[override("host", links=[host_link], displayName="Machine")],
        description="Click a machine to open its page. More machines appear here as they join (phase 2 onwards).")

    b.row("Firing alerts")
    b.table("Firing alerts", [("Value", 'ALERTS{alertstate="firing", alertname!="Watchdog"}', None)],
            by=["alertname", "severity", "service", "host", "instance"], h=6,
            hide=["Value", "__name__", "alertstate", "job", "module", "product", "monitor", "when"],
            no_value="Nothing firing")
    return b.done()


def service():
    b = Board("service", "Service",
              "One product at a time, outside in: is each endpoint up, how fast is it, and where does the time go.",
              tags=["services"])
    b.var("product", "Product", f"label_values(probe_success{{{BB}}}, product)", default="Lightning")
    b.var("service", "Service", f'label_values(probe_success{{{BB}, product="$product"}}, service)',
          multi=True, include_all=True)
    P = f'{BB}, product="$product"'
    S = f'{BB}, service="$service"'

    b.row("$product at a glance")
    b.stat("Endpoints up", f"sum(probe_success{{{P}}}) / count(probe_success{{{P}}})", w=4,
           unit="percentunit", decimals=0, thresholds=steps(RED, 1, GREEN))
    b.stat("Uptime, 24 h", f"avg(avg_over_time(probe_success{{{P}}}[24h]))", w=4,
           unit="percentunit", decimals=3, thresholds=T_UPTIME)
    b.stat("Uptime, 7 d", f"avg(avg_over_time(probe_success{{{P}}}[7d]))", w=4,
           unit="percentunit", decimals=3, thresholds=T_UPTIME)
    b.stat("Slowest now", f"max(probe_duration_seconds{{{P}}})", w=4, unit="s", decimals=2, thresholds=T_RESPONSE)
    b.stat("p95, 24 h", f"max(quantile_over_time(0.95, probe_duration_seconds{{{P}}}[24h]))", w=4,
           unit="s", decimals=2, thresholds=T_RESPONSE)
    b.stat("Soonest certificate expiry", f"min((probe_ssl_earliest_cert_expiry{{{P}}} - time()) / 86400)",
           w=4, unit=DAYS, decimals=0, thresholds=T_CERT)
    b.series("Response time by service", [q(f"max by (service) (probe_duration_seconds{{{P}}})", "{{service}}")],
             w=24, h=7, unit="s")

    b.row("$service", repeat="service")
    b.stat("Status", f"min(probe_success{{{S}}})", mappings=UP_DOWN, thresholds=steps(RED, 1, GREEN))
    b.stat("Uptime, 24 h", f"avg(avg_over_time(probe_success{{{S}}}[24h]))", unit="percentunit",
           decimals=3, thresholds=T_UPTIME)
    b.stat("Uptime, 7 d", f"avg(avg_over_time(probe_success{{{S}}}[7d]))", unit="percentunit",
           decimals=3, thresholds=T_UPTIME)
    b.stat("Failed checks, 24 h", f"sum(sum_over_time((1 - probe_success{{{S}}})[24h:1m])) or vector(0)",
           thresholds=steps(GREEN, 1, ORANGE, 5, RED), decimals=0,
           description="Probes run once a minute; each failure counts once.")
    b.stat("Response now", f"max(probe_duration_seconds{{{S}}})", unit="s", decimals=2, thresholds=T_RESPONSE,
           background=False, graph=True)
    b.stat("p95, 24 h", f"max(quantile_over_time(0.95, probe_duration_seconds{{{S}}}[24h]))", unit="s",
           decimals=2, thresholds=T_RESPONSE, background=False)
    b.stat("HTTP status", f"max(probe_http_status_code{{{S}}})", decimals=0,
           thresholds=steps(RED, 200, GREEN, 400, ORANGE, 500, RED), background=False)
    b.stat("Certificate", f"min((probe_ssl_earliest_cert_expiry{{{S}}} - time()) / 86400)", unit=DAYS,
           decimals=0, thresholds=T_CERT, background=False)
    b.series("Where the time goes", phase_targets(S), stack=True, unit="s", overrides=phase_overrides(),
             description="Each probe's time split by phase, averaged across this service's endpoints. "
                         "Server is time to first byte: the application's own work.")
    # max by (instance): a relabelled probe must not show up twice while its old series ages out.
    b.series("Response time per endpoint", [q(f"max by (instance) (probe_duration_seconds{{{S}}})", "{{instance}}")], unit="s")
    b.timeline("Up or down per endpoint", [q(f"min by (instance) (probe_success{{{S}}})", "{{instance}}")], w=12, h=6,
               mappings=UP_DOWN, thresholds=steps(RED, 1, GREEN))
    b.table("Endpoints", [
        ("Status", f"max by (instance) (probe_success{{{S}}})", UP_CELL),
        ("HTTP", f"max by (instance) (probe_http_status_code{{{S}}})", dict(custom__width=70)),
        ("Response", f"max by (instance) (probe_duration_seconds{{{S}}})", text_cell("s", T_RESPONSE, 2)),
        ("Uptime, 7 d", f"avg by (instance) (avg_over_time(probe_success{{{S}}}[7d]))",
         text_cell("percentunit", T_UPTIME, 2)),
        ("Certificate", f"min by (instance) ((probe_ssl_earliest_cert_expiry{{{S}}} - time()) / 86400)",
         text_cell(DAYS, T_CERT, 0)),
    ], by=["instance"], w=12, h=6, extra_overrides=[override("instance", displayName="Endpoint", custom__minWidth=260)])
    return b.done()


def probes():
    b = Board("probes", "Probes and certificates",
              "Every probed URL side by side: latency by phase, redirects, TLS and certificate expiry.",
              tags=["services"])
    b.row("All endpoints")
    service_link = {"title": "Open ${__data.fields.product}",
                    "url": "/d/service/service?var-product=${__data.fields.product}&${__url_time_range}"}
    b.table("Endpoints", [
        ("Status", f"max by (product, service, instance) (probe_success{{{BB}}})", UP_CELL),
        ("HTTP", f"max by (product, service, instance) (probe_http_status_code{{{BB}}})", dict(custom__width=70)),
        ("Response", f"max by (product, service, instance) (probe_duration_seconds{{{BB}}})", text_cell("s", T_RESPONSE, 2)),
        ("p95, 24 h", f"max by (product, service, instance) (quantile_over_time(0.95, probe_duration_seconds{{{BB}}}[24h]))",
         text_cell("s", T_RESPONSE, 2)),
        ("Redirects", f"max by (product, service, instance) (probe_http_redirects{{{BB}}})", dict(custom__width=90)),
        ("Certificate", f"min by (product, service, instance) ((probe_ssl_earliest_cert_expiry{{{BB}}} - time()) / 86400)",
         text_cell(DAYS, T_CERT, 0)),
    ], by=["product", "service", "instance"], h=16, sort="Product",
        extra_overrides=[override("product", links=[service_link], displayName="Product"),
                         override("service", displayName="Service"),
                         override("instance", displayName="Endpoint", custom__minWidth=320)])

    b.row("Latency")
    b.series("Total response time", [q(f"max by (instance) (probe_duration_seconds{{{BB}}})", "{{instance}}")], w=24, h=10, unit="s")
    b.series("Server time (time to first byte)",
             [q(f'max by (instance) (probe_http_duration_seconds{{{BB}, phase="processing"}})', "{{instance}}")], w=24, h=10, unit="s",
             description="The application's own work, with network and TLS taken out.")
    b.series("DNS lookup", [q(f"max by (instance) (probe_dns_lookup_time_seconds{{{BB}}})", "{{instance}}")], h=8, unit="s")
    b.series("TLS handshake", [q(f'max by (instance) (probe_http_duration_seconds{{{BB}, phase="tls"}})', "{{instance}}")],
             h=8, unit="s")

    b.row("Certificates")
    b.bars("Days until certificate expiry",
           f"sort(min by (instance) ((probe_ssl_earliest_cert_expiry{{{BB}}} - time()) / 86400))", "{{instance}}",
           w=24, h=12, unit=DAYS, decimals=0, thresholds=T_CERT, minv=0, maxv=90, color_mode="thresholds")
    return b.done()


def machine():
    b = Board("hosts", "Machine",
              "One machine in depth: CPU, memory, every disk's free space and forecast, I/O, network and hardware.",
              tags=["machines"], time="now-6h", refresh="30s")
    b.var("host", "Machine", "label_values(node_uname_info, host)")
    H = 'host="$host"'

    b.row("$host")
    b.stat("Up for", f"time() - node_boot_time_seconds{{{H}}}", unit="dtdurations", decimals=0,
           background=False, color_mode="fixed", custom=None)
    b.gauge("CPU busy", f'1 - avg(rate(node_cpu_seconds_total{{{H}, mode="idle"}}[5m]))',
            unit="percentunit", maxv=1, decimals=0, thresholds=T_USED)
    b.gauge("Memory used", f"1 - node_memory_MemAvailable_bytes{{{H}}} / node_memory_MemTotal_bytes{{{H}}}",
            unit="percentunit", maxv=1, decimals=0, thresholds=T_USED)
    b.gauge("Root disk used",
            f'1 - node_filesystem_avail_bytes{{{H}, mountpoint="/"}} / node_filesystem_size_bytes{{{H}, mountpoint="/"}}',
            unit="percentunit", maxv=1, decimals=0, thresholds=T_USED)
    b.stat("Root disk free", f'node_filesystem_avail_bytes{{{H}, mountpoint="/"}}', unit="bytes", decimals=1,
           thresholds=steps(RED, 2e9, ORANGE, 5e9, GREEN), background=False)
    b.stat("Load per core, 5 m",
           f'node_load5{{{H}}} / count(node_cpu_seconds_total{{{H}, mode="idle"}})', decimals=2,
           thresholds=steps(GREEN, 0.8, ORANGE, 1.5, RED), background=False)
    b.gauge("Temperature", f"max(node_hwmon_temp_celsius{{{H}}})", unit="celsius", maxv=100, decimals=0,
            thresholds=T_TEMP)
    b.stat("Swap used", f"node_memory_SwapTotal_bytes{{{H}}} - node_memory_SwapFree_bytes{{{H}}}",
           unit="bytes", decimals=1, background=False, color_mode="fixed", no_value="none")
    b.stat("System", f"node_os_info{{{H}}}", legend="{{pretty_name}}", text_mode="name", w=6, h=3,
           background=False, color_mode="fixed")
    b.stat("Kernel", f"node_uname_info{{{H}}}", legend="{{release}} {{machine}}", text_mode="name", w=6, h=3,
           background=False, color_mode="fixed")
    b.stat("CPU cores", f'count(node_cpu_seconds_total{{{H}, mode="idle"}})', w=4, h=3, background=False,
           color_mode="fixed")
    b.stat("Memory", f"node_memory_MemTotal_bytes{{{H}}}", unit="bytes", decimals=1, w=4, h=3,
           background=False, color_mode="fixed")
    b.stat("Disk total", f"sum(node_filesystem_size_bytes{{{H}, {FS}}})", unit="bytes", decimals=1, w=4, h=3,
           background=False, color_mode="fixed")

    b.row("Disk space")
    b.bars("Used, per filesystem",
           f"1 - node_filesystem_avail_bytes{{{H}, {FS}}} / node_filesystem_size_bytes{{{H}, {FS}}}",
           "{{mountpoint}}", w=8, h=8, unit="percentunit", decimals=1, minv=0, maxv=1,
           thresholds=T_USED, color_mode="thresholds")
    b.table("Filesystems", [
        ("Size", f"max by (mountpoint, fstype, device) (node_filesystem_size_bytes{{{H}, {FS}}})",
         dict(unit="bytes", decimals=1)),
        ("Free", f"max by (mountpoint, fstype, device) (node_filesystem_avail_bytes{{{H}, {FS}}})",
         dict(unit="bytes", decimals=1)),
        ("Used", f"max by (mountpoint, fstype, device) (1 - node_filesystem_avail_bytes{{{H}, {FS}}} / node_filesystem_size_bytes{{{H}, {FS}}})",
         gauge_cell(T_USED)),
        ("Full in", f"max by (mountpoint, fstype, device) (clamp_max(node_filesystem_avail_bytes{{{H}, {FS}}} "
                    f"/ clamp_min(-deriv(node_filesystem_avail_bytes{{{H}, {FS}}}[6h]), 1e-9) / 86400, 366))",
         dict(text_cell(DAYS, steps(RED, 3, ORANGE, 14, GREEN), 0),
              mappings=[{"type": "range", "options": {"from": 365, "to": None,
                                                      "result": {"text": "not filling", "color": GREEN}}}])),
        ("Inodes used", f"max by (mountpoint, fstype, device) (1 - node_filesystem_files_free{{{H}, {FS}}} / node_filesystem_files{{{H}, {FS}}})",
         gauge_cell(T_USED)),
    ], by=["mountpoint", "fstype", "device"], w=16, h=8,
        extra_overrides=[override("mountpoint", displayName="Mount"), override("fstype", displayName="Type"),
                         override("device", displayName="Device")],
        description="Full in: the last 6 hours' trend projected forward. 'not filling' means over a year away, or shrinking.")
    b.series("Free space over time", [q(f"node_filesystem_avail_bytes{{{H}, {FS}}}", "{{mountpoint}}")],
             w=24, h=7, unit="bytes")

    b.row("CPU")
    b.series("CPU by mode", [q(f'sum by (mode) (rate(node_cpu_seconds_total{{{H}, mode!="idle"}}[$__rate_interval])) '
                               f'/ scalar(count(node_cpu_seconds_total{{{H}, mode="idle"}}))', "{{mode}}")],
             stack=True, unit="percentunit", maxv=1)
    b.series("Load vs cores", [
        q(f"node_load1{{{H}}}", "1 m"), q(f"node_load5{{{H}}}", "5 m"), q(f"node_load15{{{H}}}", "15 m"),
        q(f'count(node_cpu_seconds_total{{{H}, mode="idle"}})', "cores")],
        overrides=[override("cores", color=color(RED), custom__lineStyle={"fill": "dash", "dash": [10, 10]},
                            custom__fillOpacity=0)])
    b.series("Busy per core", [q(f'1 - rate(node_cpu_seconds_total{{{H}, mode="idle"}}[$__rate_interval])', "cpu {{cpu}}")],
             unit="percentunit", maxv=1, legend_table=False, legend_calcs=())
    b.series("CPU frequency", [q(f"avg(node_cpu_scaling_frequency_hertz{{{H}}})", "average"),
                               q(f"max(node_cpu_scaling_frequency_max_hertz{{{H}}})", "maximum")],
             unit="hertz", overrides=[override("maximum", color=color(GREY),
                                               custom__lineStyle={"fill": "dash", "dash": [10, 10]},
                                               custom__fillOpacity=0)],
             description="Held below maximum while idle; pinned below it under load means throttling.")

    b.row("Memory")
    b.series("Memory", [
        q(f"node_memory_MemTotal_bytes{{{H}}} - node_memory_MemAvailable_bytes{{{H}}}", "used"),
        q(f"node_memory_Cached_bytes{{{H}}} + node_memory_Buffers_bytes{{{H}}} + node_memory_SReclaimable_bytes{{{H}}}",
          "cache and buffers"),
        q(f"node_memory_MemFree_bytes{{{H}}}", "free")],
        stack=True, unit="bytes",
        overrides=[override("used", color=color(ORANGE)), override("cache and buffers", color=color(BLUE)),
                   override("free", color=color(GREEN))],
        description="Cache is given back on demand, so 'used' is what matters.")
    b.series("Swap and out-of-memory kills", [
        q(f"node_memory_SwapTotal_bytes{{{H}}} - node_memory_SwapFree_bytes{{{H}}}", "swap used"),
        q(f"increase(node_vmstat_oom_kill{{{H}}}[$__rate_interval])", "OOM kills")],
        overrides=[override("swap used", unit="bytes"),
                   override("OOM kills", unit="short", color=color(RED), custom__drawStyle="bars",
                            custom__axisPlacement="right")])

    b.row("Disk I/O")
    b.series("Throughput", [
        q(f"rate(node_disk_read_bytes_total{{{H}, {DISK}}}[$__rate_interval])", "read {{device}}"),
        q(f"-rate(node_disk_written_bytes_total{{{H}, {DISK}}}[$__rate_interval])", "write {{device}}")],
        unit="Bps", description="Reads above the line, writes below.")
    b.series("Operations per second", [
        q(f"rate(node_disk_reads_completed_total{{{H}, {DISK}}}[$__rate_interval])", "read {{device}}"),
        q(f"-rate(node_disk_writes_completed_total{{{H}, {DISK}}}[$__rate_interval])", "write {{device}}")],
        unit="iops")
    b.series("Busy time", [q(f"rate(node_disk_io_time_seconds_total{{{H}, {DISK}}}[$__rate_interval])", "{{device}}")],
             unit="percentunit", maxv=1, description="Share of time the device had I/O in flight.")
    b.series("Average latency", [
        q(f"rate(node_disk_read_time_seconds_total{{{H}, {DISK}}}[$__rate_interval]) / "
          f"rate(node_disk_reads_completed_total{{{H}, {DISK}}}[$__rate_interval])", "read {{device}}"),
        q(f"rate(node_disk_write_time_seconds_total{{{H}, {DISK}}}[$__rate_interval]) / "
          f"rate(node_disk_writes_completed_total{{{H}, {DISK}}}[$__rate_interval])", "write {{device}}")],
        unit="s")

    b.row("Network")
    b.series("Traffic", [
        q(f"rate(node_network_receive_bytes_total{{{H}, {NIC}}}[$__rate_interval]) * 8", "in {{device}}"),
        q(f"-rate(node_network_transmit_bytes_total{{{H}, {NIC}}}[$__rate_interval]) * 8", "out {{device}}")],
        unit="bps", description="Received above the line, sent below.")
    b.series("Errors and drops", [
        q(f"rate(node_network_receive_errs_total{{{H}, {NIC}}}[$__rate_interval])", "receive errors {{device}}"),
        q(f"rate(node_network_transmit_errs_total{{{H}, {NIC}}}[$__rate_interval])", "send errors {{device}}"),
        q(f"rate(node_network_receive_drop_total{{{H}, {NIC}}}[$__rate_interval])", "receive drops {{device}}"),
        q(f"rate(node_network_transmit_drop_total{{{H}, {NIC}}}[$__rate_interval])", "send drops {{device}}")],
        unit="pps")
    b.series("TCP connections", [q(f"node_netstat_Tcp_CurrEstab{{{H}}}", "established"),
                                 q(f"node_sockstat_TCP_tw{{{H}}}", "time wait")])
    b.series("TCP retransmits and timeouts", [
        q(f"rate(node_netstat_Tcp_RetransSegs{{{H}}}[$__rate_interval])", "retransmitted segments"),
        q(f"rate(node_netstat_TcpExt_TCPTimeouts{{{H}}}[$__rate_interval])", "timeouts")], unit="pps")

    b.row("Hardware and system")
    b.series("Temperatures", [q(f"node_hwmon_temp_celsius{{{H}}}", "{{chip}} {{sensor}}")], w=8,
             unit="celsius", thresholds=T_TEMP, custom={"thresholdsStyle": {"mode": "dashed"}},
             legend_table=False, legend_calcs=())
    b.timeline("Pi power and throttling", [q(f'pi_throttle_state{{{H}, when="now"}}', "{{condition}}")], w=8, h=8,
               mappings=[{"type": "value", "options": {"0": {"text": "OK", "color": GREEN, "index": 0},
                                                       "1": {"text": "ACTIVE", "color": RED, "index": 1}}}],
               thresholds=steps(GREEN, 1, RED), no_value="Not a Raspberry Pi",
               description="Under-voltage, frequency capping and thermal throttling, as the firmware reports them.")
    b.series("Clock offset", [q(f"node_timex_offset_seconds{{{H}}}", "offset")], w=8, unit="s",
             legend_table=False, legend_calcs=())
    b.series("Processes", [q(f"node_procs_running{{{H}}}", "running"), q(f"node_procs_blocked{{{H}}}", "blocked on I/O")],
             w=8, legend_table=False, legend_calcs=())
    b.series("Context switches and interrupts", [
        q(f"rate(node_context_switches_total{{{H}}}[$__rate_interval])", "context switches"),
        q(f"rate(node_intr_total{{{H}}}[$__rate_interval])", "interrupts")], w=8, unit="ops",
        legend_table=False, legend_calcs=())
    b.series("Open files", [q(f"node_filefd_allocated{{{H}}}", "allocated")], w=8, legend_table=False,
             legend_calcs=())
    return b.done()


BOARDS = {"command-center.json": command_center, "service.json": service,
          "probes.json": probes, "hosts.json": machine}


def render():
    return {name: json.dumps(build(), indent=2) + "\n" for name, build in BOARDS.items()}


def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    rendered = render()
    stale = [p.name for p in OUT.glob("*.json") if p.name not in rendered]
    for name in stale:
        (OUT / name).unlink()
    for name, text in rendered.items():
        (OUT / name).write_text(text)
    print(f"dashboards: wrote {', '.join(rendered)}" + (f"; removed {', '.join(stale)}" if stale else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
