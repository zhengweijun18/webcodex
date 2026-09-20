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

## MCP provider lifecycle recovery

The maintained runtime contract is semantic rather than implementation-specific:

- provider lifecycle can be observed without starting provider work;
- a failed/retired connection can recover only from a later explicit action;
- no failed `tools/call` is automatically replayed;
- stale provider identities are never silently retargeted.

On the v0.4.1 maintenance line, `mcp_tool action=status` is passive and a
failure-locked provider can be cleared only with
`action=reset, confirm=true`. Reset does not start the provider and clears
cached schema observations, so a later effectful call requires a fresh
`describe`.

On the latest-upstream line, upstream already provides passive provider status
plus connection retirement: a fatal connection is retired and a later explicit
list/describe may reconnect under the same logical provider identity. The fork
keeps that newer upstream behavior instead of forcing the legacy reset model
onto it.

## Capability contract

`docs/agent/local-fork-capability-contract.json` describes the stable semantic
capabilities that matter to this fork. Verify them without mutation:

```bash
python3 scripts/check_capability_contract.py
```

The main doctor runs this check automatically. This deliberately verifies
capabilities rather than exact implementation shape, allowing an upstream
implementation to replace a local patch when it satisfies the same contract.

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

An install that would actually replace the app, and every rollback, is
intentionally unavailable while WebCodex processes are running. Mutating
operations also require exact confirmation words:

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
local receipt under the Desktop Application Support directory. The compatible
`patched-install-receipt.json` remains the current-state receipt. Successful
mutations are also appended to `patched-install-history.jsonl` before the
current receipt is replaced, with a stable event id so retries do not duplicate
the same event. The history append is flushed to disk, so a later receipt
overwrite cannot erase the earlier upgrade source.

`install` is identity-idempotent: if the candidate already matches the installed
commit/version/build identity it returns `already_installed` before checking the
running-process guard, creates no backup, replaces nothing, and leaves the
existing receipt untouched.

For an operator-driven one-shot switch from a currently running Desktop, prefer
`adopt` from an independent Terminal shell instead of wrapping `install` in
`launchctl submit` or another respawning supervisor:

```bash
python3 scripts/local_desktop_lifecycle.py adopt \
  --candidate /path/to/WebCodex\ Desktop.app \
  --confirm ADOPT
```

`adopt` verifies identity **before** requesting Desktop quit. Repeating the same
command after a successful adoption therefore becomes a no-op and cannot create
self-backups or restart an already-current Desktop. A real version change asks
the app to quit, waits up to 40 seconds without force-killing it, performs the
normal verified install/backup/receipt sequence, and relaunches the app. Use
`--no-relaunch` only when an intentionally stopped post-install state is wanted.
Lifecycle mutations are also serialized by a local file lock, so concurrent
install/adopt/rollback/prune attempts cannot race through replacement or backup
steps.

Preview redundant backups that have the exact same identity as the currently
installed Desktop:

```bash
python3 scripts/local_desktop_lifecycle.py prune-backups
```

Delete those redundant standard self-backups while retaining the newest one:

```bash
python3 scripts/local_desktop_lifecycle.py prune-backups --confirm PRUNE
```

Backups with any other commit/version/build identity, official backups, and
pre-rollback backups are outside this prune set and remain untouched. Increase
`--keep-current` when more than one current-identity self-backup is desired.

## Maintenance boundary

The fork should stay small. Prefer deleting compatibility code when upstream
gains the equivalent capability. New local behavior should have:

1. a read-only doctor signal;
2. a deterministic regression;
3. a rollback path;
4. no hidden Codex model turn;
5. no generic destructive external-action passthrough.
