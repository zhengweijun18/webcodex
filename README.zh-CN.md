# WebCodex

[English](README.md) | [简体中文](README.zh-CN.md)

**WebCodex 让 ChatGPT、Claude 和其他 AI Agent 直接使用你自己机器上的代码仓库和开发工具。**

你可以直接让 AI 理解项目、修改代码、运行测试、检查 Git 或排查问题。仓库仍然留在原来的机器上，不需要为了使用 WebCodex 把整个项目搬到托管环境里。

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

## 本 Fork 的增强能力：Zero-Quota 原生 Codex 逼近

这个 Fork 另外维护了一条增强分支 [`vue-lsp-native-main`](https://github.com/zhengweijun18/webcodex/tree/vue-lsp-native-main)，目标是让 **ChatGPT + WebCodex 更像一个可靠的本地开发 Agent，同时保持 Native Codex 模型调用为 0**。

当前已验证基线：`3082590b87de6f023d775c98f8a082df6b4168c4`。

这条增强分支主要增加了这些能力：

- **Zero-Quota Native Context**：可以读取可观察的 Codex Runtime 上下文，例如 Skill、项目规则、Hook、Knowledge，但不会启动 Native Codex model turn，也不会在 WebCodex 做不了时偷偷 fallback 到 Codex Agent。
- **自动项目上下文**：AI 开工前可自动拿到根目录/子目录 `AGENTS.md`、可用 Skill、Knowledge、Runtime Context 和 Workflow 当前现场，不需要每次重新解释项目怎么玩。
- **Skill / Knowledge 不依赖本机绝对路径**：模型通过 Skill 名称、opaque id、semantic key 工作，避免把开发机目录结构变成接口。
- **长任务和 Workflow 可恢复**：build/test 等后台 Job 不跟一次聊天一起消失；断线、重连、Runtime 重启后可以继续观察任务和恢复当前工作上下文。
- **真实操作更安全**：push、安装、文件修改等操作如果结果不确定，不会直接重复执行；retry 不会偷偷延长原 deadline，也不会静默换到另一个 Runner。
- **验证结果更可信**：以真实终态和 exit code 判断成功/失败，避免 `cargo fmt --check` 这类“成功但没有输出”的命令被记成假失败。
- **原生 Vue LSP**：支持 Vue SFC 的定义跳转、引用、诊断、符号等能力，并使用固定的 Vue Language Server / TypeScript 工具链。
- **Desktop one-shot 安全升级**：候选版本验证、当前版本备份、精确退出 App 自身进程、只启动一次、健康检查、Last Known Good、失败自动回滚。
- **Self-Maintaining Fork**：不是看“补丁还能不能套”，而是通过行为验证判断 upstream 是否已经真正实现同等能力；只有验证通过，本地 patch 才允许退休。
- **Doctor + 可追溯构建**：可以机器化检查源码、已安装 Desktop、正在运行的 Server/Runner、rollback、工具链、Zero-Quota 证据和构建 provenance 是否一致。

这里的“逼近原生 Codex”只指 **可观察、可验证的 Runtime 行为**，不代表复制 OpenAI 私有 Codex Host、模型 reasoning/planning、模型自己的 tool-selection intelligence 或 Native Codex TUI。

当前验证版的 macOS Intel 完整交接包也已同步到仓库：

- [WebCodex-Zero-Quota-3082590b-macOS-Intel.zip](deliverables/WebCodex-Zero-Quota-3082590b-macOS-Intel.zip)
- [交接包说明与 SHA256](deliverables/README.md)

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
- **Windows arm64** —— 支持 CLI + Runner、本地前台 Server 与 `share`，managed OpenAI `tunnel-client` 可用。固定版本 Cloudflare 没有官方 Windows ARM64 artifact，因此使用 Cloudflare 时需要受信任的显式/`PATH` `cloudflared`。当前 Desktop installer 仅提供 Windows x64。除 Desktop 自己托管的前台 runtime 外，WebCodex-managed Windows Server service 仍不支持。

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
