# Local fork operations

This fork is maintained as a small, generic compatibility layer on top of
upstream WebCodex. The operating rule is: **read-only checks by default;
explicit confirmation for local Desktop replacement or rollback; no automatic
publishing.**

## Health check

Run the fast read-only doctor:

```bash
python3 scripts/fork_doctor.py
```

Include the full Vue LSP regression:

```bash
python3 scripts/fork_doctor.py --deep
```

When a separate context-bridge workspace is available, include its self-check
without hard-coding that workspace into this repository:

```bash
python3 scripts/fork_doctor.py --context-workspace /path/to/context-workspace
```

The doctor checks the Git topology, maintenance branches, installed Desktop
identity, rollback backup, persisted Vue LSP toolchain, suspicious Git proxy
configuration, upstream drift, and optional deep/bridge regressions. It never
mutates repository or external state.

## Bootstrap the pinned Vue LSP toolchain

Read current toolchain status:

```bash
python3 scripts/vue_lsp_toolchain.py status
```

Install or repair the pinned toolchain only with an explicit confirmation:

```bash
python3 scripts/vue_lsp_toolchain.py install --confirm INSTALL
```

An optional `--proxy` can be supplied for environments that require an HTTP
proxy. The installer stages exact Vue/TypeScript versions, rewrites GitHub SSH
package URLs to HTTPS for the install process only, creates an absolute-Node
wrapper, persists the Desktop environment through a user LaunchAgent, and
verifies the resulting state.

## Stable local MCP provider environment

Runner-owned MCP providers run with a cleared child environment. Use the
provider-local `env` map for stable, non-secret machine values that must survive
Desktop launches and restarts, for example an absolute `PATH`, npm cache path,
or a local HTTP proxy:

```toml
[[mcp.providers]]
id = "example"
# ... executable / args / cwd ...
env = { PATH = "/usr/local/bin:/usr/bin:/bin", HTTP_PROXY = "http://proxy.example:8080" }
```

Use `env_from_env` only when the value must be supplied by the Runner process at
spawn time, especially for externally managed credentials. Static `env` and
`env_from_env` may not target the same child key, WebCodex-sensitive transport
keys are rejected, and neither map is advertised in Runner provider inventory.
The provider child still receives an `env_clear()` environment; this feature
adds explicit configuration, not implicit host inheritance.

## Rehearse an upstream update

Fetch upstream separately, then rehearse the forward-port in a disposable
worktree:

```bash
git fetch upstream main --tags
python3 scripts/check_upstream_compat.py --run-checks
```

The real `vue-lsp-native-main` branch is not rewritten. The rehearsal rebases
a detached copy onto the requested upstream ref, runs the compatibility checks,
reports conflicts or failures, and deletes the disposable worktree.

If upstream eventually contains native Vue SFC support, the rehearsal reports
`upstream_native_vue=true`. Treat that as a trigger to evaluate deleting this
compatibility patch instead of carrying redundant code.

## Build a local Desktop candidate

Build a signed `.app` directly, without depending on DMG packaging:

```bash
scripts/build_local_desktop_candidate.sh
```

When the three `target/dogfood` runtimes already exactly match current HEAD,
reuse them to shorten a rebuild:

```bash
scripts/build_local_desktop_candidate.sh --reuse-runtime
```

The builder keeps macOS SDK compatibility shims under `target/`, repairs the
known Rolldown optional x64 binding when npm omits it, builds the Tauri `app`
bundle, verifies codesign and all bundled runtime identities, and writes a
machine-readable provenance JSON next to the candidate. Provenance includes
runtime hashes plus Rust, Node/npm, macOS SDK, Vue language-server, and
TypeScript toolchain versions.

## Desktop lifecycle

Read installed/backup state:

```bash
python3 scripts/local_desktop_lifecycle.py status
```

Verify a candidate without installing it:

```bash
python3 scripts/local_desktop_lifecycle.py verify --candidate /path/to/WebCodex\ Desktop.app
```

Install and rollback are intentionally unavailable while WebCodex processes are
running and require exact confirmation words:

```bash
python3 scripts/local_desktop_lifecycle.py install \
  --candidate /path/to/WebCodex\ Desktop.app \
  --confirm INSTALL

python3 scripts/local_desktop_lifecycle.py rollback \
  --backup /path/to/known-good-backup.app \
  --confirm ROLLBACK
```

Each mutation verifies codesign plus bundled runtime identity, takes a safety
backup, uses a same-filesystem replacement, verifies the result, and writes a
local receipt under the Desktop Application Support directory.

## Maintenance boundary

The fork should stay small. Prefer deleting compatibility code when upstream
gains the equivalent capability. New local behavior should have:

1. a read-only doctor signal;
2. a deterministic regression;
3. a rollback path;
4. no hidden Codex model turn;
5. no generic destructive external-action passthrough.
