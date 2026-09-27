#!/usr/bin/env bash
# Fetches promtool, amtool and blackbox_exporter at the versions pinned in
# compose.yml, verified against each release's published sha256sums.txt, into
# .tools/. Used where Docker is not available to this user; the Pi checks with
# the pinned images themselves.
set -euo pipefail
cd "$(dirname "$0")/.."

case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "fetch_tools: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

version_of() { grep -oE "prom/$1:v[0-9.]+" compose.yml | cut -d: -f2 | tr -d v; }

fetch() {  # repo, tarball prefix, image name, binaries...
  local repo=$1 prefix=$2 image=$3; shift 3
  local version tarball base dir
  version=$(version_of "$image")
  tarball="$prefix-$version.linux-$arch.tar.gz"
  base="https://github.com/prometheus/$repo/releases/download/v$version"
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' RETURN
  curl -fsSL -o "$dir/$tarball" "$base/$tarball"
  curl -fsSL -o "$dir/sha256sums.txt" "$base/sha256sums.txt"
  (cd "$dir" && grep " $tarball\$" sha256sums.txt | sha256sum --check --quiet)
  tar -xzf "$dir/$tarball" -C "$dir"
  for binary in "$@"; do
    install -m 0755 "$dir/$prefix-$version.linux-$arch/$binary" ".tools/$binary"
  done
  echo "fetch_tools: $prefix $version verified"
}

mkdir -p .tools
fetch prometheus prometheus prometheus promtool
fetch alertmanager alertmanager alertmanager amtool
fetch blackbox_exporter blackbox_exporter blackbox-exporter blackbox_exporter
