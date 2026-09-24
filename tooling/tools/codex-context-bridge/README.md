# WebCodex · Codex Context Bridge

把本机 Codex 的可观察上下文通过 Runner 内部 MCP 提供给 WebCodex。它是 WebCodex fork 的可复现运行组件，不是模型可直接枚举/调用的通用 MCP Provider。它不复制 Native Host 或模型智能，也不产生 Codex model turn。

当前 Bridge 版本为 **0.6.1**。`readiness.mjs` 是 Desktop、Runner 与 Bridge 共同使用的 Codex reference 真相源：允许受支持的 CLI symlink 入口，但必须 canonicalize 到最终 regular executable；显式无效的 `CODEX_BIN` 会 fail closed，不会静默改用另一份 Codex。

## 能力

- `bootstrap_context`：读取用户级 `AGENTS.md`、原生 Codex Skill catalog、Hook registry、
  项目知识入口和当前 Ponytail mode，并生成 context fingerprint。
- `list_native_skills` / `read_native_skill`：通过当前 Codex app-server 的
  `skills/list` 获取完整原生目录，再由 WebCodex 以分页、pathless 方式发现和读取 Skill。
- `list_native_hooks` / `dispatch_hook_event`：通过当前 Codex app-server 的
  `hooks/list` 获取 Codex 自己计算的 trust status；只执行 `trusted/managed` 且 enabled
  的 command hook。
- `search_native_mcp_tools` / `describe_native_mcp_tool` / `call_native_mcp_readonly` /
  `call_native_mcp_effectful`：读取 Codex-managed MCP/Plugin 的 live catalog，并只允许
  read-only 或明确 non-destructive 的 effectful lane；destructive / unclassified 工具不进入
  通用调用代理。
- `native_host_exec_readonly`：通过 Codex app-server `command/exec` 执行 literal argv；强制 `readOnly` 文件系统 sandbox、关闭 network，且不创建 thread/model turn。上层 WebCodex 仍将进程执行按 effectful + approval-gated 处理。
- `resolve_project_knowledge`：只按项目 `reuse-manifest.json.knowledge_paths` 解析知识入口。
- `native_thread_read`：用 app-server `thread/read` + `thread/items/list` 读取一个线程的
  canonical 元数据与分页任务状态，并移除本机路径和原始 MCP 参数/结果。
- `codex_thread_summary`：保留为本地 session index / rollout 的有界兼容摘要。
- `context_parity_check`：检查用户级 AGENTS、Skill、Hook trust、项目知识入口和 fingerprint。

## 设计边界

- Codex app-server 是 Skill/Hook 发现与 Hook trust 的权威来源，Bridge 不重算 Hook trust hash；每次 bootstrap 使用一个已完成 `initialize → initialized` 握手的有界连接。
- WebCodex 的通用 `mcp_tool` 必须隐藏并拒绝此 Provider；path-bearing Bridge 结果只能进入内部 same-Runner wrapper，再投影为 pathless model surface。
- 默认不执行未信任、已修改、禁用或非 command Hook。
- `sessionEnd` 只应在真实会话结束时派发；不要把普通一轮回复当作 session end。
- Bridge Hook runtime 是可观察语义的近似层，不冒充 OpenAI private Host lifecycle；`host_lifecycle_intercept=false` 是上层必须保持的边界。
- Native-first Host Adapter 只复用 Codex 明确暴露的 zero-turn app-server contract；禁止通过本 Bridge 自动控制、抓取或持有 ChatGPT Web 浏览器/账号会话。
- Bridge 不缓存正文；fingerprint 覆盖 Skill body、knowledge entry SHA 与上层 instruction fingerprint，用于 Workflow Session resume 的 stale 检测。
- Native Context readiness 在同一个有界预算内同时验证 canonical Codex reference、Codex app-server `initialize → initialized → skills/list`，以及实际 bundled Bridge Provider 的 MCP `initialize → tools/list → read_user_agents`。`native_model_turns` 必须保持 `0`；它不启动 Codex model turn，也不代表 private Host/TUI 等价。

## 本地自检

```bash
node tooling/tools/codex-context-bridge/self-check.mjs
```

检查当前真实 Codex reference：

```bash
node tooling/tools/codex-context-bridge/readiness.mjs --json --timeout-ms 3000
```

