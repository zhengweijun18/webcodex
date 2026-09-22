# WebCodex

[English](README.md) | [简体中文](README.zh-CN.md)

**WebCodex 让 ChatGPT、Claude 和其他 AI Agent 直接使用你自己机器上的代码仓库和开发工具。**

你可以直接让 AI 理解项目、修改代码、运行测试、检查 Git 或排查问题。仓库仍然留在原来的机器上，不需要为了使用 WebCodex 把整个项目搬到托管环境里。

## 这个 Fork 的核心增强：Zero-Quota 原生 Codex 逼近

> **一句话先说清楚：**这套 Fork 是为了让 **ChatGPT + WebCodex 更像一个可靠的本地开发 Agent**——它能自动理解项目、真正操作本机代码、跑长任务、断线后继续、安全处理真实副作用、使用 Vue LSP、自己判断补丁何时可以退休，还能安全升级 Desktop；同时 **Native Codex 模型调用保持为 0**。

增强实现分支：[`vue-lsp-native-main`](https://github.com/zhengweijun18/webcodex/tree/vue-lsp-native-main)
当前已验证跨平台基线：`acc84e9f87a18af852f9076e63b72e10c8bb6c0e`

### 直接下载安装

普通用户无需克隆源码，直接从
[GitHub Releases](https://github.com/zhengweijun18/webcodex/releases/latest)
下载增强版 Desktop，退出原 WebCodex Desktop 后覆盖安装即可：

- macOS Apple Silicon：`darwin-arm64.dmg`
- macOS Intel：`darwin-x64.dmg`
- Windows x64：`win32-x64-setup.exe`
- Windows ARM64：`win32-arm64-setup.exe`

安装包已经内置增强运行时所需的 Node 与 Context Bridge，原有 WebCodex 用户配置继续保留。

### 15 个核心功能点

1. **ChatGPT 直接操作本机真实项目**：读取、搜索、修改代码，看 Git/Diff，运行编译、测试、格式化和项目自己的工具链，不只是给建议，而是可以真正把任务做完。
2. **自动理解项目规则、AGENTS、Skill、Knowledge**：AI 开工前自动获得根目录和子目录 `AGENTS.md`、可用 Skill、Knowledge、Hook/Runtime Context、当前工作区和 Workflow 状态，不需要每次从头解释“这个项目怎么玩”。
3. **Zero-Quota 读取 Codex Runtime Context**：可以把 Codex 当作 Runtime 参考，读取可观察的上下文能力，但不会启动 Native Codex model turn，也不会在 WebCodex 做不了时偷偷 fallback 到 Codex Agent。
4. **Skill / Knowledge 不暴露真实本机路径**：模型通过 Skill 名称、opaque id、semantic key 工作，不需要知道 `/Users/xxx/...` 这类开发机绝对路径；遇到同名/歧义 Skill 会停下来，不会偷偷乱选。
5. **长任务 Job 不跟聊天一起丢**：build、test 等长任务和聊天 Session 分开；一次对话结束后 Job 还可以继续跑、继续观察，不需要因为模型 turn 结束而重启任务。
6. **Workflow Session 可恢复**：断线、重连、Runner/Runtime 重启后，可以恢复当前 Workflow、验证证据和 Native Context 连续性，而不是重新开一个会话猜“之前做到哪了”。
7. **有副作用的操作不盲目重试**：push、安装、文件修改等操作如果已经发出但回执丢了，会先标记结果不确定、检查真实现场，再决定是否重试；不会因为“没收到返回”就直接执行第二遍，也不会静默换 Runner。
8. **测试 / 验证结果更可靠**：以真实命令终态和 exit code 判断成功/失败，避免 `cargo fmt --check` 这种“成功但没有 stdout”的命令被记成历史假失败。
9. **Vue / 前端真实 LSP 能力**：支持 Vue SFC 的定义跳转、引用、诊断、符号等 IDE 级能力，并固定 Vue Language Server / TypeScript 工具链，不只靠 grep 文本理解代码。
10. **Desktop one-shot 安全升级、失败自动回滚**：新版本先验证，再备份旧版本，精确退出 App 自身进程，只启动一次，检查 Server/Runner 健康；成功后设为 Last Known Good，失败则自动恢复旧版本。
11. **Fork 自动判断本地补丁什么时候可以退休**：不再因为“这是我的 Fork 补丁”就永久保留；只有 upstream 自己能过行为验证，并且拿掉本地实现后仍然 PASS，才允许删除这块本地 patch。
12. **Upstream 升级先在临时 worktree 演习**：先在 disposable worktree 里测试 rebase/merge、编译和能力行为，不直接改真实维护分支，避免一次上游升级把正在用的分支弄乱。
13. **自带 Doctor 健康检查**：可以机器化检查源码、已安装 Desktop、正在运行的 Server/Runner、rollback backup、Vue 工具链、Zero-Quota evidence、Context Bridge、upstream 状态和 deployment alignment。
14. **Desktop 构建可追溯到精确源码和工具链**：每个正式候选都能回答“它到底由哪个 commit 编出来、是不是 dirty build、Rust/Node/Vue 工具链是什么、三个 runtime binary 是不是同一版本、SHA256 是什么”。
15. **增强 Runtime 变成跨平台 Single-Install**：macOS Intel / Apple Silicon、Windows x64 / ARM64 Desktop 都内置固定版官方 Node 与 Context Bridge；Desktop-owned Runner 在用户没有显式同名配置时自动注册 bundled `codex_context`，普通接收方只装 Desktop，不再手工装 Node、复制 Bridge 或修改 `runner.toml`。

### 你实际使用时，大概是什么体验

```text
你给 ChatGPT 一个任务
        ↓
WebCodex 打开你本机真实项目
        ↓
自动读取项目规则 / AGENTS / Skill / Knowledge / 当前现场
        ↓
AI 分析代码和 LSP 信息
        ↓
修改真实文件、运行真实本机工具链
        ↓
build / test 等长任务交给 Job 持续运行
        ↓
失败就根据真实证据继续修
        ↓
验证通过 → review / commit / 交付
```

实际最大的变化是：你不需要每次重新把项目规范、目录结构、可用 Skill / Knowledge、编译器和测试方式、当前 Git / 工作区状态、上一次任务做到哪里，再复制给 AI。

### 相比普通 WebCodex，多了什么

| `vue-lsp-native-main` 增强能力 | 你实际感受到的变化 |
|---|---|
| Zero-Quota Native Context | 借鉴可观察的 Codex Runtime Context，但不消耗 Native Codex 模型额度 |
| 自动项目上下文 | AI 开工前自动知道项目规则、局部 AGENTS、Skill、Knowledge 和当前 Workflow |
| Pathless Skill / Knowledge | 模型不需要依赖开发机绝对文件路径 |
| Durable Job | 长时间 build/test 不会因为一次聊天结束就消失 |
| Workflow 恢复 | 断线、重启后能从真实现场继续，不需要重新猜上下文 |
| 安全副作用处理 | push/安装/修改结果不确定时先检查，不盲目重放 |
| Structured Validation | “成功但没输出”的命令不会被记成假失败 |
| Native Vue LSP | AI 可以像 IDE 一样理解 Vue 代码，不只靠文本搜索 |
| Desktop one-shot adopt | 升级有验证、备份、健康检查、LKG 和自动 rollback |
| Self-Maintaining Fork | 上游实现同等能力后，本地 patch 可以有证据地退休 |
| Doctor | 一条检查判断源码、运行版、工具链、rollback、Zero-Quota 是否健康 |
| 可追溯构建 | `.app` 可以追溯到精确源码 SHA 和工具链 |
| Single-Install Enhanced Runtime | 只安装 Desktop；Node + Context Bridge 随 App 打包并自动注册，不需要手改 Runner 配置 |

### 当前已经真实验证过什么

原始 macOS dogfood 的 `6a3f3ea4` 基线有机器验证证据；当前跨平台源码基线以本文顶部标出的 commit 为准：

- **Context Bridge black-box：PASS**
- **Native Context runtime：8/8 PASS**
- **Scoped Project Instructions：PASS**
- **Workflow Native Context continuity：PASS**
- **Required Capability Contract：12/12 PASS**
- **Desktop lifecycle regression：17/17 PASS**
- **Upstream compatibility rehearsal：PASS**
- **Bundled Node / Context Bridge：`v24.21.0` / `0.5.1`，bundled self-check PASS**
- **Single-Install Provider 自动注册：PASS**；普通 Desktop 用户不需要手工装 Node、复制 Bridge、修改 `runner.toml`
- **Native Codex model turns：0**
- **历史 macOS dogfood 对齐证据：Source / Installed Desktop / Running Server+Runner 曾全部对齐到 `6a3f3ea49e9f`**；当前跨平台源码基线见本文顶部。

### 哪些能力明确不属于这个 Goal

这套 Fork **没有**也**不声称**复制：OpenAI 私有 Codex Host 内部实现、Codex 模型 reasoning/planning、模型自己的 tool-selection intelligence、OpenAI 不公开的内部状态，以及 Native Codex TUI。

所以这里说的“原生 Codex 逼近”，准确含义是：

> **把能够观察、能够验证、能够独立实现的 Runtime 行为尽量补到 WebCodex；真正属于模型和私有 Host 的部分，仍然由 ChatGPT / OpenAI 模型自己负责。**

### 完整交接包

- [下载 `WebCodex-Zero-Quota-6a3f3ea4-macOS-Intel.zip`](deliverables/WebCodex-Zero-Quota-6a3f3ea4-macOS-Intel.zip)
- [交接包说明与 SHA256](deliverables/README.md)

交接包里包含：**已经内置 Node + Context Bridge 的可安装 Desktop**、精确源码快照、机器验证报告、校验和，以及更详细的《原生 Codex 逼近能力说明》。普通接收方只需要安装 Desktop，不需要再手工部署 Bridge。

**最后一句大白话总结：**这套 Fork 的价值不是“多几个工具”，而是让 ChatGPT + WebCodex 变成一个更可靠的本地开发 Agent——**懂项目、能真正动手、长任务不断、断线能恢复、真实操作不乱重试、Desktop 能安全升级、上游变化还能长期维护，而且不消耗 Native Codex 模型额度。**

## 开始使用

### 日常使用：完整 WebCodex（推荐）

如果你准备让 ChatGPT 长期使用自己的开发环境，推荐从 **普通 Server + Runner** 开始。这是 WebCodex 的完整开发体验：可以长期连接多个项目，并使用项目探索、编辑、Git、命令、测试、长任务和代码导航能力。公网 HTTPS、Cloudflare Tunnel 或 OpenAI Secure MCP Tunnel 只是 ChatGPT 到 Server 的连接方式，不会把你切换到另一套受限体验。

Windows / macOS 普通用户最推荐 **WebCodex Desktop + 官方 OpenAI Secure Tunnel**，直接按 [Desktop 安装与连接指南](docs/desktop-install.zh-CN.md)操作即可；CLI、已有 Server、自托管或高级配置再看[完整使用指南](docs/PERSONAL_SETUP.zh-CN.md)。

### 只想先试几分钟：临时分享

如果你只是想快速看看 WebCodex 是否适合自己，可以在一个仓库里运行：

```bash
cd /path/to/your/repository
npx --yes @yyjeqhc/webcodex share
```

`share` 会临时启动单项目、受限的 WebCodex 环境并给出 ChatGPT 连接信息；关闭命令后连接和临时凭据都会失效。它适合试用和临时分享，不是日常完整体验的默认部署方式。详细步骤见[快速试用](docs/QUICK_START.zh-CN.md)。

## 能做什么？

- **理解和修改代码** —— 读取、搜索、分析项目，并在配置好的项目范围内进行受保护的修改。
- **使用真实开发环境** —— 在仓库所在机器上运行命令、测试、格式化、编译器和项目自己的工具。
- **检查 Git** —— 查看状态和差异，让代码变化保持可见、可审查。
- **处理长时间任务** —— 任务可以持续运行并保持可观察，不需要一次模型回复一直等待到底。
- **保留人工审查** —— 可以通过运行时控制台和任务流程进行指导、取消、接受或拒绝。

## 为什么用 WebCodex？

- **代码留在自己的机器上。** 不需要把整个仓库上传到聊天服务。
- **AI 使用的是真实开发环境。** 文件、Git、编译器、测试和已有工具链都可以直接复用。
- **工作不局限于一次请求。** 长时间执行、测试结果和相关证据可以继续观察。
- **既能临时使用，也能长期部署。** 可以一条命令快速分享，也可以连接到自托管服务长期使用。

## 工作方式

```text
AI 客户端
   |
   | MCP / HTTPS
   v
WebCodex
   |
   v
你的机器
   |
   +-- 代码仓库
   +-- Git
   +-- 编译器 / 测试 / 开发工具
```

如果需要了解内部的 Server/Runner 架构、协议接口和权限边界，再阅读[架构说明](docs/ARCHITECTURE.md)、[MCP](docs/MCP.zh-CN.md)和[认证模型](docs/AUTH_MODEL.zh-CN.md)。

## Star History

[![Star History Chart](https://api.star-history.com/image?repos=yyjeqhc/webcodex&type=Date)](https://www.star-history.com/yyjeqhc/webcodex)

## 平台支持

- **Linux x64/arm64** —— 支持本机 `share`、Server 和 Runner 工作流。
- **macOS x64/arm64** —— 支持 Desktop 本机 Server + Runner、OpenAI Secure Tunnel、本机 `share` 和独立 Runner 工作流。
- **Windows x64** —— 推荐 Desktop 本机 Server + Runner + 官方 OpenAI Secure Tunnel；同时支持 CLI + Runner、本地前台 Server，以及显式 `webcodex share --tunnel cloudflare|openai|none`。
- **Windows arm64** —— 支持增强版 Desktop 本机 Server + Runner、CLI + Runner、本地前台 Server 与 `share`，managed OpenAI `tunnel-client` 可用。固定版本 Cloudflare 没有官方 Windows ARM64 artifact，因此使用 Cloudflare 时需要受信任的显式/`PATH` `cloudflared`。除 Desktop 自己托管的前台 runtime 外，WebCodex-managed Windows Server service 仍不支持。

Windows 接入和长期部署见[部署指南](docs/DEPLOYMENT.zh-CN.md)与 [MCP](docs/MCP.zh-CN.md)。

## 已有 Server 与高级配置

如果已经有人为你提供 WebCodex Server 和接入凭据，直接使用已有 Server 并看[完整使用指南](docs/PERSONAL_SETUP.zh-CN.md)。普通 Windows / macOS 个人安装使用 [Desktop 指南](docs/desktop-install.zh-CN.md)。生产环境、多用户、systemd/Docker、OAuth、代理/私有 CA 等运维内容再查看[部署指南](docs/DEPLOYMENT.zh-CN.md)。

这些是后续配置，不应该成为第一次使用 WebCodex 的概念负担。

## 文档

- [Desktop 安装与连接](docs/desktop-install.zh-CN.md) —— Windows / macOS 推荐路径：Desktop + 官方 OpenAI Secure Tunnel
- [Desktop 日常使用](docs/desktop-guide.zh-CN.md) —— 项目、连接、活动与后台运行
- [完整使用指南](docs/PERSONAL_SETUP.zh-CN.md) —— CLI、已有 Server、Linux 与高级普通 Server + Runner 配置
- [快速试用](docs/QUICK_START.zh-CN.md) —— 用 `share` 临时体验一个仓库
- [MCP](docs/MCP.zh-CN.md) —— ChatGPT、Claude、认证方式和 MCP 参考
- [部署指南](docs/DEPLOYMENT.zh-CN.md) —— 生产、自托管和高级运维
- [故障排查](docs/TROUBLESHOOTING.zh-CN.md) —— 连接和运行问题
- [CLI](docs/CLI.zh-CN.md) —— 命令与凭据参考
- [AI 辅助接入](docs/AI_ONBOARDING.zh-CN.md) —— 让 AI 帮你配置 WebCodex
- [安全说明](SECURITY.md) —— 安全模型与使用建议
- [文档索引](docs/INDEX.zh-CN.md) —— 全部用户和贡献者文档

## 安全

WebCodex 能在配置的项目范围内读取和修改文件、执行命令。建议使用版本控制，不要把凭据写进提示词、日志或 Git，只注册确实希望 AI 访问的项目目录。完整安全模型见 [SECURITY.md](SECURITY.md)。

## 从源码构建

```bash
cargo build --release --workspace --bins
export PATH="$PWD/target/release:$PATH"
```

## 参与贡献

欢迎提交贡献，也欢迎使用 WebCodex 或其他 coding agent 辅助开发。Bug 报告、开发流程与 PR 说明见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。

## 致谢

感谢 [LINUX DO](https://linux.do/) 社区提供友好的技术交流与开源分享环境。

## 许可证

使用 Apache License 2.0，见 [LICENSE](LICENSE)。
