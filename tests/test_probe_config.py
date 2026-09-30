from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(relative: str):
    return yaml.safe_load((ROOT / relative).read_text())


def seconds(duration: str) -> int:
    return int(duration[:-1]) * {"s": 1, "m": 60}[duration[-1]]


def test_every_probe_uses_a_module_that_exists():
    modules = load("blackbox/blackbox.yml")["modules"]
    for group in load("prometheus/targets/probes.yml"):
        assert group["labels"]["module"] in modules, group


def test_every_probe_group_names_its_service_and_product_and_has_targets():
    # product picks the Grafana page a probe appears on; without it the probe is on none.
    for group in load("prometheus/targets/probes.yml"):
        assert group["labels"].get("service"), group
        assert group["labels"].get("product"), group
        assert group["targets"], group


def test_http_modules_prefer_ipv4_with_fallback():
    # A home connection without working IPv6 must not make a dual-stack site look down.
    for name, module in load("blackbox/blackbox.yml")["modules"].items():
        assert module["http"]["preferred_ip_protocol"] == "ip4", name
        assert module["http"]["ip_protocol_fallback"] is True, name


def test_probe_timeouts_fit_inside_the_scrape_timeout():
    jobs = load("prometheus/prometheus.yml")["scrape_configs"]
    limit = seconds(next(j for j in jobs if j["job_name"] == "blackbox")["scrape_timeout"])
    for name, module in load("blackbox/blackbox.yml")["modules"].items():
        assert seconds(module["timeout"]) < limit, name


def test_post_modules_send_json_and_check_the_answer_is_the_app():
    # A 400 alone could be the platform's own error page; the body match proves the function ran.
    for name, module in load("blackbox/blackbox.yml")["modules"].items():
        http = module["http"]
        if http.get("method") == "POST":
            assert http["headers"]["Content-Type"] == "application/json", name
            assert http["body"], name
            assert http["fail_if_body_not_matches_regexp"], name
