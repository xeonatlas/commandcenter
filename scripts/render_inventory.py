#!/usr/bin/env python3
"""Renders inventory/hosts.yml into the files that depend on it.

- prometheus/targets/node.yml: what Prometheus scrapes, with the host label.
- hosts/pi/wg-mon-peers.conf: the Pi's WireGuard peers (public keys only).

A host appears in neither until enrollment has recorded its public key, so a
half-enrolled host cannot raise HostDown.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "inventory" / "hosts.yml"
NODE_TARGETS = ROOT / "prometheus" / "targets" / "node.yml"
PEERS = ROOT / "hosts" / "pi" / "wg-mon-peers.conf"
WG_PORT = 51821
HEADER = "# Rendered from inventory/hosts.yml by scripts/render_inventory.py; do not edit.\n"


def enrolled(inventory):
    return [h for h in inventory["hosts"] if h.get("wg_public_key")]


def node_targets(inventory):
    groups = [{"labels": {"host": "command-center", "role": "command-center"}, "targets": ["127.0.0.1:9100"]}]
    for h in enrolled(inventory):
        groups.append({"labels": {"host": h["host"], "hostname": h["hostname"], "role": h["role"]},
                       "targets": [f"{h['tunnel_ip']}:9100"]})
    return HEADER + yaml.safe_dump(groups, sort_keys=False, default_flow_style=None)


def peers(inventory):
    blocks = [
        f"[Peer]\n# {h['host']} ({h['hostname']})\nPublicKey = {h['wg_public_key']}\n"
        f"Endpoint = {h['endpoint']}:{WG_PORT}\nAllowedIPs = {h['tunnel_ip']}/32\n"
        # The Pi is behind the home router; its keepalives hold the tunnel open.
        "PersistentKeepalive = 25\n"
        for h in enrolled(inventory)
    ]
    return HEADER + "".join("\n" + b for b in blocks)


def render():
    inventory = yaml.safe_load(INVENTORY.read_text())
    return {NODE_TARGETS: node_targets(inventory), PEERS: peers(inventory)}


def main():
    for path, text in render().items():
        path.write_text(text)
        print(f"render_inventory: wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
