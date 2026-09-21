import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const PACKAGE = JSON.parse(
  fs.readFileSync(new URL("./package.json", import.meta.url), "utf8")
);

export const BRIDGE_VERSION = String(PACKAGE.version);
export const READINESS_SCHEMA_VERSION = "webcodex.native-context-readiness.v1";
const DEFAULT_TIMEOUT_MS = 3000;
const MAX_TIMEOUT_MS = 10000;
const MCP_PROTOCOL_VERSION = "2025-06-18";

function boundedTimeout(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_TIMEOUT_MS;
  return Math.max(250, Math.min(Math.trunc(parsed), MAX_TIMEOUT_MS));
}

function readinessResult(status, reason, nextAction, extra = {}) {
  return {
    schema_version: READINESS_SCHEMA_VERSION,
    status,
    reason,
    owner: "codex_reference",
    impact: "native_context_only",
    next_action: nextAction,
    observed_at_ms: Date.now(),
    source_version: BRIDGE_VERSION,
    native_model_turns: 0,
    ...extra
  };
}

function withDeadline(promise, deadlineAt, label) {
  const remaining = Math.max(1, deadlineAt - Date.now());
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(Object.assign(new Error(label), { code: "READINESS_TIMEOUT" })),
      remaining
    );
    Promise.resolve(promise).then(
      value => {
        clearTimeout(timer);
        resolve(value);
      },
      error => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function bundledProviderServer() {
  try {
    const bridgeDir = fs.realpathSync(path.dirname(fileURLToPath(import.meta.url)));
    const server = path.join(bridgeDir, "server.mjs");
    const metadata = fs.lstatSync(server);
    if (!metadata.isFile() || metadata.isSymbolicLink()) return null;
    const canonical = fs.realpathSync(server);
    return path.dirname(canonical) === bridgeDir ? canonical : null;
  } catch {
    return null;
  }
}

async function probeBundledProvider({ resolution, root, env, deadlineAt }) {
  const server = bundledProviderServer();
  if (!server) {
    return {
      ok: false,
      status: "unavailable",
      reason: "bundled_bridge_invalid",
      owner: "codex_context_provider",
      next_action: "reinstall_webcodex"
    };
  }

  const providerEnv = codexChildEnvironment(resolution.canonical_path, env);
  providerEnv.WEBCODEX_PROJECT_ROOT = root;
  let child;
  try {
    child = spawn(process.execPath, [server], {
      cwd: root,
      env: providerEnv,
      stdio: ["pipe", "pipe", "pipe"]
    });
  } catch {
    return {
      ok: false,
      status: "unavailable",
      reason: "bridge_provider_spawn_failed",
      owner: "codex_context_provider",
      next_action: "reinstall_webcodex"
    };
  }

  let stderr = "";
  let probeComplete = false;
  const processFailure = new Promise((_, reject) => {
    child.once("error", error => {
      reject(Object.assign(error, { code: "READINESS_SPAWN_FAILED" }));
    });
    child.once("exit", (code, signal) => {
      if (probeComplete) return;
      reject(Object.assign(
        new Error(`Context Bridge exited before readiness completed (code=${code}, signal=${signal})`),
        { code: "READINESS_PROCESS_EXITED" }
      ));
    });
  });
  child.stderr.on("data", chunk => {
    stderr = (stderr + String(chunk)).slice(-4000);
  });
  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  const pending = new Map();
  rl.on("line", line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || !Object.prototype.hasOwnProperty.call(message, "id")) return;
    const waiter = pending.get(String(message.id));
    if (!waiter) return;
    pending.delete(String(message.id));
    if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
    else waiter.resolve(message.result);
  });
  const request = (id, method, params) => new Promise((resolve, reject) => {
    pending.set(String(id), { resolve, reject });
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
  const bounded = (promise, label) => withDeadline(
    Promise.race([promise, processFailure]),
    deadlineAt,
    label
  );

  try {
    const initialized = await bounded(
      request(1, "initialize", {
        protocolVersion: MCP_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "webcodex-readiness", version: BRIDGE_VERSION }
      }),
      "Context Bridge initialize timed out"
    );
    if (
      initialized?.serverInfo?.name !== "webcodex-codex-context-bridge"
      || initialized?.serverInfo?.version !== BRIDGE_VERSION
    ) {
      throw Object.assign(new Error("Context Bridge initialize identity mismatch"), {
        code: "READINESS_INCOMPATIBLE"
      });
    }
    child.stdin.write(JSON.stringify({
      jsonrpc: "2.0",
      method: "notifications/initialized",
      params: {}
    }) + "\n");
    const listed = await bounded(
      request(2, "tools/list", {}),
      "Context Bridge tools/list timed out"
    );
    const names = new Set((listed?.tools || []).map(tool => tool?.name).filter(Boolean));
    if (!names.has("list_native_skills") || !names.has("read_user_agents")) {
      throw Object.assign(new Error("Context Bridge required readiness tools are missing"), {
        code: "READINESS_INCOMPATIBLE"
      });
    }
    const call = await bounded(
      request(3, "tools/call", { name: "read_user_agents", arguments: {} }),
      "Context Bridge read_user_agents timed out"
    );
    if (!call || call.isError === true || !Array.isArray(call.content)) {
      throw Object.assign(new Error("Context Bridge read_user_agents probe failed"), {
        code: "READINESS_INCOMPATIBLE"
      });
    }
    probeComplete = true;
    return { ok: true };
  } catch (error) {
    probeComplete = true;
    const timedOut = error?.code === "READINESS_TIMEOUT";
    const spawnFailed = error?.code === "READINESS_SPAWN_FAILED";
    return {
      ok: false,
      status: timedOut ? "degraded" : "unavailable",
      reason: spawnFailed
        ? "bridge_provider_spawn_failed"
        : timedOut
          ? "bridge_provider_timeout"
          : "bridge_provider_incompatible",
      owner: "codex_context_provider",
      next_action: spawnFailed
        ? "reinstall_webcodex"
        : timedOut
          ? "retry_native_context"
          : "reinstall_webcodex",
      diagnostic: String(error?.message || error).slice(0, 500),
      stderr: stderr.trim().slice(-500) || null
    };
  } finally {
    probeComplete = true;
    pending.clear();
    try { rl.close(); } catch {}
    try { child.stdin.end(); } catch {}
    try { child.kill("SIGTERM"); } catch {}
  }
}

function finalExecutableIsTrusted(candidate) {
  const stat = fs.statSync(candidate);
  if (!stat.isFile()) return false;
  fs.accessSync(candidate, fs.constants.X_OK);
  if (typeof process.getuid === "function" && typeof stat.uid === "number") {
    const uid = process.getuid();
    if (stat.uid !== uid && stat.uid !== 0) return false;
  }
  if (
    process.platform !== "win32"
    && typeof stat.mode === "number"
    && (stat.mode & 0o022) !== 0
  ) return false;
  return true;
}

function candidatePaths(env, home) {
  const explicit = env.CODEX_BIN?.trim();
  if (explicit) return { explicit: true, candidates: [explicit] };
  const executable = process.platform === "win32" ? "codex.exe" : "codex";
  const candidates = [
    path.join(home, ".local", "bin", executable),
    ...(process.platform === "darwin"
      ? ["/usr/local/bin/codex", "/opt/homebrew/bin/codex"]
      : []),
    ...(String(env.PATH || "")
      .split(path.delimiter)
      .filter(entry => entry && path.isAbsolute(entry))
      .map(entry => path.join(entry, executable)))
  ];
  return { explicit: false, candidates: [...new Set(candidates)] };
}

export function resolveCodexExecutable({
  env = process.env,
  home = env.HOME || os.homedir()
} = {}) {
  const { explicit, candidates } = candidatePaths(env, home);
  let invalidObserved = false;
  for (const requested of candidates) {
    if (!path.isAbsolute(requested)) {
      invalidObserved = true;
      if (explicit) break;
      continue;
    }
    try {
      const canonical = fs.realpathSync(requested);
      if (!finalExecutableIsTrusted(canonical)) {
        invalidObserved = true;
        if (explicit) break;
        continue;
      }
      return readinessResult("ready", "codex_reference_resolved", "none", {
        requested_path: requested,
        canonical_path: canonical,
        requested_was_symlink: path.resolve(requested) !== path.resolve(canonical)
      });
    } catch {
      if (fs.existsSync(requested)) invalidObserved = true;
      if (explicit) break;
    }
  }
  return readinessResult(
    "unavailable",
    invalidObserved || explicit ? "codex_reference_invalid" : "codex_reference_missing",
    invalidObserved || explicit ? "repair_codex_cli" : "install_codex_cli",
    {
      requested_path: explicit ? candidates[0] || null : null,
      canonical_path: null,
      requested_was_symlink: false
    }
  );
}

export function codexChildEnvironment(canonicalPath, baseEnv = process.env) {
  const environment = {};
  const home = baseEnv.HOME || os.homedir();
  if (home) environment.HOME = home;
  if (baseEnv.CODEX_HOME) environment.CODEX_HOME = baseEnv.CODEX_HOME;
  for (const key of ["TMPDIR", "TMP", "TEMP"]) {
    if (baseEnv[key]) environment[key] = baseEnv[key];
  }
  if (process.platform === "win32" && baseEnv.SYSTEMROOT) {
    environment.SYSTEMROOT = baseEnv.SYSTEMROOT;
  }
  const nodeDir = path.dirname(process.execPath);
  const systemDirs = process.platform === "win32"
    ? []
    : ["/usr/bin", "/bin", "/usr/sbin", "/sbin"];
  environment.PATH = [...new Set([nodeDir, ...systemDirs])].join(path.delimiter);
  environment.CODEX_BIN = canonicalPath;
  return environment;
}

function probeRoot(env) {
  for (const candidate of [
    env.WEBCODEX_PROJECT_ROOT,
    env.HOME,
    os.homedir(),
    process.cwd()
  ]) {
    if (!candidate) continue;
    try {
      if (fs.statSync(candidate).isDirectory()) return fs.realpathSync(candidate);
    } catch {}
  }
  return process.cwd();
}

export async function probeCodexReadiness({
  env = process.env,
  timeoutMs = DEFAULT_TIMEOUT_MS
} = {}) {
  const resolution = resolveCodexExecutable({ env });
  if (resolution.status !== "ready" || !resolution.canonical_path) return resolution;

  const timeout = boundedTimeout(timeoutMs);
  const deadlineAt = Date.now() + timeout;
  const root = probeRoot(env);
  let child;
  try {
    child = spawn(resolution.canonical_path, ["app-server", "--stdio"], {
      cwd: root,
      env: codexChildEnvironment(resolution.canonical_path, env),
      stdio: ["pipe", "pipe", "pipe"]
    });
  } catch {
    return readinessResult(
      "unavailable",
      "codex_app_server_spawn_failed",
      "repair_codex_cli",
      {
        canonical_path: resolution.canonical_path,
        requested_path: resolution.requested_path,
        requested_was_symlink: resolution.requested_was_symlink
      }
    );
  }

  let stderr = "";
  const spawnFailure = new Promise((_, reject) => {
    child.once("error", error => {
      reject(Object.assign(error, { code: "READINESS_SPAWN_FAILED" }));
    });
  });
  child.stderr.on("data", chunk => {
    stderr = (stderr + String(chunk)).slice(-4000);
  });
  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  const pending = new Map();
  rl.on("line", line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || !Object.prototype.hasOwnProperty.call(message, "id")) return;
    const waiter = pending.get(String(message.id));
    if (!waiter) return;
    pending.delete(String(message.id));
    if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
    else waiter.resolve(message.result);
  });
  const request = (id, method, params) => new Promise((resolve, reject) => {
    pending.set(String(id), { resolve, reject });
    child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
  try {
    await withDeadline(
      Promise.race([
        request(1, "initialize", {
          clientInfo: { name: "webcodex-readiness", version: BRIDGE_VERSION },
          capabilities: { experimentalApi: true }
        }),
        spawnFailure
      ]),
      deadlineAt,
      "Codex app-server initialize timed out"
    );
    child.stdin.write(JSON.stringify({ method: "initialized", params: {} }) + "\n");
    const skills = await withDeadline(
      Promise.race([
        request(2, "skills/list", { cwds: [root], forceReload: false }),
        spawnFailure
      ]),
      deadlineAt,
      "Codex app-server skills/list timed out"
    );
    if (!skills || !Array.isArray(skills.data)) {
      throw Object.assign(
        new Error("Codex app-server skills/list returned an incompatible payload"),
        { code: "READINESS_INCOMPATIBLE" }
      );
    }
    const provider = await probeBundledProvider({ resolution, root, env, deadlineAt });
    if (!provider.ok) {
      return readinessResult(
        provider.status,
        provider.reason,
        provider.next_action,
        {
          owner: provider.owner,
          canonical_path: resolution.canonical_path,
          requested_path: resolution.requested_path,
          requested_was_symlink: resolution.requested_was_symlink,
          diagnostic: provider.diagnostic || null,
          stderr: provider.stderr || null
        }
      );
    }
    return readinessResult("ready", "native_context_ready", "none", {
      owner: "codex_context_provider",
      canonical_path: resolution.canonical_path,
      requested_path: resolution.requested_path,
      requested_was_symlink: resolution.requested_was_symlink,
      provider_mcp_probe: true
    });
  } catch (error) {
    const timedOut = error?.code === "READINESS_TIMEOUT";
    const spawnFailed = error?.code === "READINESS_SPAWN_FAILED";
    return readinessResult(
      timedOut ? "degraded" : "unavailable",
      spawnFailed
        ? "codex_app_server_spawn_failed"
        : timedOut
          ? "codex_app_server_timeout"
          : "codex_app_server_incompatible",
      spawnFailed
        ? "repair_codex_cli"
        : timedOut
          ? "retry_native_context"
          : "update_codex_cli",
      {
        canonical_path: resolution.canonical_path,
        requested_path: resolution.requested_path,
        requested_was_symlink: resolution.requested_was_symlink,
        diagnostic: String(error?.message || error).slice(0, 500),
        stderr: stderr.trim().slice(-500) || null
      }
    );
  } finally {
    pending.clear();
    try { rl.close(); } catch {}
    try { child.stdin.end(); } catch {}
    try { child.kill("SIGTERM"); } catch {}
  }
}

async function main() {
  const timeoutIndex = process.argv.indexOf("--timeout-ms");
  const timeoutMs = timeoutIndex >= 0 ? process.argv[timeoutIndex + 1] : DEFAULT_TIMEOUT_MS;
  const payload = await probeCodexReadiness({ timeoutMs });
  process.stdout.write(JSON.stringify(payload) + "\n");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
