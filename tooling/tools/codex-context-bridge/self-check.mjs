#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { probeCodexReadiness, resolveCodexExecutable } from "./readiness.mjs";

const SELF = fileURLToPath(import.meta.url);

function reply(id, result) {
  process.stdout.write(JSON.stringify({ id, result }) + "\n");
}

async function fakeAppServer() {
  const log = process.env.FAKE_LOG;
  fs.appendFileSync(log, `start:${process.pid}\n`);
  let buffered = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    buffered += chunk;
    for (;;) {
      const newline = buffered.indexOf("\n");
      if (newline < 0) break;
      const line = buffered.slice(0, newline);
      buffered = buffered.slice(newline + 1);
      if (!line.trim()) continue;
      const message = JSON.parse(line);
      fs.appendFileSync(log, `method:${message.method}\n`);
      if (!Object.prototype.hasOwnProperty.call(message, "id")) continue;
      const root = process.env.WEBCODEX_PROJECT_ROOT;
      switch (message.method) {
        case "initialize":
          reply(message.id, { protocolVersion: "audit-fixture" });
          break;
        case "skills/list":
          reply(message.id, {
            data: [{
              cwd: root,
              skills: [{
                name: "demo-skill",
                description: "fixture skill",
                path: path.join(root, ".skills", "demo", "SKILL.md"),
                enabled: true,
                scope: "project"
              }],
              errors: []
            }]
          });
          break;
        case "hooks/list":
          reply(message.id, { data: [{ cwd: root, hooks: [], warnings: [], errors: [] }] });
          break;
        case "config/read":
          reply(message.id, { config: { model: "fixture", approval_policy: "on-request" } });
          break;
        case "thread/start":
          reply(message.id, {
            sandbox: "workspace-write",
            approvalPolicy: "on-request",
            model: "fixture",
            reasoningEffort: "low",
            activePermissionProfile: {}
          });
          break;
        case "mcpServerStatus/list":
          reply(message.id, {
            data: [{
              name: "fixture-mcp",
              authStatus: "ready",
              tools: [
                {
                  name: "alpha",
                  description: "first read-only fixture",
                  annotations: { readOnlyHint: true, destructiveHint: false },
                  inputSchema: { type: "object", properties: {} }
                },
                {
                  name: "beta",
                  description: "second read-only fixture",
                  annotations: { readOnlyHint: true, destructiveHint: false },
                  inputSchema: { type: "object", properties: {} }
                },
                {
                  name: "effect",
                  description: "allowed effectful fixture",
                  annotations: { readOnlyHint: false, destructiveHint: false },
                  inputSchema: { type: "object", properties: {} }
                },
                {
                  name: "danger",
                  description: "destructive fixture",
                  annotations: { readOnlyHint: false, destructiveHint: true },
                  inputSchema: { type: "object", properties: {} }
                }
              ]
            }]
          });
          break;
        case "thread/read":
          reply(message.id, {
            thread: {
              id: message.params?.threadId,
              sessionId: "fixture-session",
              cwd: root,
              path: path.join(root, "private-rollout.jsonl"),
              preview: "fixture task",
              ephemeral: false,
              modelProvider: "openai",
              model: "fixture",
              reasoningEffort: "low",
              createdAt: 1,
              updatedAt: 2,
              recencyAt: 2,
              status: { type: "idle" },
              cliVersion: "fixture",
              originator: "fixture",
              source: { type: "cli" },
              canAcceptDirectInput: true,
              name: "fixture thread"
            }
          });
          break;
        case "thread/items/list":
          reply(message.id, {
            data: [
              {
                turnId: "turn-2",
                item: {
                  id: "item-tool",
                  type: "mcpToolCall",
                  server: "fixture-mcp",
                  tool: "alpha",
                  status: "completed",
                  arguments: { secret: "DO_NOT_EXPOSE" },
                  result: { content: "DO_NOT_EXPOSE" },
                  readOnlyHint: true,
                  durationMs: 12
                }
              },
              {
                turnId: "turn-1",
                item: {
                  id: "item-user",
                  type: "userMessage",
                  content: [{ type: "text", text: "resume fixture" }]
                }
              }
            ],
            nextCursor: "cursor-next",
            backwardsCursor: null
          });
          break;
        case "command/exec":
          assert.deepEqual(message.params?.command, ["git", "status", "--short"]);
          assert.equal(message.params?.cwd, fs.realpathSync(root));
          assert.deepEqual(message.params?.sandboxPolicy, {
            type: "readOnly",
            networkAccess: false
          });
          assert.equal(message.params?.tty, false);
          assert.equal(message.params?.streamStdin, false);
          assert.equal(message.params?.streamStdoutStderr, false);
          reply(message.id, {
            exitCode: 0,
            stdout: "native-host-ok\n",
            stderr: ""
          });
          break;
        default:
          process.stdout.write(JSON.stringify({
            id: message.id,
            error: { code: -32601, message: `unsupported fixture method ${message.method}` }
          }) + "\n");
      }
    }
  }
}

async function contractChild() {
  const bridge = await import(`./bridge-lib.mjs?pid=${process.pid}`);
  const result = await bridge.bootstrapContext(process.env.WEBCODEX_PROJECT_ROOT, {
    phase: null,
    prompt: null,
    expectedFingerprint: process.env.EXPECTED_FINGERPRINT || null,
    instructionFingerprint: "sha256:" + "1".repeat(64)
  });
  process.stdout.write(JSON.stringify(result) + "\n");
}

async function nativeHostChild() {
  const bridge = await import("./bridge-lib.mjs?native-host-pid=" + process.pid);
  const result = await bridge.nativeHostExecReadOnly(process.env.WEBCODEX_PROJECT_ROOT, {
    executable: "git",
    args: ["status", "--short"],
    cwd: ".",
    timeoutMs: 3000
  });
  process.stdout.write(JSON.stringify(result) + "\n");
}

async function nativeDiscoveryChild() {
  const bridge = await import("./bridge-lib.mjs?native-discovery-pid=" + process.pid);
  const mcp = await bridge.searchNativeMcpTools(process.env.WEBCODEX_PROJECT_ROOT, {
    readOnlyOnly: true,
    offset: 1,
    limit: 1
  });
  const allowedMcp = await bridge.searchNativeMcpTools(process.env.WEBCODEX_PROJECT_ROOT, {
    readOnlyOnly: false,
    limit: 10
  });
  const thread = await bridge.nativeThreadRead(process.env.WEBCODEX_PROJECT_ROOT, {
    session: "thread-fixture",
    limit: 2
  });
  process.stdout.write(JSON.stringify({ mcp, allowedMcp, thread }) + "\n");
}

function runChild(env) {
  const child = spawnSync(process.execPath, [SELF, "--contract-child"], {
    env,
    encoding: "utf8",
    timeout: 20000
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const lines = child.stdout.trim().split("\n").filter(Boolean);
  return JSON.parse(lines.at(-1));
}

function runNativeHostChild(env) {
  const child = spawnSync(process.execPath, [SELF, "--native-host-child"], {
    env,
    encoding: "utf8",
    timeout: 20000
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const lines = child.stdout.trim().split("\n").filter(Boolean);
  return JSON.parse(lines.at(-1));
}

function runNativeDiscoveryChild(env) {
  const child = spawnSync(process.execPath, [SELF, "--native-discovery-child"], {
    env,
    encoding: "utf8",
    timeout: 20000
  });
  assert.equal(child.status, 0, child.stderr || child.stdout);
  const lines = child.stdout.trim().split("\n").filter(Boolean);
  return JSON.parse(lines.at(-1));
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "webcodex-context-bridge-"));
  try {
    const project = path.join(temp, "project");
    const outside = path.join(temp, "outside.txt");
    const codexHome = path.join(temp, "codex-home");
    const commandSuffix = process.platform === "win32" ? ".cmd" : "";
    const fakeCodexTarget = path.join(temp, `fake-codex-target${commandSuffix}`);
    const fakeCodex = path.join(temp, `fake-codex${commandSuffix}`);
    const log = path.join(temp, "fake.log");
    fs.mkdirSync(path.join(project, ".skills", "demo"), { recursive: true });
    fs.mkdirSync(path.join(project, "docs"), { recursive: true });
    fs.mkdirSync(codexHome, { recursive: true });
    fs.writeFileSync(path.join(codexHome, "session_index.jsonl"), JSON.stringify({
      id: "thread-fixture",
      thread_name: "fixture thread"
    }) + "\n");
    fs.writeFileSync(path.join(project, ".skills", "demo", "SKILL.md"), "# demo\nfirst body\n");
    fs.writeFileSync(path.join(project, "docs", "README.md"), "# knowledge\n");
    fs.writeFileSync(outside, "outside-only\n");
    fs.symlinkSync(outside, path.join(project, "linked.txt"));
    fs.writeFileSync(path.join(project, "reuse-manifest.json"), JSON.stringify({
      schema_version: "fixture",
      profile: "fixture",
      knowledge_paths: { docs: "docs/README.md", escape: "linked.txt" }
    }));
    const fakeCodexScript = process.platform === "win32"
      ? [
          "@echo off",
          `set "WEBCODEX_PROJECT_ROOT=${project.replaceAll("%", "%%").replaceAll('"', '""')}"`,
          `set "FAKE_LOG=${log.replaceAll("%", "%%").replaceAll('"', '""')}"`,
          `"${process.execPath.replaceAll("%", "%%").replaceAll('"', '""')}" "${SELF.replaceAll("%", "%%").replaceAll('"', '""')}" --fake-app-server %*`,
          ""
        ]
      : [
          "#!/bin/sh",
          "export WEBCODEX_PROJECT_ROOT=" + JSON.stringify(project),
          "export FAKE_LOG=" + JSON.stringify(log),
          "exec " + JSON.stringify(process.execPath) + " " + JSON.stringify(SELF) + ' --fake-app-server "$@"',
          ""
        ];
    fs.writeFileSync(fakeCodexTarget, fakeCodexScript.join("\n"));
    fs.chmodSync(fakeCodexTarget, 0o755);
    fs.symlinkSync(fakeCodexTarget, fakeCodex);

    const baseEnv = {
      ...process.env,
      CODEX_BIN: fakeCodex,
      CODEX_HOME: codexHome,
      WEBCODEX_PROJECT_ROOT: project,
      FAKE_LOG: log
    };
    const resolved = resolveCodexExecutable({ env: baseEnv });
    assert.equal(resolved.status, "ready");
    assert.equal(resolved.canonical_path, fs.realpathSync(fakeCodexTarget));
    assert.equal(resolved.requested_was_symlink, true);
    const invalid = resolveCodexExecutable({
      env: { ...baseEnv, CODEX_BIN: path.join(temp, "missing-codex") }
    });
    assert.equal(invalid.status, "unavailable");
    assert.equal(invalid.reason, "codex_reference_invalid");
    const readinessAlias = path.join(temp, "readiness-alias.mjs");
    fs.symlinkSync(fileURLToPath(new URL("./readiness.mjs", import.meta.url)), readinessAlias);
    const readinessCli = spawnSync(
      process.execPath,
      [readinessAlias, "--json", "--timeout-ms", "3000"],
      {
        env: { ...baseEnv, CODEX_BIN: path.join(temp, "missing-codex") },
        encoding: "utf8",
        timeout: 10000
      }
    );
    assert.equal(readinessCli.status, 0, readinessCli.stderr || readinessCli.stdout);
    const readinessCliPayload = JSON.parse(readinessCli.stdout);
    assert.equal(readinessCliPayload.status, "unavailable");
    assert.equal(readinessCliPayload.reason, "codex_reference_invalid");
    assert.equal(readinessCliPayload.native_model_turns, 0);
    const first = runChild(baseEnv);
    assert.equal(first.bridge_version, "0.6.1");
    assert.equal(first.stale, false);
    assert.match(first.fingerprint, /^sha256:[a-f0-9]{64}$/);
    const escape = first.knowledge.entries.find(entry => entry.key === "escape");
    assert.equal(escape.inside_project, false);
    assert.equal(escape.exists, false);
    assert.equal(escape.entry_file, null);
    assert.equal(escape.entry_sha256, null);

    let methods = fs.readFileSync(log, "utf8").trim().split("\n");
    assert.equal(methods.filter(line => line.startsWith("start:")).length, 1);
    assert.equal(methods.filter(line => line === "method:initialized").length, 1);

    fs.writeFileSync(path.join(project, ".skills", "demo", "SKILL.md"), "# demo\nsecond body\n");
    const second = runChild({ ...baseEnv, EXPECTED_FINGERPRINT: first.fingerprint });
    assert.equal(second.stale, true);
    assert.notEqual(second.fingerprint, first.fingerprint);

    methods = fs.readFileSync(log, "utf8").trim().split("\n");
    assert.equal(methods.filter(line => line.startsWith("start:")).length, 2);
    assert.equal(methods.filter(line => line === "method:initialized").length, 2);

    const nativeHost = runNativeHostChild(baseEnv);
    assert.equal(nativeHost.host_adapter, "codex_app_server");
    assert.equal(nativeHost.method, "command/exec");
    assert.deepEqual(nativeHost.sandbox, { type: "readOnly", network_access: false });
    assert.equal(nativeHost.cwd, ".");
    assert.equal(nativeHost.exit_code, 0);
    assert.equal(nativeHost.stdout, "native-host-ok\n");
    assert.equal(nativeHost.stderr, "");
    assert.equal(nativeHost.quota_mode, "zero_codex_model_turn");
    assert.equal(nativeHost.model_turn_started, false);
    assert.equal(nativeHost.state_changed, false);
    methods = fs.readFileSync(log, "utf8").trim().split("\n");
    assert.equal(methods.filter(line => line === "method:command/exec").length, 1);
    assert.equal(methods.filter(line => line === "method:thread/start").length, 2);

    const nativeDiscovery = runNativeDiscoveryChild(baseEnv);
    assert.equal(nativeDiscovery.mcp.total_matches, 2);
    assert.equal(nativeDiscovery.mcp.offset, 1);
    assert.equal(nativeDiscovery.mcp.returned_count, 1);
    assert.equal(nativeDiscovery.mcp.tools[0].name, "beta");
    assert.equal(nativeDiscovery.mcp.next_offset, null);
    assert.equal(nativeDiscovery.mcp.model_turn_started, false);
    assert.equal(nativeDiscovery.allowedMcp.total_matches, 3);
    assert.deepEqual(
      nativeDiscovery.allowedMcp.tools.map(tool => tool.name),
      ["alpha", "beta", "effect"]
    );
    assert.equal(
      nativeDiscovery.allowedMcp.tools.some(tool => tool.policy_class === "destructive"),
      false
    );
    assert.equal(nativeDiscovery.thread.resolved, true);
    assert.equal(nativeDiscovery.thread.thread.id, "thread-fixture");
    assert.equal(nativeDiscovery.thread.items.length, 2);
    assert.equal(nativeDiscovery.thread.items[0].tool, "alpha");
    assert.equal(nativeDiscovery.thread.next_cursor, "cursor-next");
    const nativeDiscoveryJson = JSON.stringify(nativeDiscovery);
    assert.equal(nativeDiscoveryJson.includes(project), false);
    assert.equal(nativeDiscoveryJson.includes("DO_NOT_EXPOSE"), false);
    assert.equal(nativeDiscovery.thread.model_turn_started, false);

    const readiness = await probeCodexReadiness({ env: baseEnv, timeoutMs: 3000 });
    assert.equal(readiness.status, "ready", JSON.stringify(readiness));
    assert.equal(readiness.native_model_turns, 0);
    assert.equal(readiness.requested_was_symlink, true);
    assert.equal(readiness.provider_mcp_probe, true);
    assert.equal(readiness.owner, "codex_context_provider");

    process.stdout.write(JSON.stringify({
      status: "pass",
      bridge_version: first.bridge_version,
      single_process_per_bootstrap: true,
      initialized_notification: true,
      codex_symlink_canonicalized: true,
      symlinked_readiness_entrypoint: true,
      explicit_invalid_codex_fails_closed: true,
      provider_mcp_probe: true,
      native_host_command_exec_readonly: true,
      native_mcp_search_pagination: true,
      native_mcp_search_excludes_destructive: true,
      native_thread_read_path_safe: true,
      skill_body_changes_fingerprint: true,
      symlink_metadata_escape_blocked: true,
      native_model_turns: 0
    }, null, 2) + "\n");
  } finally {
    fs.rmSync(temp, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
}

if (process.argv.includes("--fake-app-server")) {
  await fakeAppServer();
} else if (process.argv.includes("--contract-child")) {
  await contractChild();
} else if (process.argv.includes("--native-host-child")) {
  await nativeHostChild();
} else if (process.argv.includes("--native-discovery-child")) {
  await nativeDiscoveryChild();
} else {
  await main();
}
