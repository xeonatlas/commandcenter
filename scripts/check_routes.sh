#!/usr/bin/env bash
# Checks that each label set in a routes file reaches the receiver it names.
# Usage: AMTOOL="<amtool command>" check_routes.sh CONFIG ROUTES_FILE
# AMTOOL may be a binary path or a `docker run ... amtool` prefix (see Makefile).
set -euo pipefail
config=$1 routes=$2
amtool=${AMTOOL:?set AMTOOL to the amtool command}
failures=0 cases=0
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  labels=${line%% => *}
  expected=${line##* => }
  cases=$((cases + 1))
  # shellcheck disable=SC2086  # the command and the labels are deliberately split into words
  actual=$($amtool config routes test --config.file="$config" $labels)
  if [[ "$actual" != "$expected" ]]; then
    echo "route mismatch: {$labels} reached '$actual', expected '$expected'" >&2
    failures=$((failures + 1))
  fi
done < "$routes"
if (( failures > 0 )); then
  exit 1
fi
echo "routes: all $cases cases reach the expected receiver"
