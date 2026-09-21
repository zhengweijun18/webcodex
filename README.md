# WebCodex

[English](README.md) | [简体中文](README.zh-CN.md)

**WebCodex lets ChatGPT, Claude, and other AI agents work directly with code and developer tools on your own machines.**

Ask your assistant to inspect a repository, modify code, run tests, use Git, or investigate a failure. Your repository stays on the machine where it already lives; you do not need to move the project into a hosted workspace just to use an AI coding agent.

## This fork: Zero-Quota Native-like Runtime

> **What this fork is for:** make **ChatGPT + WebCodex** behave more like a reliable local coding agent — with project awareness, durable jobs, recoverable workflows, safer real-world effects, native Vue LSP, self-maintaining fork logic, and safe Desktop upgrades — while keeping **Native Codex model usage at zero**.

Enhanced implementation branch: [`vue-lsp-native-main`](https://github.com/zhengweijun18/webcodex/tree/vue-lsp-native-main)
Current validated baseline: `3082590b87de6f023d775c98f8a082df6b4168c4`

### 14 core capabilities

1. **ChatGPT works on the real local project** — read/search/edit files, inspect Git/diffs, run tests/builds/formatters, use the real local toolchain, and finish work instead of only suggesting code.
2. **Automatic project understanding** — before work starts, WebCodex can project root and nested `AGENTS.md`, available Skills, Knowledge, Hooks/runtime context, workspace state, and Workflow context so the model does not need the project explained from scratch every time.
3. **Zero-Quota Codex Runtime Context** — observable Codex Runtime context can be used as a reference without starting a Native Codex model turn and without silently falling back to a Codex agent.
4. **Pathless Skill / Knowledge contracts** — the model works with Skill names, opaque ids, and semantic Knowledge keys instead of depending on developer-machine absolute paths; duplicate/ambiguous Skills fail closed.
5. **Durable long-running Jobs** — builds, tests, and other long-running work are not tied to one chat turn; the Job can continue running and remain observable after the conversational turn ends.
6. **Recoverable Workflow Sessions** — workflow state, validation evidence, and Native Context continuity can survive reconnects/restarts so work can continue from the same real execution state instead of being guessed from scratch.
7. **No blind replay of real-world effects** — if a push/install/file mutation may already have happened but the response was lost, WebCodex marks the outcome uncertain and checks the real state before retrying; it does not silently replay or retarget another Runner.
8. **More trustworthy validation** — process completion + exit code are authoritative, so successful silent commands such as `cargo fmt --check` are not left behind as fake unresolved failures.
9. **Real Vue / frontend LSP** — Vue SFC definition navigation, references, diagnostics, symbols, and a pinned Vue Language Server / TypeScript toolchain are available through the native LSP surface instead of relying only on text search.
10. **One-shot safe Desktop upgrades** — candidate verification, verified backup, exact App-bundle shutdown, one relaunch, Server/Runner health checks, Last Known Good promotion, and automatic rollback if the candidate is unhealthy.
11. **Automatic patch-retirement decisions** — local fork patches are not kept forever by habit; WebCodex checks whether upstream truly provides the same behavior before a local capability is allowed to retire.
12. **Disposable upstream upgrade rehearsal** — upstream changes are rehearsed in temporary worktrees first, so merge/rebase compatibility and capability behavior can be tested without rewriting the real maintenance branch.
13. **Built-in Doctor health check** — source, installed Desktop, running Server/Runner, rollback backup, Vue toolchain, Zero-Quota evidence, Context Bridge, upstream state, and deployment alignment can be checked mechanically.
14. **Reproducible Desktop provenance** — every formal Desktop candidate can be traced to the exact source commit, build time, toolchain, runtime binary hashes, codesign result, and dirty/clean state.

### What using it feels like

```text
You give ChatGPT a task
        ↓
WebCodex opens the real local project
        ↓
Project rules / AGENTS / Skills / Knowledge / current workflow state are loaded
        ↓
The model inspects code + LSP information
        ↓
It edits real files and runs the real local toolchain
        ↓
Long builds/tests continue as durable Jobs
        ↓
Failures are fixed from real evidence, not guessed
        ↓
Validation passes → review / commit / handoff
```

The practical difference is that you do **not** need to repeatedly paste the project rules, directory structure, toolchain instructions, available Skills, test commands, or "where we left off" into every new turn.

### Compared with ordinary WebCodex

| Added on `vue-lsp-native-main` | What you actually gain |
|---|---|
| Zero-Quota Native Context | Reuse observable Codex Runtime context without consuming Native Codex model quota |
| Automatic project context | The agent starts with project rules, scoped AGENTS, Skills, Knowledge, and current workflow state |
| Pathless Skills / Knowledge | The model does not need developer-machine absolute filesystem paths |
| Durable Jobs | Long builds/tests do not disappear when one chat turn ends |
| Workflow recovery | Reconnect/restart can continue from the real prior execution state |
| Safer effect handling | Uncertain pushes/installs/mutations are checked before retry instead of blindly replayed |
| Structured validation | Silent successful commands are recorded as success, not historical fake failures |
| Native Vue LSP | IDE-like Vue navigation/diagnostics instead of text search alone |
| Safe Desktop adoption | Verified one-shot upgrade with backup, health check, LKG, and automatic rollback |
| Self-maintaining fork | Local patches can retire once upstream proves equivalent behavior |
| Doctor | One command checks source/runtime/deployment/toolchain/rollback/Zero-Quota health |
| Reproducible provenance | A Desktop `.app` can be traced back to an exact source SHA and toolchain |

### What is already validated

- **Context Bridge black-box:** PASS
- **Native Context runtime:** 8/8 PASS
- **Scoped Project Instructions:** PASS
- **Workflow Native Context continuity:** PASS
- **Required capability contract:** 12/12 PASS
- **Desktop lifecycle regression:** 17/17 PASS
- **Upstream compatibility rehearsal:** PASS
- **Observed Native Codex model turns:** **0**
- **Source HEAD = installed Desktop = running Server/Runner:** aligned on `3082590b87de`

### Important boundary: what this does *not* claim

This fork does **not** claim to copy OpenAI's private Codex Host, Codex model reasoning/planning, model-side tool-selection intelligence, hidden/private internal state, or the Native Codex TUI. The target is **observable, testable runtime behavior**, not pretending the private model/Host internals were cloned.

### Handoff package

- [Download `WebCodex-Zero-Quota-3082590b-macOS-Intel.zip`](deliverables/WebCodex-Zero-Quota-3082590b-macOS-Intel.zip)
- [Package notes + SHA256](deliverables/README.md)

**In one sentence:** this fork makes ChatGPT + WebCodex a dependable local development agent that **understands the project, can really execute work, survives long tasks and reconnects, avoids unsafe blind retries, upgrades itself safely, and remains maintainable as upstream evolves — without using Native Codex model quota.**

## Start using WebCodex

### Everyday development: full WebCodex (recommended)

If you want ChatGPT to keep using your real development environment, start with a **regular Server + Runner**. This is the full development experience: durable access to multiple projects plus project exploration, editing, Git, commands, tests, long-running work, and code navigation. Public HTTPS, Cloudflare Tunnel, and OpenAI Secure MCP Tunnel are only ways for ChatGPT to reach the Server; they do not switch you into a different restricted experience.

For Windows or macOS, the recommended first path is **WebCodex Desktop + the official OpenAI Secure Tunnel**. Follow the [Desktop installation guide](docs/desktop-install.md). For CLI, an existing Server, self-hosting, or advanced setup, use the [Full Setup guide](docs/PERSONAL_SETUP.md).

### Just trying it for a few minutes: temporary share

To quickly see whether WebCodex fits your workflow, run this inside one repository:

```bash
cd /path/to/your/repository
npx --yes @yyjeqhc/webcodex share
```

`share` starts a temporary, single-project instance of the ordinary WebCodex Adaptive Runtime and prints the ChatGPT connection values. Its temporary Project Credential limits access to that ProjectGrant; the endpoint and credential stop working when the command exits. It is intended for trials and short-lived sharing, not as the default full daily setup. See the [Quick Trial](docs/QUICK_START.md) for the exact steps.

## What can it do?

- **Understand and edit code** — read, search, inspect, and make guarded changes inside configured projects.
- **Use the real toolchain** — run commands, tests, formatters, compilers, and project-specific tooling on the machine that owns the repository.
- **Work with Git** — inspect status and diffs while keeping repository operations visible and reviewable.
- **Handle long-running work** — keep jobs observable instead of requiring one model turn to stay open indefinitely.
- **Support human review** — use the [Runtime Console](docs/runtime-console.md), Workflow Session evidence, Jobs, and Git/diff review without a separate task/result acceptance subsystem.

## Why WebCodex?

- **Your code stays on your machine.** The repository does not need to be copied into the chat service.
- **The agent gets a real development environment.** It can use the same files, Git checkout, compilers, tests, and tools you already use.
- **Work survives beyond a single request.** Long-running execution and evidence remain observable through WebCodex.
- **Start temporary or run it long-term.** Use one-command sharing for a quick session, or connect machines to a self-hosted Server for a durable setup.

## How it works

```text
AI client
   |
   | MCP / HTTPS
   v
WebCodex
   |
   v
your machine
   |
   +-- repository
   +-- Git
   +-- compilers / tests / developer tools
```

For the internal Server/Runner architecture, protocol surfaces, and authority boundaries, see [Architecture](docs/ARCHITECTURE.md), [MCP](docs/MCP.md), and [Authentication](docs/AUTH_MODEL.md).

## Star History

[![Star History Chart](https://api.star-history.com/image?repos=yyjeqhc/webcodex&type=Date)](https://www.star-history.com/yyjeqhc/webcodex)

## Platforms

- **Linux x64/arm64** — local `share`, Server, and Runner workflows.
- **macOS x64/arm64** — Desktop local Server + Runner, OpenAI Secure Tunnel, local `share`, and standalone Runner workflows.
- **Windows x64** — Desktop local Server + Runner with the official OpenAI Secure Tunnel, plus CLI + Runner, local foreground Server, and explicit `webcodex share --tunnel cloudflare|openai|none`.
- **Windows arm64** — CLI + Runner, local foreground Server, and `share`; managed OpenAI `tunnel-client` is supported. The pinned Cloudflare release has no official Windows ARM64 artifact, so Cloudflare requires a trusted explicit/PATH `cloudflared`. The Desktop installer is currently Windows x64 only. WebCodex-managed Windows Server services remain unsupported outside Desktop's owned foreground runtime.

Windows and long-lived deployments are covered in [Deployment](docs/DEPLOYMENT.md) and [MCP](docs/MCP.md).

## Existing Servers and advanced setup

If someone already provides the WebCodex Server and connection credential, use that existing Server and follow the [Full Setup guide](docs/PERSONAL_SETUP.md). For a normal Windows/macOS personal installation, use the [Desktop guide](docs/desktop-install.md). Use [Deployment](docs/DEPLOYMENT.md) only for production hosting, multiple users, systemd/Docker, OAuth, proxies, and private CAs.

Those are follow-up operating concerns, not concepts a first-time user should have to learn before WebCodex works.

## Documentation

- [Desktop installation](docs/desktop-install.md) — recommended Windows/macOS path: Desktop + official OpenAI Secure Tunnel
- [Using Desktop](docs/desktop-guide.md) — projects, connections, activity, and background operation
- [Full Setup](docs/PERSONAL_SETUP.md) — CLI, existing Server, Linux, and advanced regular Server + Runner setup
- [Quick Trial](docs/QUICK_START.md) — temporarily try one repository with `share`
- [MCP](docs/MCP.md) — ChatGPT, Claude, authentication choices, and MCP reference
- [Deployment](docs/DEPLOYMENT.md) — production, self-hosting, and advanced operations
- [Troubleshooting](docs/TROUBLESHOOTING.md) — connection and runtime problems
- [CLI](docs/CLI.md) — command and credential reference
- [AI-assisted setup](docs/AI_ONBOARDING.md) — have an AI agent help configure WebCodex
- [Security](SECURITY.md) — security model and operational guidance
- [Documentation index](docs/INDEX.md) — all user and contributor documentation

## Security

WebCodex can read and modify files and execute commands inside configured project boundaries. Use version control, keep credentials out of prompts/logs/Git, and register only project roots the assistant should access. Read [SECURITY.md](SECURITY.md) for the complete model.

## Build from source

```bash
cargo build --release --workspace --bins
export PATH="$PWD/target/release:$PATH"
```

## Contributing

Contributions are welcome, including contributions created with WebCodex itself or other coding agents. For bug reports, development workflow, and pull request guidance, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgements

Thanks to the [LINUX DO](https://linux.do/) community for its welcoming space for technical discussion and support for open-source sharing.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
