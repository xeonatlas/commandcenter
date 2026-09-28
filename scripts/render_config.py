#!/usr/bin/env python3
"""Fills ${NAME} placeholders in a config template from a KEY=VALUE env file.

Alertmanager does not expand environment variables in its config, and its
secrets (Pushover keys, the heartbeat URL) must not live in git. So the template
is committed and the secrets are poured in on the Pi at deploy time.

Only the env file is consulted, never the process environment, so a render is
reproducible from the two files alone. A placeholder with no value, or an empty
one, is an error naming every such variable: a half-rendered config must never
reach Alertmanager.
"""

import os
import string
import sys


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.isidentifier():
            raise ValueError(f"line {number}: expected KEY=VALUE, got {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def render(template: str, values: dict[str, str]) -> str:
    tmpl = string.Template(template)
    missing = [name for name in tmpl.get_identifiers() if not values.get(name)]
    if missing:
        names = ", ".join(f"${{{name}}}" for name in missing)
        raise ValueError(f"no value in the env file for {names}")
    return tmpl.substitute(values)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: render_config.py TEMPLATE ENV_FILE OUTPUT", file=sys.stderr)
        return 2
    template_path, env_path, output_path = argv[1:]
    try:
        with open(env_path) as f:
            values = parse_env(f.read())
        with open(template_path) as f:
            rendered = render(f.read(), values)
    except (OSError, ValueError) as exc:
        print(f"render_config: {exc}", file=sys.stderr)
        return 1
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
