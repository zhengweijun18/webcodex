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
machine-readable provenance JSON next to the candidate.

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
