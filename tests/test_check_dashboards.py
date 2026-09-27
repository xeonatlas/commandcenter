import copy
import json
from pathlib import Path

from check_dashboards import lint_dashboard, lint_dir

DS = {"type": "prometheus", "uid": "prometheus"}


def good(**overrides):
    doc = {
        "uid": "x",
        "title": "X",
        "panels": [
            {
                "id": 1,
                "type": "stat",
                "title": "p",
                "datasource": dict(DS),
                "targets": [{"refId": "A", "expr": "up", "datasource": dict(DS)}],
            }
        ],
    }
    doc.update(overrides)
    return doc


def test_good_dashboard_passes():
    assert lint_dashboard(good()) == []


def test_missing_uid_is_reported():
    assert "missing uid" in lint_dashboard(good(uid=""))


def test_unprovisioned_datasource_is_reported():
    doc = good()
    doc["panels"][0]["targets"][0]["datasource"]["uid"] = "P1809F7CD0C75ACF3"
    assert any("not provisioned" in p for p in lint_dashboard(doc))


def test_duplicate_panel_ids_are_reported():
    doc = good()
    doc["panels"].append(copy.deepcopy(doc["panels"][0]))
    assert any("duplicate panel ids [1]" in p for p in lint_dashboard(doc))


def test_target_without_expr_is_reported():
    doc = good()
    doc["panels"][0]["targets"][0]["expr"] = ""
    assert any("without expr" in p for p in lint_dashboard(doc))


def test_uid_shared_across_files_is_reported(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(good()))
    (tmp_path / "b.json").write_text(json.dumps(good(title="Y")))
    assert any("already used by a.json" in p for p in lint_dir(tmp_path))


def test_repo_dashboards_are_clean():
    assert lint_dir(Path(__file__).resolve().parent.parent / "grafana" / "dashboards") == []
