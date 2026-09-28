#!/usr/bin/env python3
"""Renders inventory/hosts.yml into the files that depend on it.

- prometheus/targets/node.yml: node_exporter on every host (:9100), with the host label.
- prometheus/targets/<exporter>.yml: each host's metrics gateway (:9900) for that exporter.
- hosts/pi/wg-mon-peers.conf: the Pi's WireGuard peers (public keys only).

A tunnel host appears nowhere until enrollment has recorded its public key, so a
half-enrolled host cannot raise HostDown. LAN hosts need no key.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "inventory" / "hosts.yml"
TARGETS = ROOT / "prometheus" / "targets"
NODE_TARGETS = TARGETS / "node.yml"
EXPORTERS = ("postgres", "patroni", "etcd", "haproxy")
PEERS = ROOT / "hosts" / "pi" / "wg-mon-peers.conf"
WG_PORT = 51821
HEADER = "# Rendered from inventory/hosts.yml by scripts/render_inventory.py; do not edit.\n"


def enrolled(inventory):
    return [h for h in inventory["hosts"] if h.get("wg_public_key") or h.get("lan_ip")]


def address(h):
    return h.get("tunnel_ip") or h["lan_ip"]


def labels(h):
    return {"host": h["host"], "hostname": h["hostname"], "role": h["role"]}


def dump(groups):
    return HEADER + yaml.safe_dump(groups, sort_keys=False, default_flow_style=None)


def node_targets(inventory):
    groups = [{"labels": {"host": "command-center", "role": "command-center"}, "targets": ["127.0.0.1:9100"]}]
    groups += [{"labels": labels(h), "targets": [f"{address(h)}:9100"]} for h in enrolled(inventory)]
    return dump(groups)


def gateway_targets(inventory, exporter):
    return dump([{"labels": labels(h), "targets": [f"{address(h)}:9900"]}
                 for h in enrolled(inventory) if exporter in h.get("exporters", [])])


def peers(inventory):
    blocks = [
        f"[Peer]\n# {h['host']} ({h['hostname']})\nPublicKey = {h['wg_public_key']}\n"
        f"Endpoint = {h['endpoint']}:{WG_PORT}\nAllowedIPs = {h['tunnel_ip']}/32\n"
        # The Pi is behind the home router; its keepalives hold the tunnel open.
        "PersistentKeepalive = 25\n"
        for h in enrolled(inventory) if h.get("tunnel_ip")
    ]
    return HEADER + "".join("\n" + b for b in blocks)


def render():
    inventory = yaml.safe_load(INVENTORY.read_text())
    files = {NODE_TARGETS: node_targets(inventory), PEERS: peers(inventory)}
    files.update({TARGETS / f"{e}.yml": gateway_targets(inventory, e) for e in EXPORTERS})
    return files


def main():
    for path, text in render().items():
        path.write_text(text)
        print(f"render_inventory: wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
