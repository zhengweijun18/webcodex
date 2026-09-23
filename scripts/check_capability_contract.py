#!/usr/bin/env python3
"""Read-only semantic capability checks for the maintained WebCodex fork."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


CONTRACT = Path("docs/agent/local-fork-capability-contract.json")
RETIREMENT_VERIFIER_KINDS = {
    "behavioral_capabilities",
    "desktop_lifecycle_contract",
}


def contains_any(root: Path, candidates: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[bool, dict]:
    evidence = []
    for relative, tokens in candidates:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matched = [token for token in tokens if token in text]
        evidence.append({"path": relative, "matched": matched})
        if len(matched) == len(tokens):
            return True, {"evidence": evidence}
    return False, {"evidence": evidence}


def required_files(root: Path, files: tuple[str, ...]) -> tuple[bool, dict]:
    missing = [relative for relative in files if not (root / relative).is_file()]
    return not missing, {"files": list(files), "missing": missing}


def valid_zero_quota_state(state_path: Path | None) -> tuple[bool, dict]:
    if state_path is None:
        return False, {"external": True, "reason": "zero_quota_state_not_supplied"}
    if not state_path.is_file():
        return False, {"external": True, "reason": "zero_quota_state_missing", "state": str(state_path)}
    value = json.loads(state_path.read_text(encoding="utf-8"))
    effectful = value.get("native_mcp_effectful_proxy") or {}
    readonly = value.get("native_mcp_readonly_proxy") or {}
    passed = (
        effectful.get("quota_mode") == "zero_codex_model_turn"
        and effectful.get("model_turn_started") is False
        and readonly.get("quota_mode") == "zero_codex_model_turn"
        and readonly.get("model_turn_started") is False
        and value.get("native_runtime", {}).get("acp_coding_agent_enabled") is False
    )
    return passed, {
        "external": True,
        "state": str(state_path),
        "effectful_model_turn_started": effectful.get("model_turn_started"),
        "readonly_model_turn_started": readonly.get("model_turn_started"),
        "acp_coding_agent_enabled": value.get("native_runtime", {}).get("acp_coding_agent_enabled"),
    }


def validate_retirement_groups(contract: dict) -> dict:
    groups = contract.get("retirement_groups", [])
    if not isinstance(groups, list):
        raise RuntimeError("retirement_groups must be a list")
    ids = set()
    validated = []
    for group in groups:
        if not isinstance(group, dict):
            raise RuntimeError("retirement group must be an object")
        group_id = group.get("id")
        paths = group.get("implementation_paths")
        verifier = group.get("verifier")
        if not isinstance(group_id, str) or not group_id or group_id in ids:
            raise RuntimeError(f"invalid or duplicate retirement group id: {group_id!r}")
        ids.add(group_id)
        if not isinstance(paths, list) or not paths:
            raise RuntimeError(f"retirement group {group_id} requires implementation_paths")
        for relative in paths:
            if (
                not isinstance(relative, str)
                or not relative
                or relative.startswith("/")
                or ".." in Path(relative).parts
            ):
                raise RuntimeError(
                    f"retirement group {group_id} has unsafe implementation path: {relative!r}"
                )
        if not isinstance(verifier, dict) or verifier.get("kind") not in RETIREMENT_VERIFIER_KINDS:
            raise RuntimeError(f"retirement group {group_id} has unsupported verifier")
        validated.append(
            {
                "id": group_id,
                "implementation_path_count": len(paths),
                "verifier_kind": verifier["kind"],
            }
        )
    return {"count": len(validated), "groups": validated}


def probe(root: Path, capability: str, zero_quota_state: Path | None) -> tuple[bool, dict]:
    if capability == "native_vue_sfc_lsp":
        return contains_any(
            root,
            (
                (
                    "crates/webcodex-lsp/src/language.rs",
                    ("vue", "vue-language-server"),
                ),
                (
                    "crates/webcodex-runner/src/webcodex_runner/lsp/language.rs",
                    ("vue", "vue-language-server"),
                ),
            ),
        )
    if capability == "stable_provider_environment":
        return contains_any(
            root,
            (
                (
                    "crates/webcodex-runner/src/webcodex_runner/config.rs",
                    ("env: BTreeMap", "env_from_env"),
                ),
            ),
        )
    if capability == "observable_recoverable_provider_lifecycle":
        legacy, legacy_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-core/src/mcp_gateway.rs",
                    ("ProviderStatus", "ProviderReset"),
                ),
            ),
        )
        modern, modern_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-core/src/mcp_gateway.rs",
                    ("ProviderStatus", "ConnectionRetired"),
                ),
            ),
        )
        if legacy:
            return True, {"strategy": "explicit_reset", **legacy_data}
        if modern:
            return True, {"strategy": "connection_retirement", **modern_data}
        return False, {
            "strategy": None,
            "legacy": legacy_data,
            "modern": modern_data,
        }
    if capability == "upstream_compatibility_rehearsal":
        return required_files(root, ("scripts/check_upstream_compat.py",))
    if capability == "reproducible_desktop_candidate":
        return required_files(
            root,
            (
                "scripts/build_local_desktop_candidate.sh",
                "scripts/prepare_bundled_node_macos.sh",
                "scripts/prepare_bundled_node_windows.ps1",
                "scripts/prepare_desktop_bundle_macos.py",
                "scripts/prepare_desktop_bundle.ps1",
                "scripts/desktop_install_macos_smoke.sh",
                "scripts/desktop_install_windows_smoke.ps1",
            ),
        )
    if capability == "verified_install_and_rollback":
        return required_files(root, ("scripts/local_desktop_lifecycle.py",))
    if capability == "pinned_vue_toolchain_bootstrap":
        return required_files(root, ("scripts/vue_lsp_toolchain.py",))
    if capability == "bounded_bridge_probe":
        ok, data = contains_any(
            root,
            (
                (
                    "scripts/fork_doctor.py",
                    ("timeout=30", "zero-quota state is checked separately"),
                ),
            ),
        )
        return ok, data
    if capability == "truthful_single_install_readiness":
        bridge_ok, bridge_data = contains_any(
            root,
            (
                (
                    "tooling/tools/codex-context-bridge/readiness.mjs",
                    (
                        "resolveCodexExecutable",
                        "probeCodexReadiness",
                        "probeBundledProvider",
                        "provider_mcp_probe",
                        "native_model_turns",
                        "realpathSync",
                    ),
                ),
            ),
        )
        runner_ok, runner_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-runner/src/webcodex_runner/config.rs",
                    (
                        "bundled_context_readiness",
                        "BUNDLED_CONTEXT_READINESS_TIMEOUT_MS",
                        "\"CODEX_BIN\"",
                    ),
                ),
            ),
        )
        desktop_projection_ok, desktop_projection_data = contains_any(
            root,
            (
                (
                    "apps/desktop/src-tauri/src/enhanced_runtime.rs",
                    (
                        "native_probe",
                        "vue_toolchain_external_ready",
                        "native_model_turns",
                    ),
                ),
            ),
        )
        desktop_refresh_ok, desktop_refresh_data = contains_any(
            root,
            (
                (
                    "apps/desktop/src-tauri/src/state.rs",
                    ("ENHANCED_RUNTIME_REFRESH_INTERVAL", "enhanced_runtime_observed_at"),
                ),
            ),
        )
        ui_ok, ui_data = contains_any(
            root,
            (
                (
                    "apps/desktop/src/features/workspace/WorkspaceStatus.tsx",
                    ("native_context", "vue_lsp", "readinessDetail"),
                ),
            ),
        )
        windows_bundle_ok, windows_bundle_data = contains_any(
            root,
            (
                (
                    "scripts/prepare_desktop_bundle.ps1",
                    (
                        "webcodex-tools/node/node.exe",
                        "codex-context-bridge",
                        "ContextBridgeVersion",
                    ),
                ),
                (
                    "scripts/desktop_install_windows_smoke.ps1",
                    (
                        "native_model_turns",
                        "readiness.mjs",
                        "StageMetadata",
                    ),
                ),
            ),
        )
        macos_bundle_ok, macos_bundle_data = contains_any(
            root,
            (
                (
                    "scripts/prepare_desktop_bundle_macos.py",
                    (
                        "webcodex-tools/node/node",
                        "codex-context-bridge",
                        "context_bridge_version",
                    ),
                ),
                (
                    "scripts/desktop_install_macos_smoke.sh",
                    (
                        "native_model_turns",
                        "readiness.mjs",
                        "bundled_tools",
                    ),
                ),
            ),
        )
        return (
            bridge_ok
            and runner_ok
            and desktop_projection_ok
            and desktop_refresh_ok
            and ui_ok
            and windows_bundle_ok
            and macos_bundle_ok
        ), {
            "shared_bridge_probe": bridge_data,
            "runner_injection": runner_data,
            "desktop_projection": desktop_projection_data,
            "desktop_refresh": desktop_refresh_data,
            "user_visible_status": ui_data,
            "windows_bundle": windows_bundle_data,
            "macos_bundle": macos_bundle_data,
        }
    if capability == "zero_quota_native_context_orchestration":
        runtime_ok, runtime_data = contains_any(
            root,
            (
                (
                    "src/tool_runtime/native_context.rs",
                    (
                        "native_context_for_coding_startup",
                        "host_lifecycle_intercept",
                        "STARTUP_NATIVE_CONTEXT_MAX_BYTES",
                    ),
                ),
            ),
        )
        gateway_ok, gateway_data = contains_any(
            root,
            (
                (
                    "src/mcp_gateway.rs",
                    (
                        "call_tool_on_runner",
                        "resolve_provider_on_runner",
                        "INTERNAL_CONTEXT_GATEWAY_WAIT_TIMEOUT",
                    ),
                ),
            ),
        )
        startup_ok, startup_data = contains_any(
            root,
            (
                (
                    "src/tool_runtime/coding_task.rs",
                    ("native_context_for_coding_startup", "native_context.as_ref()"),
                ),
            ),
        )
        return runtime_ok and gateway_ok and startup_ok, {
            "runtime_projection": runtime_data,
            "same_runner_gateway": gateway_data,
            "startup_integration": startup_data,
        }
    if capability == "pathless_native_context_loading":
        runtime_ok, runtime_data = contains_any(
            root,
            (
                (
                    "src/tool_runtime/native_context.rs",
                    (
                        "native_skill_load",
                        "native_knowledge_load",
                        "native_skill_id",
                        "project_relative_bridge_path",
                    ),
                ),
            ),
        )
        contract_ok, contract_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-tool-contracts/src/tool_call.rs",
                    ("NativeSkillLoad", "NativeKnowledgeLoad"),
                ),
            ),
        )
        model_ok, model_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-tool-contracts/src/tool_definition/skills.rs",
                    ("native_skill_load", "native_knowledge_load", "OwnerOnly"),
                ),
            ),
        )
        return runtime_ok and contract_ok and model_ok, {
            "runtime": runtime_data,
            "contract": contract_data,
            "model_surface": model_data,
        }
    if capability == "native_first_zero_turn_host_execution":
        bridge_ok, bridge_data = contains_any(
            root,
            (
                (
                    "tooling/tools/codex-context-bridge/bridge-lib.mjs",
                    (
                        "nativeHostExecReadOnly",
                        '"command/exec"',
                        'type: "readOnly"',
                        "networkAccess: false",
                        '"zero_codex_model_turn"',
                    ),
                ),
                (
                    "tooling/tools/codex-context-bridge/self-check.mjs",
                    (
                        "native_host_command_exec_readonly",
                        '"method:command/exec"',
                        "native_model_turns",
                    ),
                ),
            ),
        )
        runtime_ok, runtime_data = contains_any(
            root,
            (
                (
                    "src/tool_runtime/native_context.rs",
                    (
                        "native_host_exec_readonly",
                        "native_host_contract_drift",
                        "zero_codex_model_turn",
                    ),
                ),
                (
                    "src/tool_runtime/dispatch.rs",
                    ("ToolCall::NativeHostExecReadonly",),
                ),
                (
                    "crates/webcodex-runner/src/webcodex_runner/config.rs",
                    (
                        "is_legacy_managed_context_bridge",
                        "local-tools/codex-context-bridge/",
                        "replacing_legacy",
                    ),
                ),
            ),
        )
        contract_ok, contract_data = contains_any(
            root,
            (
                (
                    "crates/webcodex-tool-contracts/src/tool_definition/jobs.rs",
                    (
                        '"native_host_exec_readonly"',
                        "OwnerOnly",
                        "ToolEffect::Execute",
                        "ToolApprovalPolicy::Standard",
                    ),
                ),
                (
                    "crates/webcodex-tool-contracts/src/tool_call.rs",
                    ("NativeHostExecReadonly",),
                ),
            ),
        )
        browser_boundary_ok, browser_boundary_data = contains_any(
            root,
            (
                (
                    "tooling/tools/codex-context-bridge/server.mjs",
                    ("Never automate or scrape a ChatGPT Web browser/session",),
                ),
            ),
        )
        return bridge_ok and runtime_ok and contract_ok and browser_boundary_ok, {
            "bridge": bridge_data,
            "runtime": runtime_data,
            "contract": contract_data,
            "browser_session_boundary": browser_boundary_data,
        }
    if capability == "behavioral_native_context_conformance":
        script = root / "scripts/check_behavioral_capabilities.py"
        if not script.is_file():
            return False, {"missing": str(script)}
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--scope", "quick", "--json"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, {"exit_code": result.returncode, "stderr_tail": result.stderr[-2000:]}
        return result.returncode == 0 and value.get("status") == "passed", value
    if capability == "external_zero_quota_evidence":
        return valid_zero_quota_state(zero_quota_state)
    return False, {"error": f"unknown capability id: {capability}"}


def check(root: Path, contract_path: Path, zero_quota_state: Path | None = None) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != 1:
        raise RuntimeError("unsupported capability contract schema")
    retirement_groups = validate_retirement_groups(contract)
    results = []
    for capability in contract.get("capabilities", []):
        capability_id = capability.get("id")
        passed, data = probe(root, capability_id, zero_quota_state)
        results.append(
            {
                "id": capability_id,
                "required": bool(capability.get("required")),
                "status": "passed" if passed else "failed",
                "description": capability.get("description"),
                "data": data,
            }
        )
    failures = [
        item["id"]
        for item in results
        if item["required"] and item["status"] != "passed"
    ]
    return {
        "status": "passed" if not failures else "failed",
        "schema_version": contract["schema_version"],
        "contract": str(contract_path),
        "capabilities": results,
        "retirement_groups": retirement_groups,
        "failed_required_capabilities": failures,
        "mutations_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--zero-quota-state", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    contract = (
        args.contract.resolve()
        if args.contract
        else root / CONTRACT
    )
    result = check(root, contract, args.zero_quota_state)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["capabilities"]:
            print(f"[{item['status'].upper():6}] {item['id']}")
        print(f"\nstatus={result['status']}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
