#!/usr/bin/env python3
"""Verify the declared zero-quota observable Codex runtime contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


CONTRACT = Path("docs/agent/observable-codex-runtime-contract.json")
PARITY_CHECKER = Path("scripts/check_native_semantic_parity.py")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"required conformance file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid conformance JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"conformance JSON root must be an object: {path}")
    return value


def validate_contract(contract: dict) -> list[str]:
    failures: list[str] = []
    if contract.get("schema_version") != 1:
        failures.append("unsupported observable contract schema")
    if contract.get("kind") != "webcodex-observable-codex-runtime-contract":
        failures.append("unexpected observable contract kind")
    invariants = contract.get("invariants") or {}
    expected = {
        "codex_role": "reference_only",
        "native_codex_model_quota_budget": 0,
        "codex_model_fallback_allowed": False,
        "unsupported_behavior": "fail_closed",
    }
    for key, value in expected.items():
        if invariants.get(key) != value:
            failures.append(f"invariant {key} must be {value!r}")
    measurement = contract.get("measurement") or {}
    if measurement.get("percent_basis") != "declared_in_scope_scenarios_only":
        failures.append("conformance percentage basis must be declared in-scope scenarios only")
    if measurement.get("unknown_or_private_surfaces_count_as_supported") is not False:
        failures.append("unknown/private surfaces must never count as supported")
    scenarios = contract.get("in_scope_scenarios") or []
    if not scenarios:
        failures.append("observable contract has no in-scope scenarios")
    seen: set[str] = set()
    for scenario in scenarios:
        scenario_id = scenario.get("id")
        if not scenario_id or scenario_id in seen:
            failures.append(f"duplicate or missing scenario id: {scenario_id!r}")
            continue
        seen.add(scenario_id)
        if not scenario.get("observable_contract"):
            failures.append(f"{scenario_id}: observable_contract is empty")
        if not scenario.get("evidence"):
            failures.append(f"{scenario_id}: evidence is empty")
    out_of_scope = contract.get("explicitly_out_of_scope") or []
    out_ids = {item.get("id") for item in out_of_scope}
    required_out = {
        "codex_model_reasoning",
        "openai_private_host_internals",
        "native_tui_visual_presentation",
    }
    if not required_out.issubset(out_ids):
        failures.append("private/model/TUI exclusions must be explicit")
    return failures


def static_evidence(root: Path, scenario: dict) -> tuple[bool, list[dict]]:
    evidence_results = []
    passed = True
    for evidence in scenario.get("evidence") or []:
        relative = evidence.get("path")
        path = root / relative
        if not path.is_file():
            evidence_results.append({"path": relative, "missing": True})
            passed = False
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        tokens = evidence.get("all_tokens") or []
        missing = [token for token in tokens if token not in text]
        evidence_results.append({"path": relative, "missing_tokens": missing})
        if missing:
            passed = False
    return passed, evidence_results


def cargo_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CARGO_NET_OFFLINE"] = "true"
    env.setdefault("CARGO_TARGET_DIR", str(root / "target/observable-conformance"))
    path = env.get("PATH", "")
    cargo_bin = str(Path.home() / ".cargo/bin")
    if cargo_bin not in path.split(os.pathsep):
        env["PATH"] = cargo_bin + os.pathsep + path
    if sys.platform == "darwin":
        sdk = subprocess.run(
            ["xcrun", "--show-sdk-path"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        sdk_path = Path(sdk.stdout.strip()) if sdk.returncode == 0 else None
        if sdk_path and sdk_path.is_dir():
            framework_root = sdk_path / "System/Library/Frameworks"
            core_tbd = framework_root / "CoreGraphics.framework/Versions/A/CoreGraphics.tbd"
            needs_overlay = not (framework_root / "AVFAudio.framework").is_dir()
            core_text = core_tbd.read_text(errors="replace") if core_tbd.is_file() else ""
            missing_core = [
                symbol
                for symbol in (
                    "_CGPreflightPostEventAccess",
                    "_CGPreflightScreenCaptureAccess",
                )
                if core_text and symbol not in core_text
            ]
            needs_overlay = needs_overlay or bool(missing_core)
            if needs_overlay:
                overlay = root / "target/observable-conformance-sdk-overlay/Frameworks"
                shutil.rmtree(overlay.parent, ignore_errors=True)
                avf = overlay / "AVFAudio.framework"
                avf.mkdir(parents=True, exist_ok=True)
                (avf / "AVFAudio.tbd").write_text(
                    "--- !tapi-tbd-v3\n"
                    "archs:           [ x86_64 ]\n"
                    "platform:        macosx\n"
                    "install-name:    '/System/Library/Frameworks/AVFAudio.framework/Versions/A/AVFAudio'\n"
                    "current-version: 1\n"
                    "compatibility-version: 1\n"
                    "exports:\n"
                    "  - archs:           [ x86_64 ]\n"
                    "    symbols:         [ ]\n"
                    "...\n"
                )
                if core_tbd.is_file():
                    core_dir = overlay / "CoreGraphics.framework"
                    core_dir.mkdir(parents=True, exist_ok=True)
                    copied = core_dir / "CoreGraphics.tbd"
                    shutil.copy2(core_tbd, copied)
                    text = copied.read_text(errors="replace")
                    missing = [
                        symbol
                        for symbol in (
                            "_CGPreflightPostEventAccess",
                            "_CGPreflightScreenCaptureAccess",
                        )
                        if symbol not in text
                    ]
                    if missing:
                        needle = "    symbols:         [ "
                        if needle not in text:
                            raise RuntimeError(
                                "CoreGraphics TBD symbols list not found for conformance SDK overlay"
                            )
                        text = text.replace(
                            needle,
                            needle + ", ".join(missing) + ", ",
                            1,
                        )
                        copied.write_text(text)
                extra = f"-C link-arg=-F{overlay}"
                current = env.get("RUSTFLAGS", "").strip()
                env["RUSTFLAGS"] = extra if not current else f"{current} {extra}"
    return env


def dynamic_test_identity(spec: str | dict) -> str:
    if isinstance(spec, str):
        return f"webcodex:lib:{spec}"
    return ":".join(
        [
            str(spec.get("package") or "webcodex"),
            ",".join(spec.get("target_args") or ["--lib"]),
            str(spec.get("filter") or ""),
        ]
    )


def run_bounded_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout_secs: int,
) -> dict:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_secs)
        return {
            "status": "completed",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_secs": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        return {
            "status": "timeout",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_secs": round(time.monotonic() - started, 3),
            "timeout_secs": timeout_secs,
            "process_group_terminated": True,
        }


def run_dynamic_test(
    root: Path,
    spec: str | dict,
    *,
    timeout_secs: int = 180,
) -> dict:
    if isinstance(spec, str):
        package = "webcodex"
        target_args = ["--lib"]
        test_filter = spec
    else:
        package = str(spec.get("package") or "webcodex")
        target_args = [str(item) for item in spec.get("target_args") or ["--lib"]]
        test_filter = str(spec.get("filter") or "")
    if not test_filter:
        return {
            "status": "failed",
            "test_filter": test_filter,
            "reason": "missing dynamic test filter",
        }
    command = ["cargo", "test", "--locked", "-p", package, *target_args, test_filter]
    result = run_bounded_process(
        command,
        cwd=root,
        env=cargo_environment(root),
        timeout_secs=timeout_secs,
    )
    if result["status"] == "timeout":
        return {
            "status": "timeout",
            "test_filter": test_filter,
            "package": package,
            "reason": "timeout",
            "timeout_secs": timeout_secs,
            "duration_secs": result["duration_secs"],
            "process_group_terminated": result["process_group_terminated"],
            "stdout_tail": result["stdout"][-1600:],
            "stderr_tail": result["stderr"][-1600:],
        }
    combined = result["stdout"] + "\n" + result["stderr"]
    passed_match = re.search(r"test result: ok\. ([1-9]\d*) passed;", combined)
    return {
        "status": (
            "passed"
            if result["exit_code"] == 0 and passed_match
            else "failed"
        ),
        "test_filter": test_filter,
        "package": package,
        "target_args": target_args,
        "exit_code": result["exit_code"],
        "tests_passed": int(passed_match.group(1)) if passed_match else 0,
        "duration_secs": result["duration_secs"],
        "timeout_secs": timeout_secs,
        "stdout_tail": result["stdout"][-1600:],
        "stderr_tail": result["stderr"][-1600:],
    }


def external_zero_quota(
    root: Path,
    workspace: Path | None,
    *,
    timeout_secs: int = 30,
) -> dict:
    if workspace is None:
        return {"status": "not_requested"}
    command = [
        sys.executable,
        str(root / PARITY_CHECKER),
        "--root",
        str(root),
        "--context-workspace",
        str(workspace),
        "--json",
    ]
    result = run_bounded_process(
        command,
        cwd=root,
        env=None,
        timeout_secs=timeout_secs,
    )
    if result["status"] == "timeout":
        return {
            "status": "timeout",
            "reason": "timeout",
            "timeout_secs": timeout_secs,
            "duration_secs": result["duration_secs"],
            "process_group_terminated": result["process_group_terminated"],
            "stderr_tail": result["stderr"][-1600:],
        }
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return {
            "status": "failed",
            "reason": "invalid parity JSON",
            "duration_secs": result["duration_secs"],
            "stderr_tail": result["stderr"][-1600:],
        }
    invariant = payload.get("invariant") or {}
    return {
        "status": (
            "passed"
            if result["exit_code"] == 0
            and payload.get("status") == "passed"
            and invariant.get("native_codex_model_quota_budget") == 0
            and invariant.get("observed_native_codex_model_quota_consumed") == 0
            and invariant.get("codex_model_fallback_allowed") is False
            else "failed"
        ),
        "native_codex_model_quota_budget": invariant.get(
            "native_codex_model_quota_budget"
        ),
        "observed_native_codex_model_quota_consumed": invariant.get(
            "observed_native_codex_model_quota_consumed"
        ),
        "codex_model_fallback_allowed": invariant.get(
            "codex_model_fallback_allowed"
        ),
        "failed_checks": payload.get("failed_checks") or [],
        "duration_secs": result["duration_secs"],
        "timeout_secs": timeout_secs,
    }


def emit_progress(status: str, scenario_id: str, duration_secs: float | None = None) -> None:
    suffix = "" if duration_secs is None else f" duration={duration_secs:.3f}s"
    print(f"{status} {scenario_id}{suffix}", flush=True)


def evaluate(
    root: Path,
    contract: dict,
    *,
    run_tests: bool,
    context_workspace: Path | None,
    selected_scenarios: set[str] | None = None,
    scenario_timeout_secs: int = 180,
    progress: bool = False,
) -> dict:
    failures = validate_contract(contract)
    all_scenarios = contract.get("in_scope_scenarios") or []
    all_ids = [scenario["id"] for scenario in all_scenarios]
    requested = set(selected_scenarios or [])
    unknown = sorted(requested - set(all_ids))
    if unknown:
        failures.append("unknown selected scenarios: " + ", ".join(unknown))
    scenarios = [
        scenario
        for scenario in all_scenarios
        if not requested or scenario["id"] in requested
    ]
    external = {"status": "not_requested"}
    dynamic_cache: dict[str, dict] = {}
    results = []
    for scenario in scenarios:
        scenario_id = scenario["id"]
        scenario_started = time.monotonic()
        if progress and run_tests:
            emit_progress("RUN", scenario_id)
        static_ok, evidence = static_evidence(root, scenario)
        dynamic_filter = scenario.get("dynamic_test")
        if dynamic_filter and run_tests:
            cache_key = dynamic_test_identity(dynamic_filter)
            dynamic = dynamic_cache.setdefault(
                cache_key,
                run_dynamic_test(
                    root,
                    dynamic_filter,
                    timeout_secs=scenario_timeout_secs,
                ),
            )
        elif dynamic_filter:
            dynamic = {
                "status": "not_requested",
                "test_filter": (
                    dynamic_filter
                    if isinstance(dynamic_filter, str)
                    else dynamic_filter.get("filter")
                ),
            }
        else:
            dynamic = {"status": "not_required"}
        requires_external = bool(scenario.get("requires_external_zero_quota_evidence"))
        if requires_external and run_tests:
            external = external_zero_quota(
                root,
                context_workspace,
                timeout_secs=min(scenario_timeout_secs, 30),
            )
        elif requires_external and context_workspace is not None and not run_tests:
            external = external_zero_quota(root, context_workspace, timeout_secs=30)
        external_ok = (
            not requires_external
            or external.get("status") == "passed"
            or (not run_tests and context_workspace is None)
        )
        scenario_passed = (
            static_ok
            and external_ok
            and (
                dynamic.get("status") == "passed"
                if dynamic_filter and run_tests
                else True
            )
        )
        if not static_ok:
            failures.append(f"{scenario_id}: static evidence failed")
        if requires_external and not external_ok:
            failures.append(f"{scenario_id}: zero-quota external evidence failed")
        if dynamic_filter and run_tests and dynamic.get("status") != "passed":
            failures.append(f"{scenario_id}: dynamic test failed")
        scenario_status = "passed" if scenario_passed else "failed"
        if run_tests and (
            dynamic.get("status") == "timeout"
            or (requires_external and external.get("status") == "timeout")
        ):
            scenario_status = "timeout"
        duration = round(time.monotonic() - scenario_started, 3)
        results.append(
            {
                "id": scenario_id,
                "domain": scenario.get("domain"),
                "observable_contract": scenario.get("observable_contract"),
                "status": scenario_status,
                "duration_secs": duration,
                "static_evidence": evidence,
                "dynamic_test": dynamic,
                "requires_external_zero_quota_evidence": requires_external,
            }
        )
        if progress and run_tests:
            emit_progress(
                "PASS" if scenario_status == "passed" else scenario_status.upper(),
                scenario_id,
                duration,
            )

    total = len(results)
    passed_count = sum(1 for item in results if item["status"] == "passed")
    static_percent = round((passed_count / total) * 100, 2) if total else 0.0
    selection_complete = not requested and len(results) == len(all_scenarios)
    full_evidence_ready = (
        run_tests
        and selection_complete
        and context_workspace is not None
        and external.get("status") == "passed"
        and all(item["status"] == "passed" for item in results)
    )
    full_percent = 100.0 if full_evidence_ready else None
    failures = list(dict.fromkeys(failures))
    return {
        "status": "passed" if not failures else "failed",
        "schema_version": 1,
        "goal": contract.get("goal"),
        "measurement": {
            "basis": "declared_in_scope_scenarios_only",
            "in_scope_total": total,
            "in_scope_passed": passed_count,
            "contract_scenario_total": len(all_scenarios),
            "selection_complete": selection_complete,
            "selected_scenarios": [item["id"] for item in results],
            "static_or_requested_evidence_percent": static_percent,
            "defined_scope_conformance_percent": full_percent,
            "full_conformance_evidence_ready": full_evidence_ready,
            "does_not_claim_whole_codex_equivalence": True,
        },
        "invariants": contract.get("invariants"),
        "external_zero_quota_evidence": external,
        "scenarios": results,
        "explicitly_out_of_scope": contract.get("explicitly_out_of_scope") or [],
        "failed_checks": failures,
        "mutations_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--context-workspace", type=Path)
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Run only the named scenario. Repeat for multiple scenarios.",
    )
    parser.add_argument(
        "--scenario-timeout-secs",
        type=int,
        default=180,
        help="Independent timeout for each behavioral scenario.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="Write the final machine-readable report to this path.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    contract_path = args.contract.resolve() if args.contract else root / CONTRACT
    try:
        report = evaluate(
            root,
            load_json(contract_path),
            run_tests=args.run_tests,
            context_workspace=(
                args.context_workspace.resolve() if args.context_workspace else None
            ),
            selected_scenarios=set(args.scenario),
            scenario_timeout_secs=max(1, args.scenario_timeout_secs),
            progress=args.run_tests,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        else:
            print(f"observable conformance failed: {exc}", file=sys.stderr)
        return 1
    if args.report_file:
        report_path = args.report_file.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.json and args.run_tests:
        print("RESULT_JSON " + json.dumps(report, ensure_ascii=False), flush=True)
    elif args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["scenarios"]:
            print(f"[{item['status'].upper():6}] {item['id']}")
        print(
            "defined_scope_conformance_percent="
            + str(report["measurement"]["defined_scope_conformance_percent"])
        )
        print(f"status={report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
