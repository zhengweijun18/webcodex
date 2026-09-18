#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

cargo_bin="${CARGO:-cargo}"

"$cargo_bin" fmt --all -- --check
"$cargo_bin" check --locked -p webcodex-runner
"$cargo_bin" check --locked -p webcodex-runner --tests

run_linked_tests=1
if [[ "$(uname -s)" == "Darwin" ]]; then
  sdk_root="$(xcrun --show-sdk-path 2>/dev/null || true)"
  if [[ -z "$sdk_root" || ! -d "$sdk_root/System/Library/Frameworks/AVFAudio.framework" ]]; then
    run_linked_tests=0
    echo "SKIP linked Rust tests: selected macOS SDK lacks AVFAudio.framework" >&2
  fi
fi

if [[ "$run_linked_tests" == "1" ]]; then
  "$cargo_bin" test --locked -p webcodex-runner --bin webcodex-runner language::tests
  "$cargo_bin" test --locked -p webcodex-runner --bin webcodex-runner vue_
  "$cargo_bin" test --locked -p webcodex-runner --bin webcodex-runner \
    workspace_symbol_timeout_uses_operation_budget_only_for_rust
  "$cargo_bin" test --locked -p webcodex-runner --bin webcodex-runner \
    lsp_status_reports_every_registered_language_server
  "$cargo_bin" test --locked -p webcodex-runner --bin webcodex-runner \
    unsupported_extension_is_rejected_with_supported_list
fi

local_server="$repo_root/.webcodex-dev-tools/vue-lsp/node_modules/.bin/vue-language-server"
local_tsdk="$repo_root/.webcodex-dev-tools/vue-lsp/node_modules/typescript/lib"
server="${WEBCODEX_VUE_LANGUAGE_SERVER:-$local_server}"
tsdk="${WEBCODEX_VUE_TSDK:-$local_tsdk}"

if [[ "${WEBCODEX_RUN_REAL_VUE_LSP:-0}" == "1" || ( -x "$server" && -d "$tsdk" ) ]]; then
  python3 scripts/test_vue_lsp_protocol.py --server "$server" --tsdk "$tsdk"
else
  echo "SKIP real Vue LSP protocol test: install pinned Vue LS/TypeScript or set overrides" >&2
fi
