#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

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

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "webcodex-context-bridge-"));
  try {
    const project = path.join(temp, "project");
    const outside = path.join(temp, "outside.txt");
    const codexHome = path.join(temp, "codex-home");
    const fakeCodex = path.join(temp, "fake-codex");
    const log = path.join(temp, "fake.log");
    fs.mkdirSync(path.join(project, ".skills", "demo"), { recursive: true });
    fs.mkdirSync(path.join(project, "docs"), { recursive: true });
    fs.mkdirSync(codexHome, { recursive: true });
    fs.writeFileSync(path.join(project, ".skills", "demo", "SKILL.md"), "# demo\nfirst body\n");
    fs.writeFileSync(path.join(project, "docs", "README.md"), "# knowledge\n");
    fs.writeFileSync(outside, "outside-only\n");
    fs.symlinkSync(outside, path.join(project, "linked.txt"));
    fs.writeFileSync(path.join(project, "reuse-manifest.json"), JSON.stringify({
      schema_version: "fixture",
      profile: "fixture",
      knowledge_paths: { docs: "docs/README.md", escape: "linked.txt" }
    }));
    fs.writeFileSync(fakeCodex, '#!/bin/sh\nexec "$FAKE_NODE" "$FAKE_SELF_CHECK" --fake-app-server "$@"\n');
    fs.chmodSync(fakeCodex, 0o755);

    const baseEnv = {
      ...process.env,
      CODEX_BIN: fakeCodex,
      CODEX_HOME: codexHome,
      WEBCODEX_PROJECT_ROOT: project,
      FAKE_NODE: process.execPath,
      FAKE_SELF_CHECK: SELF,
      FAKE_LOG: log
    };
    const first = runChild(baseEnv);
    assert.equal(first.bridge_version, "0.5.0");
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

    process.stdout.write(JSON.stringify({
      status: "pass",
      bridge_version: first.bridge_version,
      single_process_per_bootstrap: true,
      initialized_notification: true,
      skill_body_changes_fingerprint: true,
      symlink_metadata_escape_blocked: true,
      native_model_turns: 0
    }, null, 2) + "\n");
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

if (process.argv.includes("--fake-app-server")) {
  await fakeAppServer();
} else if (process.argv.includes("--contract-child")) {
  await contractChild();
} else {
  await main();
}
