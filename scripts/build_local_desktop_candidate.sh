#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ -n "${CARGO:-}" ]; then
  cargo_bin="$CARGO"
elif command -v cargo >/dev/null 2>&1; then
  cargo_bin="$(command -v cargo)"
elif [ -x "$HOME/.cargo/bin/cargo" ]; then
  cargo_bin="$HOME/.cargo/bin/cargo"
else
  echo "cargo not found; set CARGO or install it under PATH/~/.cargo/bin" >&2
  exit 127
fi
export PATH="$(dirname "$cargo_bin"):$PATH"
if [ -z "${CARGO_HTTP_PROXY:-}" ]; then
  if [ -n "${HTTPS_PROXY:-}" ]; then
    export CARGO_HTTP_PROXY="$HTTPS_PROXY"
  elif [ -n "${https_proxy:-}" ]; then
    export CARGO_HTTP_PROXY="$https_proxy"
  fi
fi

reuse_runtime=0
output_root="$root/target/local-fork-desktop"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --reuse-runtime)
      reuse_runtime=1
      shift
      ;;
    --output-dir)
      [ "$#" -ge 2 ] || exit 2
      output_root="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
usage: scripts/build_local_desktop_candidate.sh [--reuse-runtime] [--output-dir PATH]

Build one signed local WebCodex Desktop .app candidate from the current clean
source tree. --reuse-runtime skips rebuilding CLI/Server/Runner only when the
existing target/dogfood binaries exactly match current HEAD.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || { echo "macOS is required" >&2; exit 1; }
[ -z "$(git status --porcelain=v1 --untracked-files=all)" ] || {
  echo "source worktree must be clean" >&2
  exit 1
}

source_sha="$(git rev-parse HEAD)"
short_sha="$(printf '%.12s' "$source_sha")"
version="$(node -p "require('./npm/webcodex/package.json').version")"
built_at="$(git show -s --format=%ct HEAD)"
machine="$(uname -m)"

case "$machine" in
  x86_64) platform="darwin-x64" ;;
  arm64) platform="darwin-arm64" ;;
  *)
    echo "unsupported macOS architecture: $machine" >&2
    exit 1
    ;;
esac

mkdir -p "$output_root"
overlay="$output_root/macos-sdk-overlay/Frameworks"
rm -rf "$output_root/macos-sdk-overlay"
extra_rustflags=""

sdk="$(xcrun --show-sdk-path 2>/dev/null || true)"
if [ "$machine" = "x86_64" ] && [ -n "$sdk" ]; then
  needs_overlay=0
  if [ ! -d "$sdk/System/Library/Frameworks/AVFAudio.framework" ]; then
    needs_overlay=1
  fi
  core_tbd="$sdk/System/Library/Frameworks/CoreGraphics.framework/Versions/A/CoreGraphics.tbd"
  if [ -f "$core_tbd" ] && ! grep -q '_CGPreflightScreenCaptureAccess' "$core_tbd"; then
    needs_overlay=1
  fi
  if [ "$needs_overlay" -eq 1 ]; then
    mkdir -p "$overlay/AVFAudio.framework" "$overlay/CoreGraphics.framework"
    cat > "$overlay/AVFAudio.framework/AVFAudio.tbd" <<'TBD'
--- !tapi-tbd-v3
archs:           [ x86_64 ]
platform:        macosx
install-name:    '/System/Library/Frameworks/AVFAudio.framework/Versions/A/AVFAudio'
current-version: 1
compatibility-version: 1
exports:
  - archs:           [ x86_64 ]
    symbols:         [ ]
...
TBD
    if [ -f "$core_tbd" ]; then
      cp "$core_tbd" "$overlay/CoreGraphics.framework/CoreGraphics.tbd"
      python3 - "$overlay/CoreGraphics.framework/CoreGraphics.tbd" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
missing = [
    symbol
    for symbol in ("_CGPreflightPostEventAccess", "_CGPreflightScreenCaptureAccess")
    if symbol not in text
]
if missing:
    needle = "    symbols:         [ "
    if needle not in text:
        raise SystemExit("CoreGraphics TBD symbols list not found")
    text = text.replace(needle, needle + ", ".join(missing) + ", ", 1)
    path.write_text(text)
PY
    fi
    extra_rustflags="-C link-arg=-F$overlay"
  fi
fi

runtime_identity() {
  name="$1"
  binary="$root/target/dogfood/$name"
  [ -x "$binary" ] || return 1
  actual="$("$binary" --version | head -n 1)"
  expected="$name $version (commit $short_sha, dirty=false, built_at=$built_at)"
  [ "$actual" = "$expected" ]
}

apply_overlay_rustflags() {
  [ -n "$extra_rustflags" ] || return 0
  current="$(printenv RUSTFLAGS 2>/dev/null || true)"
  case " $current " in
    *" $extra_rustflags "*) ;;
    "  ") export RUSTFLAGS="$extra_rustflags" ;;
    *) export RUSTFLAGS="$current $extra_rustflags" ;;
  esac
}

if [ "$reuse_runtime" -eq 1 ]; then
  for name in webcodex webcodex-server webcodex-runner; do
    runtime_identity "$name" || {
      echo "--reuse-runtime requested but target/dogfood/$name does not match HEAD" >&2
      exit 1
    }
  done
else
  apply_overlay_rustflags
  "$cargo_bin" build --locked --profile dogfood \
    -p webcodex-cli --bin webcodex \
    -p webcodex --bin webcodex-server \
    -p webcodex-runner --bin webcodex-runner
fi

npm ci --prefix apps/desktop

if [ "$machine" = "x86_64" ]; then
  if ! node -e "require('./apps/desktop/node_modules/@rolldown/binding-darwin-x64')" >/dev/null 2>&1; then
    binding_version="$(
      node - <<'NODE'
const p = require('./apps/desktop/node_modules/rolldown/package.json');
const v = (p.optionalDependencies || {})['@rolldown/binding-darwin-x64'];
if (!v) process.exit(2);
process.stdout.write(v);
NODE
    )"
    npm install --prefix apps/desktop \
      --no-save --package-lock=false --no-audit --no-fund \
      "@rolldown/binding-darwin-x64@$binding_version"
  fi
fi

npm run build --prefix apps/desktop

stage="$output_root/stage"
rm -rf "$stage"
python3 scripts/prepare_desktop_bundle_macos.py \
  --bin-dir target/dogfood \
  --version "$version" \
  --source-sha "$source_sha" \
  --built-at "$built_at" \
  --platform "$platform" \
  --signing-mode adhoc \
  --output-dir "$stage"

tauri_target="$output_root/tauri-target"
rm -rf "$tauri_target"
export CARGO_TARGET_DIR="$tauri_target"
export APPLE_SIGNING_IDENTITY="-"

apply_overlay_rustflags

(
  cd apps/desktop
  npm exec tauri -- build --bundles app \
    --config "$stage/tauri.bundle.conf.json" \
    --ci -- --locked
)

app="$tauri_target/release/bundle/macos/WebCodex Desktop.app"
[ -d "$app" ] || {
  echo "Desktop app was not produced: $app" >&2
  exit 1
}
codesign --verify --deep --strict --verbose=1 "$app"

runtime_dir="$app/Contents/Resources/webcodex-runtime"
for name in webcodex webcodex-server webcodex-runner; do
  actual="$("$runtime_dir/$name" --version | head -n 1)"
  expected="$name $version (commit $short_sha, dirty=false, built_at=$built_at)"
  [ "$actual" = "$expected" ] || {
    echo "bundled runtime mismatch for $name: $actual" >&2
    exit 1
  }
done

rustc_version="$(rustc --version)"
cargo_version="$("$cargo_bin" --version)"
node_version="$(node --version)"
npm_version="$(npm --version)"
sdk_path="$(xcrun --show-sdk-path 2>/dev/null || true)"
sdk_version="$(xcrun --show-sdk-version 2>/dev/null || true)"
vue_tool_root="$HOME/Library/Application Support/dev.webcodex.desktop/local-tools/vue-lsp-2.2.12"
vue_version=""
typescript_version=""
if [ -f "$vue_tool_root/node_modules/@vue/language-server/package.json" ]; then
  vue_version="$(node -p "require('$vue_tool_root/node_modules/@vue/language-server/package.json').version")"
fi
if [ -f "$vue_tool_root/node_modules/typescript/package.json" ]; then
  typescript_version="$(node -p "require('$vue_tool_root/node_modules/typescript/package.json').version")"
fi

provenance="$output_root/build-provenance.json"
python3 - "$provenance" "$app" "$source_sha" "$version" "$built_at" "$platform" "$stage/desktop-bundle.json" "$extra_rustflags" "$rustc_version" "$cargo_version" "$node_version" "$npm_version" "$sdk_path" "$sdk_version" "$vue_version" "$typescript_version" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

(
    out, app, source, version, built_at, platform, stage_metadata, rustflags,
    rustc_version, cargo_version, node_version, npm_version, sdk_path,
    sdk_version, vue_version, typescript_version,
) = sys.argv[1:]

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

runtime = Path(app) / "Contents/Resources/webcodex-runtime"
payload = {
    "schema_version": 1,
    "source_sha": source,
    "version": version,
    "built_at": int(built_at),
    "platform": platform,
    "app": app,
    "codesign": "verified",
    "stage_metadata": stage_metadata,
    "rustflags_overlay": rustflags,
    "toolchain": {
        "rustc": rustc_version,
        "cargo": cargo_version,
        "node": node_version,
        "npm": npm_version,
        "sdk_path": sdk_path,
        "sdk_version": sdk_version,
        "vue_language_server": vue_version or None,
        "typescript": typescript_version or None,
    },
    "runtime_sha256": {
        name: sha256(runtime / name)
        for name in ("webcodex", "webcodex-server", "webcodex-runner")
    },
}
Path(out).write_text(json.dumps(payload, indent=2) + "\n")
PY

echo "local Desktop candidate passed"
echo "app=$app"
echo "provenance=$provenance"
