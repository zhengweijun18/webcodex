# WebCodex · Codex Context Bridge

把本机 Codex 的可观察上下文通过 Runner 内部 MCP 提供给 WebCodex。它是 WebCodex fork 的可复现运行组件，不是模型可直接枚举/调用的通用 MCP Provider。它不复制 Native Host 或模型智能，也不产生 Codex model turn。

## 能力

- `bootstrap_context`：读取用户级 `AGENTS.md`、原生 Codex Skill catalog、Hook registry、
  项目知识入口和当前 Ponytail mode，并生成 context fingerprint。
- `list_native_skills` / `read_native_skill`：通过当前 Codex app-server 的
  `skills/list` 获取原生发现结果，再受限读取被发现 Skill 的文本资源。
- `list_native_hooks` / `dispatch_hook_event`：通过当前 Codex app-server 的
  `hooks/list` 获取 Codex 自己计算的 trust status；只执行 `trusted/managed` 且 enabled
  的 command hook。
- `resolve_project_knowledge`：只按项目 `reuse-manifest.json.knowledge_paths` 解析知识入口。
- `codex_thread_summary`：从本机 Codex session index / rollout 中恢复一个线程的有界进度摘要。
- `context_parity_check`：检查用户级 AGENTS、Skill、Hook trust、项目知识入口和 fingerprint。

## 设计边界

- Codex app-server 是 Skill/Hook 发现与 Hook trust 的权威来源，Bridge 不重算 Hook trust hash；每次 bootstrap 使用一个已完成 `initialize → initialized` 握手的有界连接。
- WebCodex 的通用 `mcp_tool` 必须隐藏并拒绝此 Provider；path-bearing Bridge 结果只能进入内部 same-Runner wrapper，再投影为 pathless model surface。
- 默认不执行未信任、已修改、禁用或非 command Hook。
- `sessionEnd` 只应在真实会话结束时派发；不要把普通一轮回复当作 session end。
- Bridge Hook runtime 是可观察语义的近似层，不冒充 OpenAI private Host lifecycle；`host_lifecycle_intercept=false` 是上层必须保持的边界。
- Bridge 不缓存正文；fingerprint 覆盖 Skill body、knowledge entry SHA 与上层 instruction fingerprint，用于 Workflow Session resume 的 stale 检测。

## 本地自检

```bash
node tooling/tools/codex-context-bridge/self-check.mjs
```

