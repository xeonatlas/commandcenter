import importlib.util
import ipaddress
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("render_inventory", ROOT / "scripts" / "render_inventory.py")
render_inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_inventory)
HOSTS = yaml.safe_load((ROOT / "inventory" / "hosts.yml").read_text())["hosts"]


def test_rendered_files_match_the_inventory():
    for path, text in render_inventory.render().items():
        assert path.read_text() == text, f"run `make inventory` and commit {path.name}"


def test_hosts_have_unique_labels_and_tunnel_addresses_inside_wg_mon():
    net = ipaddress.ip_network("10.98.0.0/24")
    tunnel = [h for h in HOSTS if "tunnel_ip" in h]
    assert len({h["host"] for h in HOSTS}) == len(HOSTS)
    assert len({h["tunnel_ip"] for h in tunnel}) == len(tunnel)
    for h in tunnel:
        ip = ipaddress.ip_address(h["tunnel_ip"])
        assert ip in net and ip != ipaddress.ip_address("10.98.0.1"), h  # .1 is the Pi


def test_each_host_is_reached_one_way_and_serves_known_exporters():
    home = ipaddress.ip_network("10.0.0.0/24")
    for h in HOSTS:
        assert ("tunnel_ip" in h) != ("lan_ip" in h), f"{h['host']}: tunnel_ip or lan_ip, not both"
        if "lan_ip" in h:
            assert ipaddress.ip_address(h["lan_ip"]) in home, h
        else:
            assert h.get("endpoint") and "wg_public_key" in h, h
        assert set(h.get("exporters", [])) <= set(render_inventory.EXPORTERS), h


def test_gateway_targets_follow_the_exporters_list():
    etcd = yaml.safe_load(render_inventory.gateway_targets({"hosts": HOSTS}, "etcd"))
    want = {h["host"] for h in HOSTS if "etcd" in h.get("exporters", [])}
    assert {g["labels"]["host"] for g in etcd} == want
    assert all(t.endswith(":9900") for g in etcd for t in g["targets"])


def test_only_enrolled_hosts_are_scraped():
    # A host with no key has no tunnel yet; scraping it would only raise HostDown.
    rendered = render_inventory.node_targets({"hosts": [dict(HOSTS[0], wg_public_key="")]})
    assert HOSTS[0]["tunnel_ip"] not in rendered
    assert "127.0.0.1:9100" in rendered
