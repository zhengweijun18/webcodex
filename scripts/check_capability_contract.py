#!/usr/bin/env python3
"""Read-only semantic capability checks for the maintained WebCodex fork."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


CONTRACT = Path("docs/agent/local-fork-capability-contract.json")


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


def probe(root: Path, capability: str) -> tuple[bool, dict]:
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
        return required_files(root, ("scripts/build_local_desktop_candidate.sh",))
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
    if capability == "external_zero_quota_evidence":
        return True, {"external": True, "checked_by": "fork_doctor.py --context-workspace"}
    if capability == "zero_codex_quota_global_invariant":
        return contains_any(
            root,
            (
                (
                    "docs/agent/native-semantic-parity-policy.json",
                    (
                        '"native_codex_model_quota": "forbidden"',
                        '"codex_model_fallback": "forbidden"',
                        '"unsupported_capability": "fail_closed"',
                    ),
                ),
            ),
        )
    if capability == "native_semantic_parity_policy":
        return required_files(
            root,
            (
                "docs/agent/native-semantic-parity-policy.json",
                "docs/agent/native-semantic-parity-reference.json",
            ),
        )
    if capability == "native_semantic_parity_harness":
        return contains_any(
            root,
            (
                (
                    "scripts/check_native_semantic_parity.py",
                    (
                        "codex_model_fallback_allowed",
                        "audit_bridge",
                        "validate_external_state",
                        "reference_contract_not_live_codex_model_benchmark",
                    ),
                ),
            ),
        )
    return False, {"error": f"unknown capability id: {capability}"}


def check(root: Path, contract_path: Path) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != 1:
        raise RuntimeError("unsupported capability contract schema")
    results = []
    for capability in contract.get("capabilities", []):
        capability_id = capability.get("id")
        passed, data = probe(root, capability_id)
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
        "failed_required_capabilities": failures,
        "mutations_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    contract = (
        args.contract.resolve()
        if args.contract
        else root / CONTRACT
    )
    result = check(root, contract)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["capabilities"]:
            print(f"[{item['status'].upper():6}] {item['id']}")
        print(f"\nstatus={result['status']}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
