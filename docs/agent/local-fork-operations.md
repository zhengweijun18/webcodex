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

## Zero-quota native context orchestration

`work_on_project` can optionally bootstrap native Codex context through the
Runner-local Context Bridge without starting a Codex model turn. The default
logical provider id is `codex_context`; override only the logical id with
`WEBCODEX_CODEX_CONTEXT_PROVIDER` when a deployment uses a different name.
Never configure a user-specific absolute bridge path in the Server contract.

### Enhanced Desktop single-install behavior

The maintained enhanced Desktop packages the default Context Bridge as a
Desktop resource instead of requiring a user to copy it into Application
Support or edit `runner.toml` manually. Formal macOS candidates contain:

- `Contents/Resources/webcodex-tools/node/node` — a pinned official Node
  runtime whose archive SHA256 is verified during the build;
- `Contents/Resources/webcodex-tools/codex-context-bridge/` — the vendored
  Context Bridge source from this exact WebCodex commit.

The Desktop passes those two resource locations only to the Desktop-owned
Runner process. Runner config loading treats them as an optional built-in
provider source:

- if the operator already configured provider id `codex_context`, the
  operator configuration wins and the built-in provider is not injected;
- otherwise the Runner injects `codex_context` only when Node, the bridge
  directory, and `server.mjs` are absolute, existing, regular/non-symlink
  resources after canonicalization;
- the effective provider uses the bundled Node and `server.mjs`, an empty
  provider environment, and the same bounded MCP timeout contract as other
  local providers;
- this injection is runtime-only: WebCodex does **not** rewrite the user's
  `runner.toml`;
- missing/corrupt bundled resources degrade Native Context availability
  instead of blocking the Runner or other WebCodex capabilities;
- a local Codex CLI/app-server remains the optional **reference source** for
  native context. The Desktop bundles Node and the Bridge, not the Codex
  model. If the reference CLI is unavailable, the Native Context status is
  unavailable while the rest of WebCodex remains usable.

The Desktop state exposes bundled Node, bundled Bridge, Codex reference, and
effective Native Context readiness. The Workspace status strip surfaces this
as `Native Context`, so an end user can tell whether the optional reference
capability is ready without inspecting filesystem paths or Runner TOML.

The build must run the bundled Bridge `self-check.mjs` with the bundled Node
and require `native_model_turns = 0` before producing a candidate. Build
provenance records both bundled tool versions and hashes.

The startup path is intentionally narrow:

- the bridge must be advertised by the **same Runner** that owns the resolved Project;
- WebCodex lists the provider tools and binds the exact `bootstrap_context` schema/provider instance immediately before calling it;
- a fresh workflow emits bridge phase `startup`; explicit Workflow Session resume emits phase `resume`;
- the current instruction is supplied as the bridge `userPromptSubmit` payload only after Project and Session resolution succeeds;
- the returned startup projection is bounded and strips Codex home, Skill paths, Hook commands, raw stdout/stderr, and other machine-local paths while preserving routing metadata and trusted Hook `additional_context`;
- provider absence, scope denial, schema drift, or timeout degrades the optional context projection instead of blocking the coding task;
- an `outcome_unknown` dispatch remains explicitly uncertain and is never retried automatically.

After startup discovery, models can stay pathless for the two native context reads:

- `native_skill_load(project, name)` resolves the live native catalog on the same Runner. Exact duplicate names fail closed and return bounded opaque `wc_nskill_*` candidates; retrying with `native_skill_id` selects one candidate without ever exposing its source path. Skill bodies are bounded and return only safe metadata, hash, and text.
- `native_knowledge_load(project, key)` accepts only a semantic `reuse-manifest.json.knowledge_paths` key. WebCodex requires the resolved entry to exist inside the Project, reads its declared entry file through the normal Runner file-read path, fences it against the manifest hash when available, and supports `start_line`/`limit` continuation using the same semantic key.

Neither tool broadens authority: Project-read and same-Runner ownership remain required, while the internal Context Bridge dispatch still requires local-MCP authority and exact current provider schema. Neither tool accepts a filesystem path from the model.

This is **workflow-entry orchestration**, not private Host interception. If a
client never invokes `work_on_project`, WebCodex does not claim to observe or
intercept that host prompt lifecycle.

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
operations require exact confirmation words:

```bash
python3 scripts/local_desktop_lifecycle.py install \
  --candidate /path/to/WebCodex\ Desktop.app \
  --confirm INSTALL

python3 scripts/local_desktop_lifecycle.py rollback \
  --backup /path/to/known-good-backup.app \
  --confirm ROLLBACK
```

For an operator-driven **one-shot** switch from a currently running Desktop,
use `adopt` from an independent Terminal shell:

```bash
python3 scripts/local_desktop_lifecycle.py adopt \
  --candidate /path/to/WebCodex\ Desktop.app \
  --confirm ADOPT
```

`adopt` verifies the candidate before requesting Desktop quit. A same-identity
retry is a no-op. A real upgrade quits once, takes a verified backup, installs,
verifies, relaunches once, performs a bounded health check, and promotes the
candidate to Last Known Good only after Runner and Server are observed healthy.
Health failure triggers automatic rollback under the same lifecycle lock.

**Do not wrap Desktop adoption in `launchctl submit`, KeepAlive, a respawning
restart script, or any other self-restarting supervisor.** The supported running
upgrade contract is one invocation of `adopt`; the lifecycle command itself
owns the single quit/install/relaunch transaction.

Each mutation verifies codesign plus bundled runtime identity, serializes
concurrent lifecycle changes with a local lock, records append-only install
history plus the current receipt, and maintains a persisted release state with
Last Known Good and rollback-target identities. `promote-current --confirm
PROMOTE` can seed that state from an already-running healthy Desktop without
installing or restarting it. `prune-backups` previews redundant same-identity
backups; deletion still requires `--confirm PRUNE`.

## Maintenance boundary

The fork should stay small. Prefer deleting compatibility code when upstream
gains the equivalent capability. New local behavior should have:

1. a read-only doctor signal;
2. a deterministic regression;
3. a rollback path;
4. no hidden Codex model turn;
5. no generic destructive external-action passthrough.

Static diff evidence is never enough to delete a maintained compatibility
patch. The capability contract defines behavioral retirement groups. Rehearse
one or all groups in disposable worktrees:

```bash
python3 scripts/check_patch_retirement.py --json
python3 scripts/check_patch_retirement.py \
  --group zero_quota_native_context \
  --json
```

A group is `retirable` only when the current branch passes its verifier, pure
upstream passes the same verifier, and a rebased maintenance branch with that
group's local implementation restored from upstream still passes. The real
maintenance branch is never reset, rebased, edited, installed, or restarted.

The normal doctor keeps this expensive gate off. Use
`python3 scripts/fork_doctor.py --behavioral-retirement` only when evaluating
actual patch retirement; the ordinary `patch-retirement` doctor check is a
cheap static signal and deliberately reports no authoritative
`retirement_ready` verdict.
