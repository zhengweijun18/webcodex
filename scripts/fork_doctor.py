#!/usr/bin/env python3
"""Read-only health checks for a maintained WebCodex fork."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

OFFICIAL_UPSTREAM = "https://github.com/yyjeqhc/webcodex.git"
DEFAULT_BRANCHES = ("vue-lsp-native", "vue-lsp-native-main")
RUNTIME_PREFIXES = ("crates/", "apps/", "npm/")
RUNTIME_FILES = {"Cargo.toml", "Cargo.lock"}


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 20,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=check,
    )


def git(root: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=root, check=check).stdout.strip()


def record(checks: list[dict], name: str, fn) -> None:
    try:
        status, detail, data = fn()
    except Exception as exc:
        checks.append({"name": name, "status": "failed", "detail": str(exc)})
    else:
        item = {"name": name, "status": status, "detail": detail}
        if data is not None:
            item["data"] = data
        checks.append(item)


def passed(detail: str, data=None):
    return "passed", detail, data


def warning(detail: str, data=None):
    return "warning", detail, data


def skipped(detail: str, data=None):
    return "skipped", detail, data


def check_clean(root: Path):
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError("worktree is not clean")
    return passed("worktree is clean")


def check_remotes(root: Path, expected_origin: str | None):
    remotes = {}
    for name in ("origin", "upstream"):
        value = git(root, "remote", "get-url", name)
        remotes[name] = value
    if remotes["upstream"] != OFFICIAL_UPSTREAM:
        raise RuntimeError(f"unexpected upstream: {remotes['upstream']}")
    if expected_origin is not None and remotes["origin"] != expected_origin:
        raise RuntimeError(
            f"unexpected origin: expected={expected_origin} actual={remotes['origin']}"
        )
    if remotes["origin"] == remotes["upstream"]:
        raise RuntimeError("origin and upstream must be distinct")
    return passed("origin/upstream topology is valid", remotes)


def check_branches(root: Path, branches: tuple[str, ...]):
    values = {}
    for branch in branches:
        sha = git(root, "rev-parse", f"refs/heads/{branch}")
        upstream = git(
            root,
            "for-each-ref",
            "--format=%(upstream:short)",
            f"refs/heads/{branch}",
        )
        values[branch] = {"sha": sha, "tracking": upstream}
        expected = f"origin/{branch}"
        if upstream != expected:
            raise RuntimeError(f"{branch} tracks {upstream or '<none>'}, expected {expected}")
    return passed("maintenance branches and tracking refs are present", values)


def parse_runtime_version(line: str) -> dict:
    match = re.search(
        r"^(?P<name>\S+)\s+(?P<version>\S+)\s+\(commit\s+"
        r"(?P<commit>[0-9a-f]+),\s+dirty=(?P<dirty>true|false),\s+"
        r"built_at=(?P<built_at>\d+)\)$",
        line.strip(),
    )
    if not match:
        raise RuntimeError(f"unexpected runtime version output: {line.strip()}")
    value = match.groupdict()
    value["dirty"] = value["dirty"] == "true"
    value["built_at"] = int(value["built_at"])
    return value


def installed_runtime(root: Path, app: Path) -> dict:
    runtime = app / "Contents/Resources/webcodex-runtime"
    values = {}
    for name in ("webcodex", "webcodex-server", "webcodex-runner"):
        binary = runtime / name
        if not binary.is_file():
            raise RuntimeError(f"missing installed runtime binary: {binary}")
        line = run([str(binary), "--version"], cwd=root).stdout.splitlines()[0]
        values[name] = parse_runtime_version(line)
    commits = {item["commit"] for item in values.values()}
    versions = {item["version"] for item in values.values()}
    built_at = {item["built_at"] for item in values.values()}
    if len(commits) != 1 or len(versions) != 1 or len(built_at) != 1:
        raise RuntimeError(f"installed runtime identity mismatch: {values}")
    if any(item["dirty"] for item in values.values()):
        raise RuntimeError("installed runtime reports dirty=true")
    return {
        "commit": next(iter(commits)),
        "version": next(iter(versions)),
        "built_at": next(iter(built_at)),
        "binaries": values,
    }


def check_installed_desktop(root: Path, app: Path):
    if platform.system() != "Darwin":
        return skipped("installed Desktop check is macOS-only")
    if not app.is_dir():
        raise RuntimeError(f"installed Desktop is missing: {app}")
    run(["codesign", "--verify", "--deep", "--strict", str(app)], cwd=root)
    identity = installed_runtime(root, app)
    commit = identity["commit"]
    exists = run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        check=False,
    )
    if exists.returncode != 0:
        return warning(
            "installed Desktop is signed and internally aligned, but its source commit is not present locally",
            identity,
        )

    ancestor = run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=root,
        check=False,
    ).returncode == 0
    if not ancestor:
        return warning(
            "installed Desktop is signed/aligned but is not an ancestor of current HEAD",
            identity,
        )

    changed = [
        line
        for line in git(root, "diff", "--name-only", f"{commit}..HEAD").splitlines()
        if line
    ]
    runtime_drift = [
        path
        for path in changed
        if path in RUNTIME_FILES or path.startswith(RUNTIME_PREFIXES)
    ]
    identity["branch_changes_since_install"] = changed
    identity["runtime_affecting_changes_since_install"] = runtime_drift
    if runtime_drift:
        return warning(
            "installed Desktop is healthy but current branch contains newer runtime-affecting changes",
            identity,
        )
    return passed(
        "installed Desktop is signed/aligned; newer branch changes are maintenance-only",
        identity,
    )


def check_backup(root: Path, backup_dir: Path):
    if platform.system() != "Darwin":
        return skipped("Desktop backup check is macOS-only")
    apps = sorted(backup_dir.glob("*.app")) if backup_dir.is_dir() else []
    if not apps:
        return warning(f"no Desktop backup found under {backup_dir}")
    latest = max(apps, key=lambda item: item.stat().st_mtime)
    run(["codesign", "--verify", "--deep", "--strict", str(latest)], cwd=root)
    identity = installed_runtime(root, latest)
    return passed("at least one verified rollback Desktop backup is available", {
        "latest": str(latest),
        "identity": identity,
        "count": len(apps),
    })


def check_vue_tool(root: Path, vue_root: Path):
    server = vue_root / "bin/vue-language-server"
    package = vue_root / "node_modules/@vue/language-server/package.json"
    ts_package = vue_root / "node_modules/typescript/package.json"
    tsdk = vue_root / "node_modules/typescript/lib"
    for path in (server, package, ts_package):
        if not path.exists():
            raise RuntimeError(f"missing Vue LSP tool asset: {path}")
    vue_version = json.loads(package.read_text())["version"]
    ts_version = json.loads(ts_package.read_text())["version"]
    version_line = run([str(server), "--version"], cwd=root).stdout.strip()
    if version_line != vue_version:
        raise RuntimeError(
            f"Vue language-server wrapper mismatch: wrapper={version_line} package={vue_version}"
        )

    data = {
        "server": str(server),
        "vue_version": vue_version,
        "typescript_version": ts_version,
        "tsdk": str(tsdk),
    }
    if platform.system() == "Darwin":
        launch_server = run(
            ["launchctl", "getenv", "WEBCODEX_VUE_LANGUAGE_SERVER"],
            cwd=root,
            check=False,
        ).stdout.strip()
        launch_tsdk = run(
            ["launchctl", "getenv", "WEBCODEX_VUE_TSDK"],
            cwd=root,
            check=False,
        ).stdout.strip()
        data["launchctl_server"] = launch_server
        data["launchctl_tsdk"] = launch_tsdk
        if launch_server != str(server) or launch_tsdk != str(tsdk):
            raise RuntimeError("launchctl Vue LSP environment does not match stable tool paths")
    return passed("stable Vue LSP toolchain and persisted environment are healthy", data)


def check_git_proxy(root: Path):
    result = run(
        ["git", "config", "--show-origin", "--get-regexp", r"(^|\.)https?\.proxy"],
        cwd=root,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    values = []
    for line in lines:
        parts = line.split(None, 2)
        if parts:
            values.append(parts[-1])
    suspicious = [
        value
        for value in values
        if "127.0.0.1:7890" in value
        or value.startswith("socks5")
        or value in {"127.0.0.1", "http://127.0.0.1"}
    ]
    data = {"entries": lines, "suspicious_values": suspicious}
    if suspicious or len(set(values)) > 2:
        return warning("Git proxy configuration contains conflicting/stale entries", data)
    return passed("Git proxy configuration has no obvious conflicting entries", data)


def check_upstream_state(root: Path, branch: str, upstream_ref: str):
    local = git(root, "rev-parse", branch)
    upstream = git(root, "rev-parse", upstream_ref)
    base = git(root, "merge-base", branch, upstream_ref)
    left, right = git(
        root, "rev-list", "--left-right", "--count", f"{branch}...{upstream_ref}"
    ).split()
    data = {
        "branch": branch,
        "branch_sha": local,
        "upstream_ref": upstream_ref,
        "upstream_sha": upstream,
        "merge_base": base,
        "branch_unique_commits": int(left),
        "upstream_unique_commits": int(right),
    }
    if int(right):
        return warning("upstream contains commits not yet forward-ported", data)
    return passed("forward-port branch contains current upstream", data)


def check_capability_contract(root: Path, workspace: Path | None):
    script = root / "scripts/check_capability_contract.py"
    if not script.is_file():
        raise RuntimeError(f"capability contract checker is missing: {script}")
    command = [sys.executable, str(script), "--root", str(root), "--json"]
    if workspace is not None:
        state_path = workspace / "artifacts/outputs/webcodex-codex-parity/target-mode-state.json"
        command.extend(["--zero-quota-state", str(state_path)])
    result = run(
        command,
        cwd=root,
        timeout=20,
        check=False,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("capability contract checker returned invalid JSON") from exc
    if result.returncode != 0 or value.get("status") != "passed":
        failed = value.get("failed_required_capabilities") or []
        raise RuntimeError(
            "required capability contract failed: " + ", ".join(failed)
        )
    return passed(
        "required local-fork capability contract is intact",
        {
            "schema_version": value.get("schema_version"),
            "capability_count": len(value.get("capabilities") or []),
        },
    )


def check_zero_quota_state(root: Path, workspace: Path | None):
    if workspace is None:
        return skipped("zero-quota state contract not requested")
    state_path = (
        workspace
        / "artifacts/outputs/webcodex-codex-parity/target-mode-state.json"
    )
    if not state_path.is_file():
        raise RuntimeError(f"zero-quota state file is missing: {state_path}")
    value = json.loads(state_path.read_text())
    provider = value.get("provider") or {}
    closure = value.get("closure") or {}
    effectful = value.get("native_mcp_effectful_proxy") or {}
    readonly = value.get("native_mcp_readonly_proxy") or {}
    required_tools = {
        "describe_native_mcp_tool",
        "call_native_mcp_readonly",
        "call_native_mcp_effectful",
        "context_parity_check",
    }
    tools = set(provider.get("tools") or [])
    failures = []
    if value.get("schema") != "webcodex-codex-target-mode.v3":
        failures.append("unexpected state schema")
    if provider.get("bridge_version") != "0.4.0":
        failures.append("bridge version is not 0.4.0")
    if provider.get("tool_count") != 14 or not required_tools.issubset(tools):
        failures.append("provider tool contract drift")
    for key in (
        "provider_refresh",
        "self_check",
        "context_parity_check",
        "describe_native_mcp_tool",
        "state_report_sync",
    ):
        if closure.get(key) != "pass":
            failures.append(f"closure.{key} is not pass")
    if effectful.get("quota_mode") != "zero_codex_model_turn":
        failures.append("effectful quota mode drift")
    if effectful.get("model_turn_started") is not False:
        failures.append("effectful model_turn_started drift")
    if readonly.get("quota_mode") != "zero_codex_model_turn":
        failures.append("readonly quota mode drift")
    if readonly.get("model_turn_started") is not False:
        failures.append("readonly model_turn_started drift")
    if value.get("native_runtime", {}).get("acp_coding_agent_enabled") is not False:
        failures.append("ACP coding agent unexpectedly enabled")
    if failures:
        raise RuntimeError("; ".join(failures))
    return passed(
        "zero-Codex-model-turn state contract is intact",
        {
            "state": str(state_path),
            "bridge_version": provider.get("bridge_version"),
            "tool_count": provider.get("tool_count"),
            "fingerprint": value.get("context", {}).get("fingerprint"),
        },
    )


def check_context_bridge(root: Path, workspace: Path | None):
    script = root / "tooling/tools/codex-context-bridge/self-check.mjs"
    if not script.is_file():
        raise RuntimeError(f"context bridge self-check is missing: {script}")
    try:
        result = run(["node", str(script)], cwd=root, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return warning(
            "context bridge self-check exceeded 30s; zero-quota state is checked separately"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"context bridge self-check failed: {(result.stderr or result.stdout)[-2000:]}"
        )
    output = result.stdout.strip()
    if '"status": "pass"' not in output and '"status":"pass"' not in output:
        return warning("context bridge self-check exited zero but did not report PASS", output[-2000:])
    return passed("context bridge self-check reports PASS")


def check_deep(root: Path, vue_root: Path, enabled: bool):
    if not enabled:
        return skipped("deep Vue LSP regression not requested")
    env = {
        "CARGO_NET_OFFLINE": "true",
        "WEBCODEX_RUN_REAL_VUE_LSP": "1",
        "WEBCODEX_VUE_LANGUAGE_SERVER": str(vue_root / "bin/vue-language-server"),
        "WEBCODEX_VUE_TSDK": str(vue_root / "node_modules/typescript/lib"),
    }
    result = run(
        ["bash", "scripts/check_vue_lsp_patch.sh"],
        cwd=root,
        timeout=300,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"deep Vue LSP regression failed: {(result.stderr or result.stdout)[-3000:]}"
        )
    return passed("deep Vue LSP regression passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--origin-url")
    parser.add_argument("--installed-app", type=Path, default=Path("/Applications/WebCodex Desktop.app"))
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path.home() / "Library/Application Support/dev.webcodex.desktop/backups",
    )
    parser.add_argument(
        "--vue-tool-root",
        type=Path,
        default=Path.home()
        / "Library/Application Support/dev.webcodex.desktop/local-tools/vue-lsp-2.2.12",
    )
    parser.add_argument("--context-workspace", type=Path)
    parser.add_argument("--forward-branch", default="vue-lsp-native-main")
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    checks: list[dict] = []
    record(checks, "worktree-clean", lambda: check_clean(root))
    record(checks, "remotes", lambda: check_remotes(root, args.origin_url))
    record(checks, "maintenance-branches", lambda: check_branches(root, DEFAULT_BRANCHES))
    record(checks, "installed-desktop", lambda: check_installed_desktop(root, args.installed_app))
    record(checks, "rollback-backup", lambda: check_backup(root, args.backup_dir))
    record(checks, "vue-lsp-toolchain", lambda: check_vue_tool(root, args.vue_tool_root))
    record(checks, "git-proxy", lambda: check_git_proxy(root))
    record(
        checks,
        "upstream-state",
        lambda: check_upstream_state(root, args.forward_branch, args.upstream_ref),
    )
    record(checks, "capability-contract", lambda: check_capability_contract(root, args.context_workspace))
    record(
        checks,
        "zero-quota-state",
        lambda: check_zero_quota_state(root, args.context_workspace),
    )
    record(
        checks,
        "context-bridge",
        lambda: check_context_bridge(root, args.context_workspace),
    )
    record(checks, "deep-vue-regression", lambda: check_deep(root, args.vue_tool_root, args.deep))

    failures = [item for item in checks if item["status"] == "failed"]
    warnings = [item for item in checks if item["status"] == "warning"]
    summary = {
        "status": "failed" if failures else ("warning" if warnings else "passed"),
        "root": str(root),
        "checks": checks,
        "failed_checks": [item["name"] for item in failures],
        "warning_checks": [item["name"] for item in warnings],
        "mutations_performed": False,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(f"[{item['status'].upper():7}] {item['name']}: {item['detail']}")
        print(f"\nstatus={summary['status']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
