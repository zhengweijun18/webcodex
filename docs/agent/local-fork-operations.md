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

## Zero-quota native semantic parity

The fork treats native Codex as a behavior reference, never as a model backend.
The hard invariant is that native Codex model quota budget is zero and Codex
model fallback is forbidden.

The policy file at docs/agent/native-semantic-parity-policy.json assigns every
tracked parity surface one of three routes only:

- webcodex_native: independent WebCodex implementation;
- zero_quota_bridge: direct metadata/MCP app-server RPC backed by separate
  zero-model-turn evidence;
- unavailable: fail closed when neither safe route exists.

There is deliberately no codex_model route. Native thread/process resume,
host-private lifecycle interception, and TUI/CLI presentation parity are
intentional gaps rather than reasons to start a Codex model turn.

Run scripts/check_native_semantic_parity.py for a repository-only policy and
reference check. Add --context-workspace /path/to/context-workspace to validate
the separately maintained zero-quota state and audit the bridge's current RPC
surface.

The bridge audit is allowlist-based. A newly introduced Codex app-server RPC is
rejected until it is explicitly classified; thread/start is accepted only in
ephemeral form. Supplied state must prove Codex model turns are disabled, ACP
coding-agent execution is disabled, read-only/effectful MCP paths report
model_turn_started=false, and recorded usage/rate limits are unchanged.

docs/agent/native-semantic-parity-reference.json is a normalized behavior
fixture, not a fresh live Codex model benchmark. It makes differential checks
deterministic without spending native Codex quota.

To resolve one policy route, run the parity checker with --route followed by a
capability id, for example native_mcp_readonly_call. Without valid zero-quota
bridge evidence, such a route fails closed to unavailable.

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

## Self-maintaining compatibility policy

docs/agent/self-maintenance-policy.json turns the fork capability contract into
an operational policy. Each local runtime unit declares how upstream
satisfaction is detected semantically, which implementation paths are local
fallback, which regression proves equivalence, how redundant implementation is
retired, and which rollback boundary protects adoption.

Generate the current machine-readable capability inventory with
scripts/self_maintenance.py inventory. When the context workspace is available,
pass --context-workspace /path/to/context-workspace and --json.

The inventory combines upstream semantic evidence, local-diff presence, and the
zero-quota native parity report. It classifies surfaces as already_supported,
upstream_can_replace_local, zero_quota_adaptable, intentionally_unavailable, or
needs_human_review. A previous inventory JSON may be supplied through
--baseline to obtain a semantic diff of Codex reference surfaces and local unit
categories.

Run scripts/self_maintenance.py retirement --json to rehearse automatic local
patch retirement. For a unit that upstream now satisfies, the rehearsal creates
a disposable detached worktree, forward-ports the maintenance branch, restores
that unit's implementation paths from upstream, and runs the unit regression.
It reports retirable only when the upstream implementation still satisfies the
policy after the local implementation is removed. The real maintenance branch
is never reset, rebased, or edited by this flow.

Run scripts/self_maintenance.py autopilot with the same context-workspace option
for the default maintenance autopilot. It combines the upstream forward-port
rehearsal, capability inventory, patch-retirement rehearsal, and optional
candidate verification into safe_to_upgrade, needs_adapter, or blocked.
By default it does not fetch, build, install, adopt, restart, publish, or mutate
the Desktop. Supplying --fetch explicitly permits only the remote-tracking-ref
update; Desktop adoption always remains a separate explicitly confirmed
lifecycle action.

## Compatibility watcher

The self-maintenance layer also exposes a one-shot compatibility watcher. It is
designed for an external scheduler such as launchd or another automation layer,
not as a self-respawning resident process. Each invocation observes once,
updates only its maintenance state, and exits.

Initialize or check the current semantic baseline with
scripts/self_maintenance.py watch-check, normally supplying the same
--context-workspace used by the zero-quota parity checks.

The first healthy run records a baseline under the user's WebCodex Desktop
application-support self-maintenance directory. Subsequent runs compare a
stable semantic projection. Volatile evidence such as the reference state's
verified_at timestamp is ignored. Meaningful changes include upstream or
tracked compatibility-branch identity, local capability category, retirement outcome,
native parity route, reference surface, and zero-quota invariants.

When nothing meaningful changed, the result is unchanged and no event is
appended. When something changed, the watcher writes one deduplicated event to
maintenance-events.jsonl and keeps the previous acknowledged baseline until the
event is explicitly acknowledged. Re-running against the same change returns
the same event id rather than appending duplicates.

Inspect state with scripts/self_maintenance.py watch-status --json. After
reviewing a non-blocking event, advance the baseline with
scripts/self_maintenance.py watch-ack --event-id <event-id> --json.

Blocking events cannot be acknowledged into the baseline. In particular, a
zero-quota/parity failure is critical and fail-closed; the drift must be fixed
before a later healthy observation can become the baseline.

watch-check is non-fetching by default. Add --fetch only when remote-tracking
ref mutation is explicitly wanted. Even with --fetch, the watcher never builds,
installs, adopts, restarts, or otherwise mutates the Desktop, and retirement
verification remains isolated in disposable worktrees.

The state file is atomically replaced under an exclusive lock. Event history is
append-only, flushed to disk, and semantically deduplicated. Baseline advancement
is therefore an explicit operator decision rather than a side effect of
observation.

## Observable Codex runtime conformance

The strongest native-compatibility target is 100% conformance across the
declared, observable, independently implementable runtime contract. This is not
a claim that WebCodex is internally identical to Codex.

The machine-readable scope is
docs/agent/observable-codex-runtime-contract.json. Its denominator includes
deterministic runtime behavior such as explicit Session resume across restart,
bounded context recovery, durable Job cancellation state, permission denial and
correlation, timeout/cancellation, bounded retry deadlines, uncertain mutation
recovery, bounded file reads, and zero-model-turn native MCP semantics.

Codex model reasoning/tool selection, OpenAI-private host internals, and native
TUI visual presentation are explicitly outside the denominator. Unknown or
private surfaces never count as supported.

Run scripts/check_observable_conformance.py for the static contract check.
Supplying --context-workspace adds independent zero-quota evidence. Supplying
both --context-workspace and --run-tests executes the dynamic conformance suite.
Only that full-evidence mode is allowed to report
defined_scope_conformance_percent=100. Static evidence alone may report 100%
coverage but deliberately leaves the defined-scope conformance percentage null.

Dynamic scenarios are isolated: each one prints RUN before execution and
PASS/FAIL/TIMEOUT with duration after execution, has its own bounded timeout,
and terminates the whole spawned process group on timeout so leftover Cargo or
compiler children cannot poison the next scenario. Use repeated
--scenario <id> arguments to diagnose only selected scenarios; any subset is
deliberately ineligible for defined-scope 100%. Use --report-file <path> to
persist the final machine-readable scenario report separately from the live
progress stream.

The native_thread_process_resume parity surface is implemented as a WebCodex
native observable equivalent: persisted Workflow Sessions survive runtime
restart, explicit Session ids resume the same workflow, stale/lost context
returns bounded current handoff state, and Job state remains re-observable.
This does not create or invoke a native Codex model thread.

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
self-backups or restart an already-current Desktop. A real version change is a
transaction: candidate verify, quit once, backup, install, verify, relaunch
once, bounded health check, then receipt/history and Last Known Good promotion.

The default health window is 30 seconds. The candidate becomes Last Known Good
only after the installed identity is verified and the relaunched Desktop
process is observed healthy inside that window. If health fails, adoption stops
the failed candidate, restores the pre-transaction backup under the same
lifecycle lock, verifies the restored identity, relaunches the previous Desktop
when it was running before the transaction, and records an
adopt_auto_rollback history event. Use --health-timeout to change the bounded
health window.

--no-relaunch deliberately produces a verified-but-unlaunched adopted state and
does not promote that candidate to Last Known Good. Lifecycle mutations are
serialized by a local file lock, so concurrent install/adopt/rollback/prune
attempts cannot race through replacement or backup steps.

The lifecycle state machine is persisted in desktop-release-state.json beside
the install receipt. It records the installed state, current candidate, Last
Known Good identity, rollback target, failed candidate, and last transaction.
This removes guesswork about which version is merely validated, which was
adopted, which is healthy, and which backup is the recovery target.

An already-running verified Desktop can seed the state machine without any
install or restart by running local_desktop_lifecycle.py promote-current with
--confirm PROMOTE. Promotion is refused unless both Runner and Server are
currently observed. The command records the current identity as Last Known Good
and selects the newest verified different-identity backup as its rollback
target.

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
