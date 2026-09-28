#!/usr/bin/env bash
# Ships this repo to the command-center Pi and applies it there.
# COMMAND_CENTER_HOST overrides the ssh host (default: command-center).
set -euo pipefail
cd "$(dirname "$0")/.."
host=${COMMAND_CENTER_HOST:-command-center}
remote_shell=/usr/bin/ssh   # plain `ssh` is kitty's kitten on the workstation

make check
# .env and run/ are excluded, which also protects the Pi's copies from --delete.
rsync -az --delete -e "$remote_shell" \
  --exclude=.git/ --exclude=.venv/ --exclude=run/ --exclude=.env \
  --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.superpowers/ --exclude=.tools/ \
  ./ "$host:/opt/command-center/"
"$remote_shell" "$host" /opt/command-center/scripts/apply.sh
