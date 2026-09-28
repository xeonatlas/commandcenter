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
    assert len({h["host"] for h in HOSTS}) == len(HOSTS)
    assert len({h["tunnel_ip"] for h in HOSTS}) == len(HOSTS)
    for h in HOSTS:
        ip = ipaddress.ip_address(h["tunnel_ip"])
        assert ip in net and ip != ipaddress.ip_address("10.98.0.1"), h  # .1 is the Pi


def test_only_enrolled_hosts_are_scraped():
    # A host with no key has no tunnel yet; scraping it would only raise HostDown.
    rendered = render_inventory.node_targets({"hosts": [dict(HOSTS[0], wg_public_key="")]})
    assert HOSTS[0]["tunnel_ip"] not in rendered
    assert "127.0.0.1:9100" in rendered
