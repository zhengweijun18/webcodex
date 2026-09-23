#!/usr/bin/env node
import readline from "node:readline";
import {
  BRIDGE_VERSION,
  DEFAULT_PROJECT_ROOT,
  bootstrapContext,
  codexThreadSummary,
  contextParityCheck,
  dispatchHookEvent,
  callNativeMcpReadOnly,
  callNativeMcpEffectful,
  describeNativeMcpTool,
  searchNativeMcpTools,
  readGlobalAgents,
  listNativeHooks,
  listNativeMcpServers,
  listNativeSkills,
  nativeHostExecReadOnly,
  readNativeSkill,
  resolveProjectKnowledge
} from "./bridge-lib.mjs";

const toolDefinitions = [
  {
    name: "bootstrap_context",
    description: "Bootstrap WebCodex from the live local Codex environment: user AGENTS, native Codex skills/hooks, knowledge routes, Ponytail state, hashes, and optional lifecycle hooks.",
    inputSchema: {
      type: "object",
      properties: {
        project_root: { type: "string" },
        phase: { type: ["string", "null"], enum: ["startup", "resume", "clear", "compact", null] },
        prompt: { type: ["string", "null"] },
        expected_fingerprint: { type: ["string", "null"] },
        instruction_fingerprint: { type: ["string", "null"] }
      },
      additionalProperties: false
    }
  },
  {
    name: "list_native_skills",
    description: "List a bounded catalog of Skills discovered by the current local Codex app-server for this project.",
    inputSchema: {
      type: "object",
      properties: { project_root: { type: "string" }, force_reload: { type: "boolean" } },
      additionalProperties: false
    }
  },
  {
    name: "read_user_agents",
    description: "Read the live user-level ~/.codex/AGENTS.md with its current SHA256. This is the user workflow source of truth.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false
    }
  },
  {
    name: "read_native_skill",
    description: "Read a bounded text resource from a Skill path that is present in native Codex skills/list.",
    inputSchema: {
      type: "object",
      required: ["skill_path"],
      properties: {
        project_root: { type: "string" },
        skill_path: { type: "string" },
        resource: { type: "string" }
      },
      additionalProperties: false
    }
  },
  {
    name: "list_native_hooks",
    description: "List native Codex hooks including Codex-computed currentHash, enabled state, source and trustStatus.",
    inputSchema: {
      type: "object",
      properties: { project_root: { type: "string" } },
      additionalProperties: false
    }
  },
  {
    name: "list_native_mcp_servers",
    description: "Read a bounded inventory of MCP/tool servers visible to the current native Codex app-server, including tool names and auth status. Does not call any listed tool.",
    inputSchema: {
      type: "object",
      properties: { project_root: { type: "string" } },
      additionalProperties: false
    }
  },
  {
    name: "search_native_mcp_tools",
    description: "Search the live native Codex MCP/tool catalog without starting a model turn. Defaults to explicitly read-only/non-destructive tools only.",
    inputSchema: {
      type: "object",
      properties: {
        project_root: { type: "string" },
        query: { type: "string" },
        server: { type: ["string", "null"] },
        read_only_only: { type: "boolean" },
        limit: { type: "integer", minimum: 1, maximum: 100 }
      },
      additionalProperties: false
    }
  },
  {
    name: "describe_native_mcp_tool",
    description: "Describe one live native Codex MCP/Plugin tool, including its input schema, annotations and target-mode policy class, without starting a model turn.",
    inputSchema: {
      type: "object",
      required: ["server", "tool"],
      properties: {
        project_root: { type: "string" },
        server: { type: "string" },
        tool: { type: "string" }
      },
      additionalProperties: false
    }
  },
  {
    name: "call_native_mcp_readonly",
    description: "Call one native Codex-managed MCP tool only when Codex marks it readOnlyHint=true and destructiveHint!=true. Uses app-server tool RPC directly and never starts a model turn.",
    inputSchema: {
      type: "object",
      required: ["server", "tool"],
      properties: {
        project_root: { type: "string" },
        server: { type: "string" },
        tool: { type: "string" },
        arguments: { type: "object" }
      },
      additionalProperties: false
    }
  },
  {
    name: "call_native_mcp_effectful",
    description: "Call one native Codex-managed MCP tool only when Codex marks it readOnlyHint=false and destructiveHint!=true. This is the target mode's deliberately broader-than-CLI non-destructive effect lane. It never starts a Codex model turn.",
    inputSchema: {
      type: "object",
      required: ["server", "tool"],
      properties: {
        project_root: { type: "string" },
        server: { type: "string" },
        tool: { type: "string" },
        arguments: { type: "object" }
      },
      additionalProperties: false
    }
  },
  {
    name: "native_host_exec_readonly",
    description: "Run one literal argv command through native Codex app-server command/exec with a hard-coded readOnly filesystem sandbox and network disabled. This request never starts a Codex thread/model turn. The outer WebCodex tool treats it as effectful and approval-gated because process execution is not equivalent to a pure read.",
    inputSchema: {
      type: "object",
      required: ["executable"],
      properties: {
        project_root: { type: "string" },
        executable: { type: "string", minLength: 1, maxLength: 1024 },
        args: {
          type: "array",
          maxItems: 256,
          items: { type: "string", maxLength: 8192 }
        },
        cwd: { type: ["string", "null"], maxLength: 1024 },
        timeout_ms: { type: ["integer", "null"], minimum: 1, maximum: 300000 }
      },
      additionalProperties: false
    }
  },
  {
    name: "dispatch_hook_event",
    description: "Run enabled trusted/managed native command hooks for one Codex lifecycle event. Never runs untrusted or modified hooks.",
    inputSchema: {
      type: "object",
      required: ["event_name"],
      properties: {
        project_root: { type: "string" },
        event_name: {
          type: "string",
          enum: ["preToolUse", "permissionRequest", "postToolUse", "preCompact", "postCompact", "sessionStart", "sessionEnd", "userPromptSubmit", "subagentStart", "subagentStop", "stop", "interrupt"]
        },
        payload: { type: "object" }
      },
      additionalProperties: false
    }
  },
  {
    name: "resolve_project_knowledge",
    description: "Resolve project knowledge locations strictly from reuse-manifest.json.knowledge_paths and return machine-entry hashes.",
    inputSchema: {
      type: "object",
      properties: { project_root: { type: "string" } },
      additionalProperties: false
    }
  },
  {
    name: "codex_thread_summary",
    description: "Resolve an exact/prefix native Codex session id or thread name and return a bounded user/assistant/tool-event summary from the local rollout.",
    inputSchema: {
      type: "object",
      required: ["session"],
      properties: { session: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 20 } },
      additionalProperties: false
    }
  },
  {
    name: "context_parity_check",
    description: "Check whether native Codex user skills, trusted hooks, global AGENTS and project knowledge entries are discoverable now; returns a live fingerprint and stale signal.",
    inputSchema: {
      type: "object",
      properties: { project_root: { type: "string" }, expected_fingerprint: { type: ["string", "null"] } },
      additionalProperties: false
    }
  }
];

function root(input) {
  return input?.project_root || DEFAULT_PROJECT_ROOT;
}

async function callTool(name, input = {}) {
  switch (name) {
    case "bootstrap_context":
      return bootstrapContext(root(input), {
        phase: input.phase === undefined ? "resume" : input.phase,
        prompt: input.prompt ?? null,
        expectedFingerprint: input.expected_fingerprint ?? null,
        instructionFingerprint: input.instruction_fingerprint ?? null
      });
    case "list_native_skills":
      return listNativeSkills(root(input), input.force_reload ?? true).then(result => ({
        cwd: result.cwd,
        count: result.skills.length,
        errors: result.errors,
        skills: result.skills.map(skill => ({
          name: skill.name,
          description: skill.description,
          path: skill.path,
          scope: skill.scope,
          enabled: skill.enabled,
          plugin_id: skill.pluginId || null
        }))
      }));
    case "read_user_agents":
      return readGlobalAgents();
    case "read_native_skill":
      return readNativeSkill(root(input), input.skill_path, input.resource || "SKILL.md");
    case "list_native_hooks":
      return listNativeHooks(root(input));
    case "list_native_mcp_servers":
      return listNativeMcpServers(root(input));
    case "search_native_mcp_tools":
      return searchNativeMcpTools(root(input), {
        query: input.query || "",
        server: input.server ?? null,
        readOnlyOnly: input.read_only_only ?? true,
        limit: input.limit || 50
      });
    case "describe_native_mcp_tool":
      return describeNativeMcpTool(root(input), input.server, input.tool);
    case "call_native_mcp_readonly":
      return callNativeMcpReadOnly(root(input), input.server, input.tool, input.arguments || {});
    case "call_native_mcp_effectful":
      return callNativeMcpEffectful(root(input), input.server, input.tool, input.arguments || {});
    case "native_host_exec_readonly":
      return nativeHostExecReadOnly(root(input), {
        executable: input.executable,
        args: input.args || [],
        cwd: input.cwd ?? null,
        timeoutMs: input.timeout_ms ?? null
      });
    case "dispatch_hook_event":
      return dispatchHookEvent(root(input), input.event_name, input.payload || {});
    case "resolve_project_knowledge":
      return resolveProjectKnowledge(root(input));
    case "codex_thread_summary":
      return codexThreadSummary(input.session, input.limit || 12);
    case "context_parity_check":
      return contextParityCheck(root(input), input.expected_fingerprint ?? null);
    default:
      throw new Error(`unknown tool: ${name}`);
  }
}

function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async line => {
  let request;
  try { request = JSON.parse(line); }
  catch {
    send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
    return;
  }
  if (!Object.prototype.hasOwnProperty.call(request, "id")) return;
  try {
    if (request.method === "initialize") {
      send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          protocolVersion: request.params?.protocolVersion || "2025-06-18",
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: "webcodex-codex-context-bridge", version: BRIDGE_VERSION },
          instructions: "Use bootstrap_context at WebCodex session start/resume, then read_user_agents when the user-level AGENTS body is not already retained. Route against native_skills.catalog and load only the matching SKILL.md with read_native_skill. For native Codex-managed MCP/Plugin capabilities, search with search_native_mcp_tools, inspect exact arguments with describe_native_mcp_tool, then call read-only tools through call_native_mcp_readonly or effectful-but-non-destructive tools through call_native_mcp_effectful. native_host_exec_readonly is the Native-first standalone execution lane: it uses Codex app-server command/exec without a thread/turn, hard-codes readOnly filesystem sandboxing and disables network; the outer WebCodex contract still treats process execution as effectful and approval-gated. Destructive generic MCP tools remain unavailable. None of these paths starts a Codex model turn. Never use Codex ACP/turn/prompt execution in zero-quota target mode. Never automate or scrape a ChatGPT Web browser/session from this bridge. A non-essential provider/probe that times out or is unavailable must not block the task: record the degraded capability and use an already validated fallback instead of repeating the same long call. Run userPromptSubmit before acting on each new user coding prompt; run subagentStart only when the task explicitly calls for subagent work; run sessionEnd only on a real session close."
        }
      });
    } else if (request.method === "ping") {
      send({ jsonrpc: "2.0", id: request.id, result: {} });
    } else if (request.method === "tools/list") {
      send({ jsonrpc: "2.0", id: request.id, result: { tools: toolDefinitions } });
    } else if (request.method === "tools/call") {
      const name = request.params?.name;
      const input = request.params?.arguments || {};
      const result = await callTool(name, input);
      send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          content: [{ type: "text", text: JSON.stringify(result) }],
          structuredContent: result,
          isError: false
        }
      });
    } else {
      send({ jsonrpc: "2.0", id: request.id, error: { code: -32601, message: "Method not found" } });
    }
  } catch (error) {
    send({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        content: [{ type: "text", text: JSON.stringify({ error: error?.message || String(error) }) }],
        isError: true
      }
    });
  }
});

