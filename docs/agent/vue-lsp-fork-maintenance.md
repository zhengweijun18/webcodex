# Vue SFC LSP local-fork maintenance

This fork adds native `.vue` routing to the Runner LSP registry while keeping
WebCodex's existing read-only semantic-navigation boundary.

## Supported runtime

- WebCodex base: `v0.4.1` / `f080c8f3ea70e37bd9f17fdd0e1b4c3a3aa330f8`
- Patch branch: `vue-lsp-native`
- Latest-main forward-port branch: `vue-lsp-native-main`
- Verified upstream-main base: `da4595d0fad78df6652a837c2dce120276054207`
- Vue language server: `@vue/language-server@2.2.12`
- TypeScript: 5.x
- Optional overrides:
  - `WEBCODEX_VUE_LANGUAGE_SERVER`
  - `WEBCODEX_VUE_TSDK`

Vue Language Server 2.x is intentionally run with `vue.hybridMode=false`.
Current 3.x releases expect an editor-owned `tsserver/request` bridge, which
WebCodex 0.4.1 does not expose.

## Updating from upstream

Remote layout is fixed as:

- `upstream` → `https://github.com/yyjeqhc/webcodex.git`
- `origin` → `https://github.com/zhengweijun18/webcodex.git`

Both maintenance branches use generic names: `vue-lsp-native` and
`vue-lsp-native-main`.

Before rebasing, require a clean worktree. Then:

```bash
git fetch upstream --tags
git switch vue-lsp-native
git rebase <new-upstream-ref>
scripts/check_vue_lsp_patch.sh
```

For example, when a new release tag exists:

```bash
git rebase v0.4.2
```

If the rebase conflicts inside the LSP registry, preserve the upstream
architecture first, then re-apply only these invariants:

1. `.vue` routes to a dedicated Vue language-server kind with language id
   `vue`.
2. Vue initialization receives the current project root so it can resolve
   `node_modules/typescript/lib` without machine-specific source paths.
3. Standalone mode is forced; auto-import cache is disabled.
4. The Vue language-server process is kept network-closed.
5. Existing TypeScript/JavaScript routing and project-primary ordering stay
   unchanged.
6. The default per-project LSP capacity remains at least two so TypeScript and
   Vue servers can coexist in one project; the per-agent bound remains finite.
7. Focused registry, initialization, fake-navigation, status, and real Vue
   protocol tests continue to pass.

Enable Git rerere in the local clone so repeated upstream conflicts can reuse
previous resolutions:

```bash
git config rerere.enabled true
git config rerere.autoupdate true
```

The same patch has been forward-ported from the v0.4.1 layout to the current
upstream layout where the LSP core lives in `crates/webcodex-lsp`; the original
patch cherry-picked cleanly across that extraction. The check script detects
both layouts automatically. On the split layout it runs linked core tests in
`webcodex-lsp` even when the full Runner cannot link locally.

The check script does not fetch, rebase, push, publish, install packages, or
restart Desktop. It always runs formatting plus normal/test-target `cargo
check`. On macOS it skips only Runner-linked Rust tests when the selected SDK
does not contain `AVFAudio.framework` (older Command Line Tools cannot link the
Runner's existing `xcap` dependency); that is an environment limitation, not a
Vue patch failure. When the pinned Vue language server and TypeScript SDK are
available, `scripts/test_vue_lsp_protocol.py` validates real standard-LSP
document symbols, definition, and references without depending on the Runner
test linker. Set `WEBCODEX_RUN_REAL_VUE_LSP=1` to require that real protocol
test instead of allowing it to be skipped when the tools are absent.

## 2026-09-18 dogfood acceptance

The patch was validated through WebCodex itself, not only through the standalone
protocol smoke:

- v0.4.1 patched Runner commit `c4f1d50b` built successfully with the
  `dogfood` profile on the Intel macOS host.
- The latest-upstream forward port at `71f4a4cf` also built the standalone
  `webcodex-lsp` dogfood artifact and a complete patched Runner.
- This host's selected Command Line Tools SDK is macOS 10.15-era and lacks
  `AVFAudio.framework` plus the newer
  `CGPreflightScreenCaptureAccess` / `CGPreflightPostEventAccess` exports.
  Full local Runner linking was verified with a temporary overlay under
  `target/`: an `AVFAudio.tbd` stub plus a copied CoreGraphics TBD containing
  those two runtime-present exports. The overlay is local build scaffolding and
  is not committed or installed into the system SDK.
- The patched v0.4.1 Runner registered against the stock v0.4.1 Desktop/Server
  after confirming zero active Jobs. Server compatibility remained `compatible`.
  The stock Runner was restored after validation and all temporary launchd Jobs,
  configs, and logs were removed.
- A real CDI/HUI Vue 2 file,
  `L3-CDI产品知识库/工程基础依赖包/sources/hui-vue-2.9.1/packages/input/src/input.vue`,
  was exercised through WebCodex's own LSP tools:
  - `lsp_status`: Vue server available via the configured environment override.
  - `document_symbols`: 214 symbols reported.
  - `goto_definition`: the script call `this.handleInput(event)` resolved to
    the `handleInput` method at lines 554-565.
  - `find_references`: the method declaration returned three in-file script
    references including the declaration.
  - Template `@input="handleInput"` to script definition returned no location
    under Vue 2 + Volar 2.2.12. This is an observed language-server semantic
    limitation for that template binding, not a WebCodex routing or process
    failure.
