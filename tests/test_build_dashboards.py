import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_dashboards", ROOT / "scripts" / "build_dashboards.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_committed_dashboards_match_the_generator():
    rendered = build.render()
    on_disk = {p.name: p.read_text() for p in (ROOT / "grafana" / "dashboards").glob("*.json")}
    assert on_disk == rendered, "run `make dashboards` and commit the result"


def test_every_panel_fits_the_grid_and_none_overlap():
    for name, text in build.render().items():
        taken = set()
        for panel in json.loads(text)["panels"]:
            pos = panel["gridPos"]
            assert pos["x"] + pos["w"] <= 24, (name, panel["title"])
            cells = {(x, y) for x in range(pos["x"], pos["x"] + pos["w"])
                     for y in range(pos["y"], pos["y"] + pos["h"])}
            assert not cells & taken, (name, panel["title"])
            taken |= cells


def test_pushover_links_still_resolve():
    # Alertmanager messages link to /d/command-center; the uid must not change.
    uids = {json.loads(text)["uid"] for text in build.render().values()}
    assert "command-center" in uids
