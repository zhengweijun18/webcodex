#!/usr/bin/env python3
"""Verify Codex-compatible observable semantics without using Codex model quota."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


POLICY = Path("docs/agent/native-semantic-parity-policy.json")
REFERENCE = Path("docs/agent/native-semantic-parity-reference.json")
STATE_RELATIVE = Path("artifacts/outputs/webcodex-codex-parity/target-mode-state.json")
BRIDGE_RELATIVE = Path("tooling/tools/webcodex-codex-context-bridge/bridge-lib.mjs")
ALLOWED_ROUTES = {"webcodex_native", "zero_quota_bridge", "unavailable"}


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"required parity file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid parity JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"parity JSON root must be an object: {path}")
    return value


def contains_tokens(path: Path, tokens: tuple[str, ...]) -> tuple[bool, dict]:
    if not path.is_file():
        return False, {"path": str(path), "missing": True}
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [token for token in tokens if token not in text]
    return not missing, {"path": str(path), "missing_tokens": missing}


def static_probe(root: Path, probe: str) -> tuple[bool, dict]:
    if probe == "local_file_operations":
        source_ok, source = contains_tokens(
            root / "crates/webcodex-runner/src/webcodex_runner/files.rs",
            ("webcodex.file_read_range.v1", "read_range_with_budget", "sha256"),
        )
        tests_ok, tests = contains_tokens(
            root / "crates/webcodex-runner/src/main_tests/file_read.rs",
            (
                "runner_file_read_range_reads_large_file_subset_under_max_bytes",
                "runner_file_read_range_errors_never_include_absolute_path",
            ),
        )
        return source_ok and tests_ok, {"source": source, "tests": tests}
    if probe == "provider_lifecycle":
        source = root / "crates/webcodex-core/src/mcp_gateway.rs"
        if not source.is_file():
            return False, {"path": str(source), "missing": True}
        text = source.read_text(encoding="utf-8", errors="replace")
        passive = "ProviderStatus" in text
        recovery = "ProviderReset" in text or "ConnectionRetired" in text
        return passive and recovery, {
            "path": str(source),
            "passive_status": passive,
            "explicit_or_retired_recovery": recovery,
        }
    if probe == "session_context_recovery":
        source_ok, source = contains_tokens(
            root / "src/tool_runtime/session_context.rs",
            ("session_context_revision", "session_continuity", "session_recovery"),
        )
        tests_ok, tests = contains_tokens(
            root / "src/tool_runtime/sessions/tests.rs",
            ("session_recovery", "history_lost", "current_handoff"),
        )
        return source_ok and tests_ok, {"source": source, "tests": tests}
    if probe == "session_resume_equivalent":
        restart_ok, restart = contains_tokens(
            root / "src/tool_runtime/tests/reconnect.rs",
            (
                "canonical_project_session_explicit_resume_survives_restart",
                "restored_sessions",
                "coding_resume_call",
                'resumed.output["session_id"]',
            ),
        )
        recovery_ok, recovery = contains_tokens(
            root / "src/tool_runtime/sessions/tests.rs",
            (
                "history_lost_session_context_recovery_adds_current_handoff",
                "bounded_session_context_recovery_adds_current_handoff_before_latest_ack",
                "current_handoff",
            ),
        )
        jobs_ok, jobs = contains_tokens(
            root / "src/tool_runtime/tests/jobs.rs",
            (
                "model_facing_stop_job_reports_requested_and_already_stop_requested",
                "terminal_pending",
                "recovery_kind",
            ),
        )
        return restart_ok and recovery_ok and jobs_ok, {
            "restart_resume": restart,
            "context_recovery": recovery,
            "job_reobservation": jobs,
        }
    if probe == "intentionally_unavailable":
        return True, {"intentional": True}
    if probe == "zero_quota_bridge":
        return True, {"external": True}
    return False, {"error": f"unknown parity probe: {probe}"}


def validate_policy(policy: dict) -> list[str]:
    failures: list[str] = []
    if policy.get("schema_version") != 1:
        failures.append("unsupported parity policy schema")
    if policy.get("kind") != "webcodex-native-semantic-parity-policy":
        failures.append("unexpected parity policy kind")
    invariants = policy.get("invariants") or {}
    expected = {
        "codex_role": "reference_only",
        "native_codex_model_quota": "forbidden",
        "codex_model_fallback": "forbidden",
        "unsupported_capability": "fail_closed",
    }
    for key, value in expected.items():
        if invariants.get(key) != value:
            failures.append(f"invariant {key} must be {value}")
    routes = set(invariants.get("allowed_routes") or [])
    if routes != ALLOWED_ROUTES:
        failures.append("allowed routes must be exactly webcodex_native/zero_quota_bridge/unavailable")
    seen: set[str] = set()
    for capability in policy.get("capabilities") or []:
        capability_id = capability.get("id")
        if not capability_id or capability_id in seen:
            failures.append(f"duplicate or missing capability id: {capability_id!r}")
            continue
        seen.add(capability_id)
        route_order = capability.get("route_order") or []
        if not route_order:
            failures.append(f"{capability_id}: route_order is empty")
            continue
        invalid = [route for route in route_order if route not in ALLOWED_ROUTES]
        if invalid:
            failures.append(f"{capability_id}: forbidden route(s): {', '.join(invalid)}")
        if route_order[-1] != "unavailable":
            failures.append(f"{capability_id}: route_order must fail closed to unavailable")
        if capability.get("parity_target") == "intentional_gap" and route_order != ["unavailable"]:
            failures.append(f"{capability_id}: intentional gaps must expose only unavailable")
    return failures


def validate_reference(policy: dict, reference: dict) -> list[str]:
    failures: list[str] = []
    if reference.get("schema_version") != 1:
        failures.append("unsupported parity reference schema")
    if reference.get("kind") != "webcodex-native-semantic-parity-reference":
        failures.append("unexpected parity reference kind")
    if reference.get("source") != "reference_contract_not_live_codex_model_benchmark":
        failures.append("reference source must explicitly say it is not a live Codex model benchmark")
    if reference.get("live_codex_model_execution_required") is not False:
        failures.append("reference fixture must not require live Codex model execution")
    expected = reference.get("capabilities") or {}
    for capability in policy.get("capabilities") or []:
        capability_id = capability["id"]
        item = expected.get(capability_id)
        if not isinstance(item, dict):
            failures.append(f"reference missing capability: {capability_id}")
            continue
        route_order = capability.get("route_order") or []
        if item.get("expected_route") != route_order[0]:
            failures.append(f"reference route drift: {capability_id}")
        if not item.get("observable_contract"):
            failures.append(f"reference observable contract is empty: {capability_id}")
    extra = sorted(set(expected) - {item["id"] for item in policy.get("capabilities") or []})
    if extra:
        failures.append("reference has unknown capabilities: " + ", ".join(extra))
    return failures


def extract_bridge_rpc_methods(text: str) -> set[str]:
    return set(
        re.findall(
            r"""(?:codexRpc|request)\(\s*["']([^"']+)["']""",
            text,
        )
    )


def audit_bridge(policy: dict, workspace: Path) -> tuple[bool, dict, list[str]]:
    bridge_path = workspace / BRIDGE_RELATIVE
    if not bridge_path.is_file():
        return False, {"path": str(bridge_path)}, ["zero-quota bridge source is missing"]
    text = bridge_path.read_text(encoding="utf-8", errors="replace")
    methods = extract_bridge_rpc_methods(text)
    bridge_policy = policy.get("zero_quota_bridge") or {}
    allowed = set(bridge_policy.get("allowed_app_server_methods") or [])
    unknown = sorted(methods - allowed)
    missing_required_guard = []
    if bridge_policy.get("thread_start_requires_ephemeral") is not True:
        missing_required_guard.append("policy does not require ephemeral thread/start")
    thread_calls = list(
        re.finditer(r"""(?:codexRpc|request)\(\s*["']thread/start["']""", text)
    )
    non_ephemeral = []
    for match in thread_calls:
        nearby = text[match.start() : match.start() + 260]
        if "ephemeral: true" not in nearby:
            non_ephemeral.append(match.start())
    failures = []
    if unknown:
        failures.append("bridge uses non-allowlisted Codex app-server RPC: " + ", ".join(unknown))
    if non_ephemeral:
        failures.append("bridge has thread/start without nearby ephemeral: true")
    failures.extend(missing_required_guard)
    if bridge_policy.get("generic_destructive_passthrough") is not False:
        failures.append("bridge policy must forbid generic destructive passthrough")
    return not failures, {
        "path": str(bridge_path),
        "methods": sorted(methods),
        "allowlisted_methods": sorted(allowed),
        "non_ephemeral_thread_start_offsets": non_ephemeral,
    }, failures


def validate_external_state(policy: dict, workspace: Path) -> tuple[bool, dict, list[str]]:
    state_path = workspace / STATE_RELATIVE
    if not state_path.is_file():
        return False, {"path": str(state_path)}, ["zero-quota state file is missing"]
    value = load_json(state_path)
    bridge_policy = policy.get("zero_quota_bridge") or {}
    failures: list[str] = []
    if value.get("schema") != bridge_policy.get("state_schema"):
        failures.append("unexpected zero-quota state schema")
    native = value.get("native_runtime") or {}
    if native.get("codex_model_turns_allowed") is not False:
        failures.append("native_runtime.codex_model_turns_allowed must be false")
    if native.get("acp_coding_agent_enabled") is not False:
        failures.append("native_runtime.acp_coding_agent_enabled must be false")
    required_quota = bridge_policy.get("required_quota_mode")
    for field in ("native_mcp_readonly_proxy", "native_mcp_effectful_proxy"):
        item = value.get(field) or {}
        if item.get("quota_mode") != required_quota:
            failures.append(f"{field}.quota_mode drift")
        if item.get("model_turn_started") is not False:
            failures.append(f"{field}.model_turn_started must be false")
    effectful = value.get("native_mcp_effectful_proxy") or {}
    if effectful.get("generic_destructive_passthrough") is not False:
        failures.append("generic destructive MCP passthrough must be false")
    proof = value.get("quota_proof") or {}
    if proof.get("codex_model_turn_started") is not False:
        failures.append("quota_proof.codex_model_turn_started must be false")
    if proof.get("rate_limits_before_after_equal") is not True:
        failures.append("quota proof rate limits changed")
    if proof.get("usage_before_after_equal") is not True:
        failures.append("quota proof usage changed")
    bridge_ok, bridge_data, bridge_failures = audit_bridge(policy, workspace)
    failures.extend(bridge_failures)
    return not failures and bridge_ok, {
        "path": str(state_path),
        "verified_at": value.get("verified_at"),
        "goal": value.get("goal"),
        "native_runtime": {
            "codex_model_turns_allowed": native.get("codex_model_turns_allowed"),
            "acp_coding_agent_enabled": native.get("acp_coding_agent_enabled"),
        },
        "quota_proof": {
            "codex_model_turn_started": proof.get("codex_model_turn_started"),
            "rate_limits_before_after_equal": proof.get("rate_limits_before_after_equal"),
            "usage_before_after_equal": proof.get("usage_before_after_equal"),
        },
        "bridge_rpc_audit": bridge_data,
    }, failures


def evaluate(
    root: Path,
    policy_path: Path,
    reference_path: Path,
    context_workspace: Path | None,
) -> dict:
    policy = load_json(policy_path)
    reference = load_json(reference_path)
    failures = validate_policy(policy)
    failures.extend(validate_reference(policy, reference))

    external_ok: bool | None = None
    external_data: dict = {"status": "not_requested"}
    if context_workspace is not None:
        external_ok, data, external_failures = validate_external_state(policy, context_workspace)
        external_data = {
            "status": "passed" if external_ok else "failed",
            **data,
        }
        failures.extend(external_failures)

    coverage = []
    capability_map = {item["id"]: item for item in policy.get("capabilities") or []}
    for capability_id, capability in capability_map.items():
        probe = capability.get("probe")
        static_ok, evidence = static_probe(root, probe)
        if probe == "zero_quota_bridge":
            if context_workspace is None:
                evidence_status = "not_requested"
                resolved_route = None
            else:
                evidence_status = "passed" if external_ok else "failed"
                resolved_route = "zero_quota_bridge" if external_ok else "unavailable"
            evidence = external_data
        elif probe == "intentionally_unavailable":
            evidence_status = "intentional"
            resolved_route = "unavailable"
        else:
            evidence_status = "passed" if static_ok else "failed"
            resolved_route = "webcodex_native" if static_ok else "unavailable"
            if capability.get("required") and not static_ok:
                failures.append(f"{capability_id}: required local probe failed")
        coverage.append(
            {
                "id": capability_id,
                "required": bool(capability.get("required")),
                "parity_target": capability.get("parity_target"),
                "declared_route_order": capability.get("route_order"),
                "resolved_route": resolved_route,
                "evidence_status": evidence_status,
                "evidence": evidence,
                "codex_model_fallback_allowed": False,
            }
        )

    failures = list(dict.fromkeys(failures))
    return {
        "status": "passed" if not failures else "failed",
        "schema_version": 1,
        "goal": policy.get("goal"),
        "invariant": {
            "codex_is_reference_not_backend": True,
            "native_codex_model_quota_budget": 0,
            "observed_native_codex_model_quota_consumed": 0 if external_ok is True else None,
            "codex_model_fallback_allowed": False,
            "unsupported_capability_behavior": "unavailable",
        },
        "policy": str(policy_path),
        "reference": {
            "path": str(reference_path),
            "source": reference.get("source"),
            "live_codex_model_execution_required": reference.get(
                "live_codex_model_execution_required"
            ),
        },
        "external_evidence": external_data,
        "coverage": coverage,
        "failed_checks": failures,
        "mutations_performed": False,
    }


def route_from_report(report: dict, capability_id: str) -> dict:
    for item in report.get("coverage") or []:
        if item.get("id") == capability_id:
            resolved = item.get("resolved_route")
            if resolved is None:
                resolved = "unavailable"
            return {
                "capability": capability_id,
                "route": resolved,
                "declared_route_order": item.get("declared_route_order"),
                "evidence_status": item.get("evidence_status"),
                "codex_model_fallback_allowed": False,
                "fail_closed": resolved == "unavailable",
            }
    raise RuntimeError(f"unknown parity capability: {capability_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--context-workspace", type=Path)
    parser.add_argument("--route")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    policy = args.policy.resolve() if args.policy else root / POLICY
    reference = args.reference.resolve() if args.reference else root / REFERENCE
    workspace = args.context_workspace.resolve() if args.context_workspace else None
    try:
        report = evaluate(root, policy, reference, workspace)
        if args.route:
            output = route_from_report(report, args.route)
            print(json.dumps(output, indent=2))
            return 0 if report["status"] == "passed" else 1
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for item in report["coverage"]:
                route = item["resolved_route"] or "not-evaluated"
                print(f"[{item['evidence_status'].upper():13}] {item['id']}: {route}")
            print("\ninvariant=native_codex_model_quota_budget==0")
            print(f"status={report['status']}")
        return 0 if report["status"] == "passed" else 1
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        else:
            print(f"native semantic parity check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
