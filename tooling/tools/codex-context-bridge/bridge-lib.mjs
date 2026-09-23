import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { spawn } from "node:child_process";
import {
  BRIDGE_VERSION,
  codexChildEnvironment,
  codexSpawnOptions,
  resolveCodexExecutable,
  stopChildProcess
} from "./readiness.mjs";

export { BRIDGE_VERSION };
export const DEFAULT_PROJECT_ROOT = process.env.WEBCODEX_PROJECT_ROOT || process.cwd();
export const CODEX_HOME = process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
export const CODEX_RESOLUTION = resolveCodexExecutable();
export const CODEX_BIN = CODEX_RESOLUTION.canonical_path || null;

function requireCodexBin() {
  if (!CODEX_BIN) {
    throw new Error(CODEX_RESOLUTION.reason || "codex_reference_missing");
  }
  return CODEX_BIN;
}

const MAX_TEXT_BYTES = 128 * 1024;
const TRUSTED_HOOK_STATUSES = new Set(["trusted", "managed"]);
const EVENT_NAMES = new Set([
  "preToolUse", "permissionRequest", "postToolUse", "preCompact", "postCompact",
  "sessionStart", "sessionEnd", "userPromptSubmit", "subagentStart", "subagentStop",
  "stop", "interrupt"
]);

export function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function fileDigest(filePath) {
  const data = fs.readFileSync(filePath);
  return { path: filePath, sha256: sha256(data), bytes: data.length };
}

export function readTextBounded(filePath, maxBytes = MAX_TEXT_BYTES) {
  const data = fs.readFileSync(filePath);
  if (data.length > maxBytes) {
    throw new Error(`file too large for bridge read: ${filePath} (${data.length} bytes)`);
  }
  return data.toString("utf8");
}

function ensureProjectRoot(projectRoot) {
  const resolved = path.resolve(projectRoot || DEFAULT_PROJECT_ROOT);
  const stat = fs.statSync(resolved);
  if (!stat.isDirectory()) throw new Error(`project_root is not a directory: ${resolved}`);
  return resolved;
}

function rpcTimeout(ms, message) {
  return new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    // A completed RPC must not keep a one-shot Bridge process alive solely
    // because the losing Promise.race timeout is still pending.
    timer.unref?.();
  });
}

export async function codexRpc(method, params, { timeoutMs = 12000 } = {}) {
  const codexBin = requireCodexBin();
  const child = spawn(codexBin, ["app-server", "--stdio"], codexSpawnOptions(codexBin, {
    cwd: DEFAULT_PROJECT_ROOT,
    env: codexChildEnvironment(codexBin),
    stdio: ["pipe", "pipe", "pipe"]
  }));
  let stderr = "";
  child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-16000); });
  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  const waiters = new Map();
  rl.on("line", line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (message && Object.prototype.hasOwnProperty.call(message, "id")) {
      const waiter = waiters.get(String(message.id));
      if (waiter) {
        waiters.delete(String(message.id));
        if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
      }
    }
  });
  const request = (id, requestMethod, requestParams) => new Promise((resolve, reject) => {
    waiters.set(String(id), { resolve, reject });
    child.stdin.write(JSON.stringify({ id, method: requestMethod, params: requestParams }) + "\n");
  });
  try {
    const initialized = await Promise.race([
      request(1, "initialize", {
        clientInfo: { name: "webcodex-codex-context-bridge", version: BRIDGE_VERSION },
        capabilities: { experimentalApi: true }
      }),
      rpcTimeout(timeoutMs, "Codex app-server initialize timed out")
    ]);
    child.stdin.write(JSON.stringify({ method: "initialized", params: {} }) + "\n");
    const result = await Promise.race([
      request(2, method, params),
      rpcTimeout(timeoutMs, `Codex app-server ${method} timed out`)
    ]);
    return { initialized, result };
  } finally {
    await stopChildProcess(child);
    try { rl.close(); } catch {}
  }
}

async function withCodexAppServer(projectRoot, callback, { timeoutMs = 60000 } = {}) {
  const root = ensureProjectRoot(projectRoot);
  const codexBin = requireCodexBin();
  const child = spawn(codexBin, ["app-server", "--stdio"], codexSpawnOptions(codexBin, {
    cwd: root,
    env: codexChildEnvironment(codexBin),
    stdio: ["pipe", "pipe", "pipe"]
  }));
  let stderr = "";
  child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-16000); });
  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  const waiters = new Map();
  let nextId = 0;
  rl.on("line", line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || !Object.prototype.hasOwnProperty.call(message, "id")) return;
    const waiter = waiters.get(String(message.id));
    if (!waiter) return;
    waiters.delete(String(message.id));
    if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
    else waiter.resolve(message.result);
  });
  const request = (method, params, requestTimeoutMs = timeoutMs) => {
    const id = ++nextId;
    const response = new Promise((resolve, reject) => {
      waiters.set(String(id), { resolve, reject });
      child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    });
    return Promise.race([
      response,
      rpcTimeout(requestTimeoutMs, `Codex app-server ${method} timed out`)
    ]);
  };
  try {
    await request("initialize", {
      clientInfo: { name: "webcodex-codex-context-bridge", version: BRIDGE_VERSION },
      capabilities: { experimentalApi: true }
    }, 12000);
    child.stdin.write(JSON.stringify({ method: "initialized", params: {} }) + "\n");
    return await callback({ request, root });
  } catch (error) {
    const suffix = stderr.trim() ? `; stderr: ${stderr.trim().slice(-4000)}` : "";
    throw new Error(`${error?.message || String(error)}${suffix}`);
  } finally {
    await stopChildProcess(child);
    try { rl.close(); } catch {}
  }
}

function nativeMcpToolEntries(server) {
  const tools = server?.tools || {};
  if (Array.isArray(tools)) {
    return tools.filter(tool => tool && typeof tool === "object" && tool.name);
  }
  return Object.entries(tools).map(([name, tool]) => ({ name, ...(tool || {}) }));
}

function nativeMcpToolIsReadOnly(tool) {
  return tool?.annotations?.readOnlyHint === true && tool?.annotations?.destructiveHint !== true;
}

function nativeMcpToolIsEffectfulNonDestructive(tool) {
  return tool?.annotations?.readOnlyHint === false && tool?.annotations?.destructiveHint !== true;
}

function nativeMcpToolPolicyClass(tool) {
  if (nativeMcpToolIsReadOnly(tool)) return "read_only";
  if (tool?.annotations?.destructiveHint === true) return "destructive";
  if (nativeMcpToolIsEffectfulNonDestructive(tool)) return "effectful_non_destructive";
  return "unclassified";
}

function compactNativeMcpTool(serverName, tool) {
  return {
    server: serverName,
    name: tool.name,
    description: typeof tool.description === "string" && tool.description.length > 320
      ? tool.description.slice(0, 320) + "…"
      : (tool.description || null),
    annotations: tool.annotations || null,
    read_only: nativeMcpToolIsReadOnly(tool),
    policy_class: nativeMcpToolPolicyClass(tool)
  };
}

function boundedNativeMcpResult(value, maxBytes = 96 * 1024) {
  const text = JSON.stringify(value);
  const bytes = Buffer.byteLength(text);
  if (bytes <= maxBytes) return { truncated: false, bytes, value };
  return {
    truncated: true,
    bytes,
    preview_json: Buffer.from(text).subarray(0, maxBytes).toString("utf8")
  };
}

export async function searchNativeMcpTools(projectRoot, {
  query = "",
  server = null,
  readOnlyOnly = true,
  limit = 50
} = {}) {
  ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("mcpServerStatus/list", {
    detail: "toolsAndAuthOnly",
    limit: 100
  }, { timeoutMs: 45000 });
  const needle = String(query || "").trim().toLowerCase();
  const selectedServer = server ? String(server) : null;
  const all = [];
  for (const item of result?.data || []) {
    const serverName = item.name || item.serverName || item.id || null;
    if (!serverName || (selectedServer && serverName !== selectedServer)) continue;
    for (const tool of nativeMcpToolEntries(item)) {
      const compact = compactNativeMcpTool(serverName, tool);
      if (readOnlyOnly && !compact.read_only) continue;
      const haystack = `${serverName}\n${tool.name}\n${tool.description || ""}`.toLowerCase();
      if (needle && !haystack.includes(needle)) continue;
      all.push(compact);
    }
  }
  const boundedLimit = Math.max(1, Math.min(Number(limit) || 50, 100));
  return {
    query: query || null,
    server: selectedServer,
    read_only_only: Boolean(readOnlyOnly),
    total_matches: all.length,
    tools: all.slice(0, boundedLimit),
    truncated: all.length > boundedLimit,
    quota_mode: "zero_codex_model_turn"
  };
}

export async function describeNativeMcpTool(projectRoot, serverName, toolName) {
  if (!serverName || !toolName) throw new Error("server and tool are required");
  ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("mcpServerStatus/list", {
    detail: "toolsAndAuthOnly",
    limit: 100
  }, { timeoutMs: 45000 });
  const server = (result?.data || []).find(item =>
    (item.name || item.serverName || item.id) === serverName
  );
  if (!server) throw new Error(`native MCP server not found: ${serverName}`);
  const tool = nativeMcpToolEntries(server).find(item => item.name === toolName);
  if (!tool) throw new Error(`native MCP tool not found: ${serverName}.${toolName}`);
  const inputSchema = tool.inputSchema ?? tool.input_schema ?? null;
  return {
    server: serverName,
    name: toolName,
    description: tool.description || null,
    annotations: tool.annotations || null,
    policy_class: nativeMcpToolPolicyClass(tool),
    input_schema: boundedNativeMcpResult(inputSchema, 48 * 1024),
    quota_mode: "zero_codex_model_turn"
  };
}

export async function callNativeMcpReadOnly(projectRoot, serverName, toolName, args = {}) {
  if (!serverName || !toolName) throw new Error("server and tool are required");
  return withCodexAppServer(projectRoot, async ({ request, root }) => {
    const status = await request("mcpServerStatus/list", {
      detail: "toolsAndAuthOnly",
      limit: 100
    }, 45000);
    const server = (status?.data || []).find(item =>
      (item.name || item.serverName || item.id) === serverName
    );
    if (!server) throw new Error(`native MCP server not found: ${serverName}`);
    const tool = nativeMcpToolEntries(server).find(item => item.name === toolName);
    if (!tool) throw new Error(`native MCP tool not found: ${serverName}.${toolName}`);
    if (!nativeMcpToolIsReadOnly(tool)) {
      throw new Error(`native MCP tool is not explicitly read-only/non-destructive: ${serverName}.${toolName}`);
    }
    const thread = await request("thread/start", { cwd: root, ephemeral: true }, 30000);
    const threadId = thread?.thread?.id;
    if (!threadId) throw new Error("Codex app-server did not return an ephemeral thread id");
    const result = await request("mcpServer/tool/call", {
      server: serverName,
      threadId,
      tool: toolName,
      arguments: args || {}
    }, 60000);
    return {
      server: serverName,
      tool: toolName,
      annotations: tool.annotations || null,
      quota_mode: "zero_codex_model_turn",
      model_turn_started: false,
      result: boundedNativeMcpResult(result)
    };
  });
}

export async function callNativeMcpEffectful(projectRoot, serverName, toolName, args = {}) {
  if (!serverName || !toolName) throw new Error("server and tool are required");
  return withCodexAppServer(projectRoot, async ({ request, root }) => {
    const status = await request("mcpServerStatus/list", {
      detail: "toolsAndAuthOnly",
      limit: 100
    }, 45000);
    const server = (status?.data || []).find(item =>
      (item.name || item.serverName || item.id) === serverName
    );
    if (!server) throw new Error(`native MCP server not found: ${serverName}`);
    const tool = nativeMcpToolEntries(server).find(item => item.name === toolName);
    if (!tool) throw new Error(`native MCP tool not found: ${serverName}.${toolName}`);
    if (!nativeMcpToolIsEffectfulNonDestructive(tool)) {
      throw new Error(
        `native MCP tool is not explicitly effectful/non-destructive: ${serverName}.${toolName}`
      );
    }
    const thread = await request("thread/start", { cwd: root, ephemeral: true }, 30000);
    const threadId = thread?.thread?.id;
    if (!threadId) throw new Error("Codex app-server did not return an ephemeral thread id");
    const result = await request("mcpServer/tool/call", {
      server: serverName,
      threadId,
      tool: toolName,
      arguments: args || {}
    }, 60000);
    return {
      server: serverName,
      tool: toolName,
      annotations: tool.annotations || null,
      policy_class: "effectful_non_destructive",
      quota_mode: "zero_codex_model_turn",
      model_turn_started: false,
      result: boundedNativeMcpResult(result)
    };
  });
}

const NATIVE_HOST_EXEC_MAX_ARGS = 256;
const NATIVE_HOST_EXEC_MAX_ARG_BYTES = 8192;
const NATIVE_HOST_EXEC_MAX_TOTAL_ARG_BYTES = 16 * 1024;
const NATIVE_HOST_EXEC_DEFAULT_TIMEOUT_MS = 30_000;
const NATIVE_HOST_EXEC_MAX_TIMEOUT_MS = 5 * 60_000;
const NATIVE_HOST_EXEC_OUTPUT_CAP_BYTES = 64 * 1024;

function nativeHostExecCommand(executable, args = []) {
  if (typeof executable !== "string" || !executable || executable.length > 1024 || executable.includes("\0")) {
    throw new Error("native host executable must be a non-empty bounded string");
  }
  if (!Array.isArray(args) || args.length > NATIVE_HOST_EXEC_MAX_ARGS) {
    throw new Error(`native host args must contain at most ${NATIVE_HOST_EXEC_MAX_ARGS} items`);
  }
  const values = [executable, ...args];
  let totalBytes = 0;
  for (const value of values) {
    if (typeof value !== "string" || value.includes("\0")) {
      throw new Error("native host argv values must be strings without NUL bytes");
    }
    const bytes = Buffer.byteLength(value);
    if (bytes > NATIVE_HOST_EXEC_MAX_ARG_BYTES) {
      throw new Error(`native host argv item exceeds ${NATIVE_HOST_EXEC_MAX_ARG_BYTES} bytes`);
    }
    totalBytes += bytes;
  }
  if (totalBytes > NATIVE_HOST_EXEC_MAX_TOTAL_ARG_BYTES) {
    throw new Error(`native host argv exceeds ${NATIVE_HOST_EXEC_MAX_TOTAL_ARG_BYTES} bytes`);
  }
  return values;
}

function nativeHostExecCwd(projectRoot, requestedCwd) {
  const root = ensureProjectRoot(projectRoot);
  const canonicalRoot = fs.realpathSync(root);
  if (requestedCwd === null || requestedCwd === undefined || requestedCwd === "" || requestedCwd === ".") {
    return canonicalRoot;
  }
  if (typeof requestedCwd !== "string" || requestedCwd.length > 1024 || path.isAbsolute(requestedCwd)) {
    throw new Error("native host cwd must be a bounded project-relative path");
  }
  const candidate = path.resolve(root, requestedCwd);
  const lexicalInside = candidate === root || candidate.startsWith(root + path.sep);
  if (!lexicalInside || !fs.existsSync(candidate) || !fs.statSync(candidate).isDirectory()) {
    throw new Error("native host cwd must resolve to an existing directory inside the project");
  }
  const canonical = fs.realpathSync(candidate);
  const canonicalInside = canonical === canonicalRoot || canonical.startsWith(canonicalRoot + path.sep);
  if (!canonicalInside) {
    throw new Error("native host cwd escapes the project after canonicalization");
  }
  return canonical;
}

function nativeHostExecTimeout(value) {
  if (value === null || value === undefined) return NATIVE_HOST_EXEC_DEFAULT_TIMEOUT_MS;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < 1) {
    throw new Error("native host timeout_ms must be a positive integer");
  }
  return Math.min(parsed, NATIVE_HOST_EXEC_MAX_TIMEOUT_MS);
}

/**
 * Run one literal argv command through Codex app-server's standalone
 * command/exec contract. The app-server schema explicitly guarantees this
 * request does not create a thread or turn. WebCodex hard-codes the strict
 * read-only/no-network sandbox here; callers cannot widen it.
 *
 * A read-only filesystem sandbox does not make an arbitrary executable a pure
 * observation (for example, a process could still attempt process-local side
 * effects), so the outer WebCodex tool remains an effectful, approval-gated
 * operation even though its Codex sandbox cannot write the workspace.
 */
export async function nativeHostExecReadOnly(projectRoot, {
  executable,
  args = [],
  cwd = null,
  timeoutMs = null
} = {}) {
  const root = ensureProjectRoot(projectRoot);
  const command = nativeHostExecCommand(executable, args);
  const resolvedCwd = nativeHostExecCwd(root, cwd);
  const effectiveTimeoutMs = nativeHostExecTimeout(timeoutMs);
  return withCodexAppServer(root, async ({ request }) => {
    const result = await request("command/exec", {
      command,
      cwd: resolvedCwd,
      timeoutMs: effectiveTimeoutMs,
      outputBytesCap: NATIVE_HOST_EXEC_OUTPUT_CAP_BYTES,
      sandboxPolicy: {
        type: "readOnly",
        networkAccess: false
      },
      tty: false,
      streamStdin: false,
      streamStdoutStderr: false
    }, effectiveTimeoutMs + 5_000);
    return {
      host_adapter: "codex_app_server",
      method: "command/exec",
      sandbox: {
        type: "readOnly",
        network_access: false
      },
      cwd: path.relative(fs.realpathSync(root), resolvedCwd) || ".",
      exit_code: Number.isInteger(result?.exitCode) ? result.exitCode : null,
      stdout: typeof result?.stdout === "string" ? result.stdout : "",
      stderr: typeof result?.stderr === "string" ? result.stderr : "",
      timeout_ms: effectiveTimeoutMs,
      output_bytes_cap: NATIVE_HOST_EXEC_OUTPUT_CAP_BYTES,
      quota_mode: "zero_codex_model_turn",
      model_turn_started: false,
      state_changed: false
    };
  }, { timeoutMs: effectiveTimeoutMs + 10_000 });
}

function permissionProfileFromThreadStart(result) {
  const sandbox = result?.sandbox || null;
  const activePermissionProfile = result?.activePermissionProfile || null;
  return {
    approval_policy: result?.approvalPolicy || null,
    approvals_reviewer: result?.approvalsReviewer || null,
    sandbox: sandbox ? {
      type: sandbox.type || null,
      writable_roots: sandbox.writableRoots || [],
      network_access: sandbox.networkAccess ?? null
    } : null,
    active_permission_profile: activePermissionProfile?.id || null,
    multi_agent_mode: result?.multiAgentMode || null,
    model: result?.model || result?.thread?.model || null,
    reasoning_effort: result?.reasoningEffort || result?.thread?.reasoningEffort || null
  };
}

export async function nativePermissionProfileSummary(projectRoot) {
  const root = ensureProjectRoot(projectRoot);
  const { result } = await codexRpc(
    "thread/start",
    { cwd: root, ephemeral: true },
    { timeoutMs: 30000 }
  );
  return permissionProfileFromThreadStart(result);
}

function skillsFromListResult(root, result) {
  return result?.data?.[0] || { cwd: root, skills: [], errors: [] };
}

export async function listNativeSkills(projectRoot, forceReload = true) {
  const root = ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("skills/list", { cwds: [root], forceReload });
  return skillsFromListResult(root, result);
}

function hooksFromListResult(root, result) {
  return result?.data?.[0] || { cwd: root, hooks: [], warnings: [], errors: [] };
}

export async function listNativeHooks(projectRoot) {
  const root = ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("hooks/list", { cwds: [root] });
  return hooksFromListResult(root, result);
}


function runtimeConfigFromResult(result) {
  const config = result?.config || result || {};
  return {
    model: config.model || null,
    model_reasoning_effort: config.model_reasoning_effort || null,
    approvals_reviewer: config.approvals_reviewer || null,
    service_tier: config.service_tier || null,
    features: {
      codex_apps: Boolean(config.features?.codex_apps),
      computer_use: Boolean(config.features?.computer_use),
      js_repl: Boolean(config.features?.js_repl),
      remote_plugin: Boolean(config.features?.remote_plugin)
    },
    enabled_plugins: Object.entries(config.plugins || {})
      .filter(([, value]) => value?.enabled !== false)
      .map(([id]) => id)
      .sort(),
    configured_mcp_servers: Object.entries(config.mcp_servers || {})
      .filter(([, value]) => value?.enabled !== false)
      .map(([id]) => id)
      .sort()
  };
}

export async function nativeRuntimeConfigSummary(projectRoot) {
  const root = ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("config/read", { cwd: root, includeLayers: false });
  return runtimeConfigFromResult(result);
}

export async function listNativeMcpServers(projectRoot) {
  ensureProjectRoot(projectRoot);
  const { result } = await codexRpc("mcpServerStatus/list", {
    detail: "toolsAndAuthOnly",
    limit: 100
  }, { timeoutMs: 45000 });
  return {
    servers: (result?.data || []).map(server => {
      const tools = Array.isArray(server.tools)
        ? server.tools.map(tool => typeof tool === "string" ? tool : tool?.name).filter(Boolean)
        : Object.keys(server.tools || {});
      return {
        name: server.name || server.serverName || server.id || null,
        auth_status: server.authStatus || null,
        tool_count: tools.length,
        tools: tools.sort(),
        error: server.error || null
      };
    }),
    next_cursor: result?.nextCursor || null
  };
}

function skillResourcePath(skillPath, resource = "SKILL.md") {
  const packageRoot = path.dirname(path.resolve(skillPath));
  const target = path.resolve(packageRoot, resource || "SKILL.md");
  const prefix = packageRoot.endsWith(path.sep) ? packageRoot : packageRoot + path.sep;
  if (target !== path.join(packageRoot, "SKILL.md") && !target.startsWith(prefix)) {
    throw new Error("skill resource escapes selected package");
  }
  return target;
}

export async function readNativeSkill(projectRoot, skillPath, resource = "SKILL.md") {
  const catalog = await listNativeSkills(projectRoot, false);
  const selected = catalog.skills.find(skill => path.resolve(skill.path) === path.resolve(skillPath));
  if (!selected) throw new Error("skill_path is not present in native Codex skills/list");
  const target = skillResourcePath(selected.path, resource);
  const text = readTextBounded(target);
  return {
    skill: selected,
    resource,
    file: fileDigest(target),
    text
  };
}

function resolveEntryFile(target) {
  if (!fs.existsSync(target)) return null;
  if (fs.statSync(target).isFile()) return target;
  for (const name of ["llms.txt", "README.md", "SKILL.md", "data_structure.md"]) {
    const candidate = path.join(target, name);
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

export function resolveProjectKnowledge(projectRoot) {
  const root = ensureProjectRoot(projectRoot);
  const canonicalRoot = fs.realpathSync(root);
  const manifestPath = path.join(root, "reuse-manifest.json");
  if (!fs.existsSync(manifestPath)) {
    return { project_root: root, manifest: null, knowledge_paths: {}, entries: [] };
  }
  const manifestText = readTextBounded(manifestPath);
  const manifest = JSON.parse(manifestText);
  const knowledgePaths = manifest.knowledge_paths || {};
  const entries = Object.entries(knowledgePaths).map(([key, relative]) => {
    const absolute = path.resolve(root, relative);
    const lexicalInside = absolute === root || absolute.startsWith(root + path.sep);
    const targetExists = lexicalInside && fs.existsSync(absolute);
    let canonicalTarget = null;
    let insideRoot = lexicalInside;
    if (targetExists) {
      canonicalTarget = fs.realpathSync(absolute);
      insideRoot = canonicalTarget === canonicalRoot
        || canonicalTarget.startsWith(canonicalRoot + path.sep);
    }
    const entryFile = insideRoot && canonicalTarget ? resolveEntryFile(canonicalTarget) : null;
    return {
      key,
      relative_path: relative,
      absolute_path: absolute,
      inside_project: insideRoot,
      exists: Boolean(insideRoot && targetExists),
      entry_file: entryFile,
      entry_sha256: entryFile ? fileDigest(entryFile).sha256 : null
    };
  });
  return {
    project_root: root,
    manifest: { ...fileDigest(manifestPath), profile: manifest.profile, schema_version: manifest.schema_version },
    knowledge_paths: knowledgePaths,
    entries
  };
}

export function readGlobalAgents() {
  const filePath = path.join(CODEX_HOME, "AGENTS.md");
  if (!fs.existsSync(filePath)) return null;
  return { ...fileDigest(filePath), text: readTextBounded(filePath) };
}

export function globalAgentsInfo() {
  const filePath = path.join(CODEX_HOME, "AGENTS.md");
  if (!fs.existsSync(filePath)) return null;
  return fileDigest(filePath);
}

function listDirectUserSkillFiles() {
  const root = path.join(CODEX_HOME, "skills");
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && !entry.name.startsWith("."))
    .map(entry => path.join(root, entry.name, "SKILL.md"))
    .filter(filePath => fs.existsSync(filePath))
    .map(filePath => fileDigest(filePath));
}

function findPonytailState() {
  const dataRoot = path.join(CODEX_HOME, "plugins", "data");
  if (!fs.existsSync(dataRoot)) return { mode: null, path: null };
  for (const name of fs.readdirSync(dataRoot)) {
    if (!name.toLowerCase().includes("ponytail")) continue;
    const statePath = path.join(dataRoot, name, ".ponytail-active");
    if (fs.existsSync(statePath)) {
      return { mode: fs.readFileSync(statePath, "utf8").trim() || null, path: statePath };
    }
  }
  return { mode: null, path: null };
}

function hookPluginEnv(hook) {
  const env = { ...process.env };
  if (!hook.pluginId || !hook.sourcePath) return env;
  const normalized = path.resolve(hook.sourcePath);
  const hooksDir = path.dirname(normalized);
  const pluginRoot = path.basename(hooksDir) === "hooks" ? path.dirname(hooksDir) : path.dirname(normalized);
  env.CLAUDE_PLUGIN_ROOT = pluginRoot;
  const dataRoot = path.join(CODEX_HOME, "plugins", "data");
  if (fs.existsSync(dataRoot)) {
    const wanted = hook.pluginId.replace(/[@/]/g, "-").toLowerCase();
    const names = fs.readdirSync(dataRoot);
    const exact = names.find(name => name.toLowerCase() === wanted);
    const fuzzy = names.find(name => wanted.includes(name.toLowerCase()) || name.toLowerCase().includes(wanted));
    const dataName = exact || fuzzy;
    if (dataName) env.PLUGIN_DATA = path.join(dataRoot, dataName);
  }
  return env;
}

function matcherAllows(hook, payload) {
  if (!hook.matcher) return true;
  let subject = "";
  if (hook.eventName === "sessionStart") subject = String(payload.source || "startup");
  else if (hook.eventName === "subagentStart") subject = String(payload.agent_type || "");
  else subject = String(payload.matcher_value || "");
  try { return new RegExp(hook.matcher, "i").test(subject); } catch { return false; }
}

function executeHookCommand(hook, payload, projectRoot) {
  return new Promise((resolve) => {
    const env = hookPluginEnv(hook);
    env.CODEX_HOME = CODEX_HOME;
    env.CLAUDE_PROJECT_DIR = projectRoot;
    const child = spawn("/bin/sh", ["-lc", hook.command], {
      cwd: projectRoot,
      env,
      stdio: ["pipe", "pipe", "pipe"]
    });
    const chunks = [];
    const errors = [];
    let size = 0;
    const max = 128 * 1024;
    const collect = (target, chunk) => {
      if (size >= max) return;
      const part = Buffer.from(chunk).subarray(0, max - size);
      target.push(part);
      size += part.length;
    };
    child.stdout.on("data", chunk => collect(chunks, chunk));
    child.stderr.on("data", chunk => collect(errors, chunk));
    let timedOut = false;
    const timeoutMs = Math.max(1, Number(hook.timeoutSec || 5)) * 1000;
    const timer = setTimeout(() => {
      timedOut = true;
      try { child.kill("SIGTERM"); } catch {}
    }, timeoutMs);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      const stdout = Buffer.concat(chunks).toString("utf8");
      const stderr = Buffer.concat(errors).toString("utf8");
      let parsed = null;
      try { parsed = stdout.trim() ? JSON.parse(stdout) : null; } catch {}
      const additionalContext = parsed?.hookSpecificOutput?.additionalContext
        ?? (parsed ? null : (stdout.trim() || null));
      resolve({
        key: hook.key,
        event_name: hook.eventName,
        code,
        signal,
        timed_out: timedOut,
        system_message: parsed?.systemMessage || null,
        additional_context: additionalContext,
        stderr: stderr.trim() || null
      });
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

async function dispatchHookEventFromRegistry(root, eventName, payload, registry) {
  if (!EVENT_NAMES.has(eventName)) throw new Error(`unsupported hook event: ${eventName}`);
  const matching = registry.hooks
    .filter(hook => hook.eventName === eventName)
    .filter(hook => hook.enabled && TRUSTED_HOOK_STATUSES.has(hook.trustStatus))
    .filter(hook => matcherAllows(hook, payload));
  const results = [];
  const skipped = registry.hooks
    .filter(hook => hook.eventName === eventName && !matching.includes(hook))
    .map(hook => ({
      key: hook.key,
      reason: !hook.enabled ? "disabled"
        : !TRUSTED_HOOK_STATUSES.has(hook.trustStatus) ? `trust:${hook.trustStatus}`
        : !matcherAllows(hook, payload) ? "matcher" : "not-selected"
    }));
  for (const hook of matching) {
    if (hook.handlerType !== "command") {
      skipped.push({ key: hook.key, reason: `handler:${hook.handlerType}` });
      continue;
    }
    results.push(await executeHookCommand(hook, {
      ...payload,
      cwd: root,
      hook_event_name: eventName
    }, root));
  }
  return { event_name: eventName, results, skipped, warnings: registry.warnings, errors: registry.errors };
}

export async function dispatchHookEvent(projectRoot, eventName, payload = {}) {
  const root = ensureProjectRoot(projectRoot);
  const registry = await listNativeHooks(root);
  return dispatchHookEventFromRegistry(root, eventName, payload, registry);
}

function directUserSkillCatalog(skills) {
  const directRoot = path.join(CODEX_HOME, "skills");
  return skills
    .filter(skill => path.dirname(path.dirname(path.resolve(skill.path))) === directRoot)
    .map(skill => ({
      name: skill.name,
      description: skill.description,
      path: skill.path,
      enabled: skill.enabled
    }));
}


function compactNativeSkillCatalog(skills) {
  return skills.map(skill => ({
    name: skill.name,
    description: typeof skill.description === "string" && skill.description.length > 240
      ? skill.description.slice(0, 240) + "…"
      : (skill.description || null),
    path: skill.path,
    scope: skill.scope,
    enabled: skill.enabled,
    plugin_id: skill.pluginId || null
  }));
}

function compactHook(hook) {
  return {
    key: hook.key,
    event_name: hook.eventName,
    source: hook.source,
    plugin_id: hook.pluginId || null,
    enabled: hook.enabled,
    trust_status: hook.trustStatus,
    current_hash: hook.currentHash
  };
}

function digestIfReadable(filePath) {
  try {
    return filePath && fs.existsSync(filePath) ? fileDigest(filePath).sha256 : null;
  } catch {
    return null;
  }
}

export async function bootstrapContext(projectRoot, {
  phase = "resume",
  prompt = null,
  expectedFingerprint = null,
  instructionFingerprint = null
} = {}) {
  const root = ensureProjectRoot(projectRoot);
  return withCodexAppServer(root, async ({ request }) => {
    // One initialized app-server snapshot per bootstrap. Independent RPCs may
    // overlap, but they share one connection/process and therefore one registry
    // generation instead of racing multiple cold app-server instances.
    const [skillsResult, hooksResult, configResult, threadResult] = await Promise.all([
      request("skills/list", { cwds: [root], forceReload: true }),
      request("hooks/list", { cwds: [root] }),
      request("config/read", { cwd: root, includeLayers: false }),
      request("thread/start", { cwd: root, ephemeral: true }, 30000)
    ]);
    const skills = skillsFromListResult(root, skillsResult);
    const hooks = hooksFromListResult(root, hooksResult);
    const runtimeConfig = runtimeConfigFromResult(configResult);
    const permissionProfile = permissionProfileFromThreadStart(threadResult);
    const globalAgents = globalAgentsInfo();
    const knowledge = resolveProjectKnowledge(root);
    const projectAgentsPath = path.join(root, "AGENTS.md");
    const projectAgents = fs.existsSync(projectAgentsPath) ? fileDigest(projectAgentsPath) : null;
    const ponytail = findPonytailState();
    const sessionHook = phase
      ? await dispatchHookEventFromRegistry(root, "sessionStart", { source: phase }, hooks)
      : null;
    const promptHook = prompt !== null && prompt !== undefined
      ? await dispatchHookEventFromRegistry(root, "userPromptSubmit", { prompt: String(prompt) }, hooks)
      : null;
    const fingerprintInput = JSON.stringify({
      global_agents: globalAgents?.sha256 || null,
      project_agents: projectAgents?.sha256 || null,
      instruction_fingerprint: instructionFingerprint || null,
      manifest: knowledge.manifest?.sha256 || null,
      knowledge_entries: knowledge.entries.map(entry => [entry.key, entry.entry_sha256, entry.inside_project]),
      skills: skills.skills.map(skill => [skill.path, skill.enabled, digestIfReadable(skill.path)]),
      hooks: hooks.hooks.map(hook => [hook.key, hook.currentHash, hook.enabled, hook.trustStatus]),
      ponytail_mode: ponytail.mode,
      runtime_config: runtimeConfig,
      permission_profile: permissionProfile
    });
    const fingerprint = "sha256:" + sha256(fingerprintInput);
    return {
      bridge_version: BRIDGE_VERSION,
      project_root: root,
      codex_home: CODEX_HOME,
      fingerprint,
      stale: expectedFingerprint ? expectedFingerprint !== fingerprint : false,
      global_agents: globalAgents,
      project_agents: projectAgents,
      native_skills: {
        count: skills.skills.length,
        catalog: compactNativeSkillCatalog(skills.skills),
        direct_user_count: directUserSkillCatalog(skills.skills).length,
        direct_user_skills: directUserSkillCatalog(skills.skills),
        errors: skills.errors
      },
      native_hooks: {
        count: hooks.hooks.length,
        hooks: hooks.hooks.map(compactHook),
        warnings: hooks.warnings,
        errors: hooks.errors
      },
      native_runtime: runtimeConfig,
      native_permission_profile: permissionProfile,
      ponytail,
      knowledge,
      lifecycle: {
        session_start: sessionHook,
        user_prompt_submit: promptHook,
        semantics: "bridge_approximation"
      },
      required_followups: [
        "read_user_agents",
        "route against native_skills.catalog and read_native_skill only for the matching Skill",
        "resolve_project_knowledge before reading knowledge bodies"
      ]
    };
  });
}

function sessionIndexEntries() {
  const indexPath = path.join(CODEX_HOME, "session_index.jsonl");
  if (!fs.existsSync(indexPath)) return [];
  return fs.readFileSync(indexPath, "utf8").split(/\r?\n/).filter(Boolean).flatMap(line => {
    try { return [JSON.parse(line)]; } catch { return []; }
  });
}

function findRollout(sessionId) {
  const root = path.join(CODEX_HOME, "sessions");
  if (!fs.existsSync(root)) return null;
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const target = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(target);
      else if (entry.isFile() && entry.name.includes(sessionId) && entry.name.endsWith(".jsonl")) return target;
    }
  }
  return null;
}

export function codexThreadSummary(sessionQuery, limit = 60) {
  const matches = sessionIndexEntries().filter(item =>
    item.id === sessionQuery || item.id?.startsWith(sessionQuery) || item.thread_name === sessionQuery
  );
  if (matches.length !== 1) {
    return { query: sessionQuery, matches: matches.slice(0, 20), ambiguous: matches.length > 1 };
  }
  const session = matches[0];
  const rollout = findRollout(session.id);
  const records = [];
  if (rollout) {
    const lines = fs.readFileSync(rollout, "utf8").split(/\r?\n/).filter(Boolean);
    for (const line of lines) {
      let row;
      try { row = JSON.parse(line); } catch { continue; }
      const payload = row.payload || {};
      if (row.type === "response_item" && payload.type === "message" && ["user", "assistant"].includes(payload.role)) {
        const text = (payload.content || []).map(c => c.text || c.input_text || c.output_text || "").filter(Boolean).join("\n");
        if (text) records.push({ timestamp: row.timestamp, kind: "message", role: payload.role, phase: payload.phase || null, text });
      } else if (row.type === "event_msg" && ["task_complete", "turn_aborted"].includes(payload.type)) {
        records.push({ timestamp: row.timestamp, kind: payload.type, error: payload.error || null });
      } else if (row.type === "response_item" && ["custom_tool_call", "function_call"].includes(payload.type)) {
        records.push({ timestamp: row.timestamp, kind: "tool_call", name: payload.name || null });
      }
    }
  }
  const boundedLimit = Math.max(1, Math.min(Number(limit) || 12, 20));
  const boundedRecords = records.slice(-boundedLimit).map(record => ({
    ...record,
    text: typeof record.text === "string" && record.text.length > 1200
      ? record.text.slice(0, 1200) + "\n…[truncated by WebCodex bridge]"
      : record.text
  }));
  return {
    session,
    rollout,
    records_total: records.length,
    records: boundedRecords
  };
}

export async function contextParityCheck(projectRoot, expectedFingerprint = null) {
  const root = ensureProjectRoot(projectRoot);
  const [skills, hooks] = await Promise.all([listNativeSkills(root, true), listNativeHooks(root)]);
  const directUserSkills = listDirectUserSkillFiles();
  const nativePaths = new Set(skills.skills.map(skill => path.resolve(skill.path)));
  const missingDirectSkills = directUserSkills.filter(file => !nativePaths.has(path.resolve(file.path)));
  const untrustedEnabledHooks = hooks.hooks.filter(hook =>
    hook.enabled && !TRUSTED_HOOK_STATUSES.has(hook.trustStatus)
  );
  const bootstrap = await bootstrapContext(root, { phase: null, expectedFingerprint });
  const machineEntries = bootstrap.knowledge.entries.filter(entry => entry.key.endsWith("_machine_entry"));
  const missingMachineEntries = machineEntries.filter(entry => !entry.exists || !entry.entry_file);
  const checks = {
    global_agents: Boolean(bootstrap.global_agents),
    direct_user_skills_discovered_by_native_codex: missingDirectSkills.length === 0,
    enabled_hooks_trusted: untrustedEnabledHooks.length === 0,
    reuse_manifest: Boolean(bootstrap.knowledge.manifest),
    machine_entries_resolvable: missingMachineEntries.length === 0
  };
  return {
    status: Object.values(checks).every(Boolean) ? "pass" : "warn",
    checks,
    fingerprint: bootstrap.fingerprint,
    stale: bootstrap.stale,
    native_skill_count: skills.skills.length,
    direct_user_skill_count: directUserSkills.length,
    missing_direct_skills: missingDirectSkills,
    hook_count: hooks.hooks.length,
    untrusted_enabled_hooks: untrustedEnabledHooks,
    native_runtime: bootstrap.native_runtime,
    native_permission_profile: bootstrap.native_permission_profile,
    missing_machine_entries: missingMachineEntries,
    limitation: "Runner MCP cannot itself intercept ChatGPT host lifecycle; project/global AGENTS must require bridge lifecycle calls unless WebCodex host gains a pre-prompt integration point."
  };
}
