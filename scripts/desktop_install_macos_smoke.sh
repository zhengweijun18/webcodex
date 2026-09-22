#!/usr/bin/env bash
set -euo pipefail

smoke_phase="preflight"
smoke_finished=0
report_smoke_failure() {
  status="$1"
  if [ "$smoke_finished" -ne 1 ] && [ "$status" -ne 0 ]; then
    printf '::error title=macOS Desktop smoke::phase=%s status=%s\n' "$smoke_phase" "$status" >&2
  fi
}

usage() {
  echo "usage: $0 --dmg <path> --version <version> --source-sha <40hex> --built-at <unix> --platform <darwin-x64|darwin-arm64> --stage-metadata <path> --signing-mode <adhoc|developer-id> [--evidence <path>]" >&2
  exit 2
}

dmg=""
version=""
source_sha=""
built_at=""
platform=""
stage_metadata=""
signing_mode=""
evidence=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dmg) [ "$#" -ge 2 ] || usage; dmg="$2"; shift 2 ;;
    --version) [ "$#" -ge 2 ] || usage; version="$2"; shift 2 ;;
    --source-sha) [ "$#" -ge 2 ] || usage; source_sha="$2"; shift 2 ;;
    --built-at) [ "$#" -ge 2 ] || usage; built_at="$2"; shift 2 ;;
    --platform) [ "$#" -ge 2 ] || usage; platform="$2"; shift 2 ;;
    --stage-metadata) [ "$#" -ge 2 ] || usage; stage_metadata="$2"; shift 2 ;;
    --signing-mode) [ "$#" -ge 2 ] || usage; signing_mode="$2"; shift 2 ;;
    --evidence) [ "$#" -ge 2 ] || usage; evidence="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[ -n "$dmg" ] && [ -n "$version" ] && [ -n "$source_sha" ] && [ -n "$built_at" ] \
  && [ -n "$platform" ] && [ -n "$stage_metadata" ] && [ -n "$signing_mode" ] || usage
[[ "$source_sha" =~ ^[0-9A-Fa-f]{40}$ ]] || { echo "invalid source SHA" >&2; exit 1; }
[[ "$built_at" =~ ^[1-9][0-9]*$ ]] || { echo "invalid built_at" >&2; exit 1; }
case "$platform" in
  darwin-x64) expected_host=x86_64; expected_arch=x86_64 ;;
  darwin-arm64) expected_host=arm64; expected_arch=arm64 ;;
  *) echo "unsupported Desktop platform: $platform" >&2; exit 1 ;;
esac
case "$signing_mode" in adhoc|developer-id) ;; *) echo "invalid signing mode" >&2; exit 1 ;; esac
smoke_phase="host-and-input"
[ "$(uname -m)" = "$expected_host" ] || { echo "Desktop smoke requires native $expected_host host" >&2; exit 1; }
[ -f "$dmg" ] && [ ! -L "$dmg" ] || { echo "DMG is missing or not a regular file: $dmg" >&2; exit 1; }
[ -f "$stage_metadata" ] && [ ! -L "$stage_metadata" ] || { echo "stage metadata is missing: $stage_metadata" >&2; exit 1; }

smoke_phase="mount-dmg"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/webcodex-desktop-macos-smoke.XXXXXX")"
mount_point="$temp_root/mount"
mkdir "$mount_point"
attached=0
cleanup() {
  if [ "$attached" -eq 1 ]; then
    hdiutil detach "$mount_point" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf -- "$temp_root"
}
finish_smoke() {
  status=$?
  report_smoke_failure "$status"
  cleanup
  return "$status"
}
trap finish_smoke EXIT
trap 'exit 130' INT TERM

hdiutil attach -readonly -nobrowse -mountpoint "$mount_point" "$dmg" >/dev/null
attached=1
app="$mount_point/WebCodex Desktop.app"
[ -d "$app/Contents" ] || { echo "WebCodex Desktop.app is missing from DMG" >&2; exit 1; }
runtime_dir="$app/Contents/Resources/webcodex-runtime"
[ -d "$runtime_dir" ] || { echo "bundled WebCodex runtime directory is missing" >&2; exit 1; }

smoke_phase="stage-metadata"
python3 - "$stage_metadata" "$version" "$source_sha" "$built_at" "$platform" "$signing_mode" <<'PY'
import json
import re
import sys
from pathlib import Path

path, version, source, built_at, platform, signing_mode = sys.argv[1:]
value = json.loads(Path(path).read_text(encoding="utf-8"))
required = {
    "schema_version",
    "platform",
    "version",
    "source_sha",
    "built_at",
    "signing_mode",
    "resource_dir",
    "tool_resource_dir",
    "provenance",
    "files",
    "bundled_tools",
}
if set(value) != required or value.get("schema_version") != 3:
    raise SystemExit("unexpected Desktop staging metadata schema")
if value.get("version") != version or value.get("source_sha") != source.lower():
    raise SystemExit("Desktop staging metadata release identity mismatch")
if value.get("built_at") != int(built_at) or value.get("platform") != platform or value.get("signing_mode") != signing_mode:
    raise SystemExit("Desktop staging metadata platform/signing identity mismatch")
if value.get("provenance") != "same_unsigned_runtime_input_before_platform_signing":
    raise SystemExit("Desktop staging metadata provenance mismatch")
if value.get("resource_dir") != "resources/webcodex-runtime":
    raise SystemExit("Desktop staging metadata runtime resource path mismatch")
if value.get("tool_resource_dir") != "resources/webcodex-tools":
    raise SystemExit("Desktop staging metadata tool resource path mismatch")
files = value.get("files")
if not isinstance(files, dict) or set(files) != {"webcodex", "webcodex-server", "webcodex-runner"}:
    raise SystemExit("Desktop staging metadata runtime set mismatch")
for name, item in files.items():
    if not isinstance(item, dict) or set(item) != {"filename", "size", "source_sha256", "staged_unsigned_sha256"}:
        raise SystemExit(f"malformed staged runtime metadata: {name}")
    if item["filename"] != name or not isinstance(item["size"], int) or item["size"] <= 0:
        raise SystemExit(f"invalid staged runtime file metadata: {name}")
    for key in ("source_sha256", "staged_unsigned_sha256"):
        if not isinstance(item[key], str) or not re.fullmatch(r"[0-9a-f]{64}", item[key]):
            raise SystemExit(f"invalid staged runtime digest: {name}")
    if item["source_sha256"] != item["staged_unsigned_sha256"]:
        raise SystemExit(f"unsigned source/staged digest mismatch: {name}")
tools = value.get("bundled_tools")
if not isinstance(tools, dict) or set(tools) != {"node", "codex_context_bridge"}:
    raise SystemExit("Desktop staging metadata bundled tool set mismatch")
node = tools["node"]
if (
    not isinstance(node, dict)
    or set(node) != {"version", "sha256", "resource"}
    or node.get("resource") != "webcodex-tools/node/node"
    or not isinstance(node.get("version"), str)
    or not re.fullmatch(r"[0-9a-f]{64}", str(node.get("sha256", "")))
):
    raise SystemExit("malformed bundled Node metadata")
bridge = tools["codex_context_bridge"]
if (
    not isinstance(bridge, dict)
    or set(bridge) != {"version", "resource_dir", "files"}
    or bridge.get("resource_dir") != "webcodex-tools/codex-context-bridge"
    or not isinstance(bridge.get("version"), str)
    or not isinstance(bridge.get("files"), dict)
):
    raise SystemExit("malformed bundled Context Bridge metadata")
required_bridge = {"bridge-lib.mjs", "readiness.mjs", "README.md", "package.json", "server.mjs", "self-check.mjs"}
if set(bridge["files"]) != required_bridge:
    raise SystemExit("bundled Context Bridge metadata file set mismatch")
for name, item in bridge["files"].items():
    if (
        not isinstance(item, dict)
        or set(item) != {"size", "sha256"}
        or not isinstance(item.get("size"), int)
        or item["size"] <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
    ):
        raise SystemExit(f"malformed bundled Context Bridge file metadata: {name}")
PY

smoke_phase="bundled-runtime"
short_source="$(printf '%s' "${source_sha:0:12}" | tr '[:upper:]' '[:lower:]')"
for name in webcodex webcodex-server webcodex-runner; do
  binary="$runtime_dir/$name"
  [ -f "$binary" ] && [ ! -L "$binary" ] && [ -x "$binary" ] || { echo "bundled runtime missing: $name" >&2; exit 1; }
  actual="$("$binary" --version | head -n 1)"
  expected="$name $version (commit $short_source, dirty=false, built_at=$built_at)"
  [ "$actual" = "$expected" ] || { echo "unexpected bundled runtime identity for $name" >&2; exit 1; }
  actual_arch="$(/usr/bin/lipo -archs "$binary")"
  [ "$actual_arch" = "$expected_arch" ] || { echo "unexpected bundled runtime architecture for $name: $actual_arch" >&2; exit 1; }
done

smoke_phase="bundled-tools"
tools_dir="$app/Contents/Resources/webcodex-tools"
bundled_node="$tools_dir/node/node"
bridge_dir="$tools_dir/codex-context-bridge"
[ -f "$bundled_node" ] && [ ! -L "$bundled_node" ] && [ -x "$bundled_node" ] || {
  echo "bundled Node executable is missing" >&2
  exit 1
}
for name in bridge-lib.mjs readiness.mjs README.md package.json server.mjs self-check.mjs; do
  [ -f "$bridge_dir/$name" ] && [ ! -L "$bridge_dir/$name" ] || {
    echo "bundled Context Bridge file is missing: $name" >&2
    exit 1
  }
done

bundled_versions="$(python3 - "$stage_metadata" <<'PY'
import json
import sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(value["bundled_tools"]["node"]["version"])
print(value["bundled_tools"]["codex_context_bridge"]["version"])
PY
)"
bundled_node_version="$(printf '%s\n' "$bundled_versions" | sed -n '1p')"
context_bridge_version="$(printf '%s\n' "$bundled_versions" | sed -n '2p')"
[ "$("$bundled_node" --version)" = "$bundled_node_version" ] || {
  echo "bundled Node version mismatch after DMG install" >&2
  exit 1
}
[ "$(/usr/bin/lipo -archs "$bundled_node")" = "$expected_arch" ] || {
  echo "bundled Node architecture mismatch after DMG install" >&2
  exit 1
}

smoke_phase="context-bridge-self-check"
self_check="$("$bundled_node" "$bridge_dir/self-check.mjs")"
python3 - "$self_check" "$context_bridge_version" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
expected = sys.argv[2]
if payload.get("status") != "pass":
    raise SystemExit(f"bundled Context Bridge self-check failed: {payload}")
if payload.get("bridge_version") != expected:
    raise SystemExit("bundled Context Bridge self-check version mismatch")
if payload.get("native_model_turns") != 0:
    raise SystemExit("bundled Context Bridge self-check started a native model turn")
PY

smoke_phase="context-bridge-readiness"
readiness="$("$bundled_node" "$bridge_dir/readiness.mjs" --json --timeout-ms 3000)"
python3 - "$readiness" "$context_bridge_version" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
expected = sys.argv[2]
if payload.get("status") not in {"ready", "degraded", "unavailable"}:
    raise SystemExit(f"bundled Context Bridge readiness returned invalid status: {payload}")
if payload.get("source_version") != expected:
    raise SystemExit("bundled Context Bridge readiness version mismatch")
if payload.get("native_model_turns") != 0:
    raise SystemExit("bundled Context Bridge readiness started a native model turn")
for key in ("reason", "owner", "impact", "next_action", "observed_at_ms"):
    if payload.get(key) in (None, ""):
        raise SystemExit(f"bundled Context Bridge readiness missing {key}: {payload}")
PY

smoke_phase="codesign"
codesign --verify --deep --strict --verbose=2 "$app"
notarized=false
if [ "$signing_mode" = developer-id ]; then
  spctl --assess --type execute --verbose=2 "$app"
  xcrun stapler validate "$app"
  notarized=true
fi

smoke_phase="evidence"
if [ -n "$evidence" ]; then
  mkdir -p "$(dirname "$evidence")"
  python3 - "$stage_metadata" "$runtime_dir" "$dmg" "$platform" "$signing_mode" "$notarized" "$evidence" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

metadata_path, runtime_dir, dmg_path, platform, signing_mode, notarized, evidence_path = sys.argv[1:]
metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))

def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

runtime = Path(runtime_dir)
files = {}
for name, staged in metadata["files"].items():
    bundled = runtime / name
    files[name] = {
        "unsigned_input_sha256": staged["staged_unsigned_sha256"],
        "bundled_signed_sha256": digest(bundled),
    }
payload = {
    "schema_version": 1,
    "platform": platform,
    "signing_mode": signing_mode,
    "notarized": notarized == "true",
    "dmg_sha256": digest(Path(dmg_path)),
    "runtime": files,
}
Path(evidence_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
fi

smoke_phase="complete"
smoke_finished=1
echo "macOS Desktop DMG smoke passed: $platform signing=$signing_mode notarized=$notarized"
