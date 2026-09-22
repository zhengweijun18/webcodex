# WebCodex Enhanced Runtime handoff package

This package is the ready-to-share delivery for the enhanced WebCodex runtime maintained on [`vue-lsp-native-main`](https://github.com/zhengweijun18/webcodex/tree/vue-lsp-native-main).

Its main goal is **not simply “Zero-Quota”**. The package brings together the broader runtime improvements that make ChatGPT + WebCodex behave more like a dependable local development agent. **Zero-Quota Native Context is one of those capabilities.**

## What this enhanced build adds

- **Real local project execution** — ChatGPT can inspect and edit the real repository, use Git, run builds/tests/formatters, and work with the machine's actual developer toolchain.
- **Automatic project context** — root and nested `AGENTS.md`, Skills, Knowledge, Hooks/runtime context, workspace state, and Workflow context can be projected before work starts.
- **Durable Jobs + Workflow recovery** — long builds/tests can outlive one chat turn, while Workflow Sessions retain context and validation continuity across reconnects or runtime restarts.
- **Safer real-world effects** — uncertain pushes, installs, or file mutations are checked against the real state before retry instead of being blindly replayed or silently retargeted.
- **Structured validation** — real terminal state and exit codes are authoritative, preventing successful silent commands from becoming fake unresolved failures.
- **Native Vue / frontend LSP** — Vue SFC navigation, references, diagnostics, symbols, and a pinned Vue/TypeScript toolchain are available through the native LSP surface.
- **One-shot safe Desktop upgrades** — candidate verification, verified backup, exact App-bundle shutdown, one relaunch, Server/Runner health checks, Last Known Good promotion, and automatic rollback.
- **Self-maintaining fork** — upstream compatibility is rehearsed in disposable worktrees, and local patches can retire only after behavioral verification proves upstream has really replaced them.
- **Doctor + reproducible provenance** — source, installed Desktop, running Server/Runner, rollback state, toolchain, Zero-Quota evidence, runtime hashes, codesign, and source SHA can be checked mechanically.
- **Zero-Quota Native Context** — observable Codex Runtime context such as Skills, scoped instructions, Hooks, and Knowledge can be used without starting a Native Codex model turn or silently falling back to a Codex agent.
- **Single-install enhanced runtime** — the macOS Desktop now bundles official Node `v24.21.0` plus Context Bridge `0.5.0`; the Desktop-owned Runner injects the bundled `codex_context` provider when no explicit user provider exists, so normal recipients do not install Node, copy the Bridge, or edit `runner.toml`.

In short, this package is about **project understanding, real execution, durable work, recovery, safer effects, native code intelligence, safe upgrades, and long-term maintainability** — with Zero-Quota as an important part of that larger goal.

## Current validated baseline

- Branch: `vue-lsp-native-main`
- Commit: `6a3f3ea49e9f34f5919ae69d8f33afd7e76f7c15`
- Desktop: `0.4.1`
- Platform: macOS Intel / `darwin-x64`
- Bundled Node: `v24.21.0`
- Context Bridge: `0.5.0`
- Single-install Desktop provider registration: validated
- Native Codex model turns observed by packaged verification: `0`

The packaged verification also covers Native Context behavior, scoped project instructions, Workflow continuity, capability contracts, Desktop lifecycle regression, upstream compatibility, and patch-retirement behavior.

## Package

`WebCodex-Zero-Quota-6a3f3ea4-macOS-Intel.zip`

The filename keeps the historical `Zero-Quota` label for continuity, but the actual package scope is the full **Enhanced Runtime** described above. This release is single-install for ordinary macOS Desktop recipients.

The ZIP contains:

- installable `WebCodex Desktop.app` with bundled Node + Context Bridge
- exact source snapshot for `6a3f3ea4`
- Context Bridge 0.5.0 audit/advanced copy
- machine-readable verification reports
- build provenance and checksums
- the plain-language capability overview

## SHA256

See [`SHA256SUMS.txt`](SHA256SUMS.txt).

The unpacked handoff directory is intentionally not committed separately to Git because that would duplicate the ZIP contents in repository history. The local maintenance checkout keeps both the unpacked directory and the ZIP for future updates.
