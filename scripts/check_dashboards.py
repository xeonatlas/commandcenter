#!/usr/bin/env python3
"""Lints the provisioned Grafana dashboards before they reach the Pi.

Grafana loads a broken dashboard without complaint. A missing uid gives it a new
URL on every restart, which breaks the links in Pushover messages, and a
datasource uid that is not provisioned renders every panel as "datasource not
found". Both are cheap to catch here and tedious to diagnose from a phone.
"""

import json
import sys
from pathlib import Path

DATASOURCE_UID = "prometheus"


def _datasource_uids(node):
    if isinstance(node, dict):
        ds = node.get("datasource")
        if isinstance(ds, dict) and "uid" in ds:
            yield ds["uid"]
        for value in node.values():
            yield from _datasource_uids(value)
    elif isinstance(node, list):
        for item in node:
            yield from _datasource_uids(item)


def lint_dashboard(doc: dict) -> list[str]:
    problems = [f"missing {key}" for key in ("uid", "title") if not doc.get(key)]
    panels = doc.get("panels", [])
    ids = [panel.get("id") for panel in panels]
    if None in ids:
        problems.append("a panel has no id")
    dupes = sorted({i for i in ids if i is not None and ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate panel ids {dupes}")
    for uid in _datasource_uids([panels, doc.get("templating", {})]):
        if uid != DATASOURCE_UID:
            problems.append(f"datasource uid {uid!r} is not provisioned (expected {DATASOURCE_UID!r})")
    for panel in panels:
        for target in panel.get("targets", []):
            if not target.get("expr") or not target.get("refId"):
                problems.append(f"panel {panel.get('title')!r} has a target without expr or refId")
    return problems


def lint_dir(directory: Path) -> list[str]:
    files = sorted(directory.glob("*.json"))
    if not files:
        return [f"no dashboards in {directory}"]
    problems, seen = [], {}
    for path in files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: invalid JSON: {exc}")
            continue
        problems += [f"{path.name}: {problem}" for problem in lint_dashboard(doc)]
        uid = doc.get("uid")
        if uid and uid in seen:
            problems.append(f"{path.name}: uid {uid!r} already used by {seen[uid]}")
        elif uid:
            seen[uid] = path.name
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_dashboards.py DIRECTORY", file=sys.stderr)
        return 2
    directory = Path(argv[1])
    problems = lint_dir(directory)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"dashboards: {len(list(directory.glob('*.json')))} clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
