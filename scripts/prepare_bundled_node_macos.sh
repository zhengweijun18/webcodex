#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --platform <darwin-x64|darwin-arm64> --output-dir <path>" >&2
  exit 2
}

platform=""
output_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --platform) [ "$#" -ge 2 ] || usage; platform="$2"; shift 2 ;;
    --output-dir) [ "$#" -ge 2 ] || usage; output_dir="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$platform" ] && [ -n "$output_dir" ] || usage

node_version="v24.21.0"
case "$platform" in
  darwin-x64)
    node_arch="x64"
    expected_host="x86_64"
    expected_binary_arch="x86_64"
    expected_sha256="1462cb3b3046b815cf8ea436d3da450ec1a9f11dac7e5a46b0ada5305d7e8097"
    ;;
  darwin-arm64)
    node_arch="arm64"
    expected_host="arm64"
    expected_binary_arch="arm64"
    expected_sha256="bed7eea5325e1108f32ce5228ddd6a5f0f08a499ee42aa7442aea583702f6057"
    ;;
  *) usage ;;
esac

[ "$(uname -s)" = "Darwin" ] || { echo "native macOS host required" >&2; exit 1; }
[ "$(uname -m)" = "$expected_host" ] || {
  echo "native host architecture mismatch: expected=$expected_host actual=$(uname -m)" >&2
  exit 1
}

case "$output_dir" in
  /*) ;;
  *) output_dir="$(pwd)/$output_dir" ;;
esac
mkdir -p "$output_dir"
node_dist="node-$node_version-darwin-$node_arch"
archive="$output_dir/$node_dist.tar.gz"
node_bin="$output_dir/$node_dist/bin/node"

verify_archive() {
  [ -f "$archive" ] || return 1
  [ "$(shasum -a 256 "$archive" | awk '{print $1}')" = "$expected_sha256" ]
}

if ! verify_archive; then
  rm -f "$archive" "$archive.partial"
  curl -fL --retry 2 --connect-timeout 15 --max-time 300     "https://nodejs.org/dist/$node_version/$node_dist.tar.gz"     -o "$archive.partial"
  actual="$(shasum -a 256 "$archive.partial" | awk '{print $1}')"
  [ "$actual" = "$expected_sha256" ] || {
    echo "bundled Node checksum mismatch: expected=$expected_sha256 actual=$actual" >&2
    rm -f "$archive.partial"
    exit 1
  }
  mv "$archive.partial" "$archive"
fi

rm -rf "$output_dir/$node_dist"
tar -xzf "$archive" -C "$output_dir"
[ -x "$node_bin" ] || { echo "bundled Node executable missing: $node_bin" >&2; exit 1; }
[ "$("$node_bin" --version)" = "$node_version" ] || { echo "bundled Node version mismatch" >&2; exit 1; }
[ "$(/usr/bin/lipo -archs "$node_bin")" = "$expected_binary_arch" ] || {
  echo "bundled Node architecture mismatch" >&2
  exit 1
}
printf '%s\n' "$node_bin"
