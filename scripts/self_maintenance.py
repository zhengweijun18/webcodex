#!/usr/bin/env python3
"""Self-maintaining, zero-Codex-quota compatibility orchestration for WebCodex."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


POLICY = Path("docs/agent/self-maintenance-policy.json")
PARITY_CHECKER = Path("scripts/check_native_semantic_parity.py")
UPSTREAM_CHECKER = Path("scripts/check_upstream_compat.py")
STATE_RELATIVE = Path("artifacts/outputs/webcodex-codex-parity/target-mode-state.json")
WATCH_STATE_SCHEMA = "webcodex-self-maintenance-watch-state.v1"
WATCH_EVENT_SCHEMA = "webcodex-self-maintenance-event.v1"
DEFAULT_WATCH_STATE_ROOT = (
    Path.home()
    / "Library/Application Support/dev.webcodex.desktop/self-maintenance"
)


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 60,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    path_entries = [
        str(Path.home() / ".cargo/bin"),
        str(Path.home() / ".local/bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        merged.get("PATH", ""),
    ]
    merged["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)
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


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def load_policy(root: Path, policy_path: Path | None = None) -> dict:
    path = policy_path or root / POLICY
    policy = load_json(path)
    if policy.get("schema_version") != 1:
        raise RuntimeError("unsupported self-maintenance policy schema")
    if policy.get("kind") != "webcodex-self-maintenance-policy":
        raise RuntimeError("unexpected self-maintenance policy kind")
    invariants = policy.get("invariants") or {}
    required = {
        "upstream_first": True,
        "codex_role": "reference_only",
        "native_codex_model_quota_budget": 0,
        "codex_model_fallback_allowed": False,
        "real_branch_mutation_during_rehearsal": False,
        "maintenance_autopilot_default_mutation": False,
    }
    failures = [
        f"{key}={invariants.get(key)!r}"
        for key, expected in required.items()
        if invariants.get(key) != expected
    ]
    if failures:
        raise RuntimeError("self-maintenance invariant drift: " + ", ".join(failures))
    watcher = policy.get("watcher") or {}
    watcher_expected = {
        "execution_model": "external_scheduler_one_shot",
        "baseline_advancement": "explicit_ack_only",
        "zero_quota_drift": "blocking_fail_closed",
        "desktop_mutation": False,
        "real_branch_mutation": False,
    }
    watcher_failures = [
        f"{key}={watcher.get(key)!r}"
        for key, expected in watcher_expected.items()
        if watcher.get(key) != expected
    ]
    if watcher_failures:
        raise RuntimeError(
            "self-maintenance watcher invariant drift: " + ", ".join(watcher_failures)
        )
    return policy


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace watcher state symlink: {path}")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)
    return path


@contextmanager
def watch_state_lock(state_root: Path):
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "watcher.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def watch_state_path(state_root: Path) -> Path:
    return state_root / "watcher-state.json"


def watch_history_path(state_root: Path) -> Path:
    return state_root / "maintenance-events.jsonl"


def read_watch_state(state_root: Path) -> dict | None:
    path = watch_state_path(state_root)
    if not path.is_file():
        return None
    value = load_json(path)
    if value.get("schema") != WATCH_STATE_SCHEMA:
        raise RuntimeError(f"unsupported watcher state schema: {value.get('schema')!r}")
    return value


def append_watch_event(state_root: Path, event: dict) -> tuple[Path, bool]:
    path = watch_history_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"refusing to append watcher history symlink: {path}")
    event_id = event.get("event_id")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if existing.get("event_id") == event_id:
                return path, False
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)
    return path, True


def ref_file_text(root: Path, ref: str, relative: str) -> str | None:
    result = run(
        ["git", "show", f"{ref}:{relative}"],
        cwd=root,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def probe_ref(root: Path, ref: str, satisfaction: dict) -> tuple[bool, dict]:
    alternatives = satisfaction.get("alternatives") or []
    evidence = []
    for alternative in alternatives:
        relative = alternative.get("path")
        tokens = alternative.get("all_tokens") or []
        text = ref_file_text(root, ref, relative)
        matched = [] if text is None else [token for token in tokens if token in text]
        item = {
            "path": relative,
            "exists": text is not None,
            "matched": matched,
            "required": tokens,
        }
        evidence.append(item)
        if text is not None and len(matched) == len(tokens):
            return True, {"strategy": satisfaction.get("strategy"), "evidence": evidence}
    return False, {"strategy": satisfaction.get("strategy"), "evidence": evidence}


def probe_tree(root: Path, satisfaction: dict) -> tuple[bool, dict]:
    evidence = []
    for alternative in satisfaction.get("alternatives") or []:
        relative = alternative.get("path")
        tokens = alternative.get("all_tokens") or []
        path = root / relative
        if not path.is_file():
            evidence.append(
                {"path": relative, "exists": False, "matched": [], "required": tokens}
            )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matched = [token for token in tokens if token in text]
        evidence.append(
            {
                "path": relative,
                "exists": True,
                "matched": matched,
                "required": tokens,
            }
        )
        if len(matched) == len(tokens):
            return True, {"strategy": satisfaction.get("strategy"), "evidence": evidence}
    return False, {"strategy": satisfaction.get("strategy"), "evidence": evidence}


def local_paths_differ(
    root: Path,
    branch: str,
    upstream_ref: str,
    paths: list[str],
) -> bool:
    result = run(
        ["git", "diff", "--quiet", f"{upstream_ref}...{branch}", "--", *paths],
        cwd=root,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.returncode == 1


def local_implementation_present(
    root: Path,
    branch: str,
    upstream_ref: str,
    unit: dict,
) -> tuple[bool, dict]:
    presence = unit.get("local_presence")
    if isinstance(presence, dict):
        present, evidence = probe_ref(root, branch, presence)
        return present, {"strategy": "semantic_tokens", **evidence}
    paths = unit.get("local_implementation_paths") or []
    present = local_paths_differ(root, branch, upstream_ref, paths)
    return present, {"strategy": "upstream_diff", "paths": paths}


def assess_units(root: Path, policy: dict, branch: str, upstream_ref: str) -> list[dict]:
    results = []
    for unit in policy.get("local_units") or []:
        upstream_ok, upstream_evidence = probe_ref(
            root, upstream_ref, unit.get("upstream_satisfaction") or {}
        )
        paths = unit.get("local_implementation_paths") or []
        local_present, local_evidence = local_implementation_present(
            root, branch, upstream_ref, unit
        )
        if upstream_ok and not local_present:
            category = "upstream_native"
        elif upstream_ok and local_present:
            category = "upstream_can_replace_local"
        elif not upstream_ok and local_present:
            category = "required_local"
        else:
            category = "needs_human_review"
        results.append(
            {
                "id": unit.get("id"),
                "capability_id": unit.get("capability_id"),
                "purpose": unit.get("purpose"),
                "upstream_satisfies": upstream_ok,
                "upstream_evidence": upstream_evidence,
                "local_implementation_present": local_present,
                "local_presence_evidence": local_evidence,
                "category": category,
                "fallback": unit.get("fallback"),
                "verification": unit.get("verification"),
                "retirement": unit.get("retirement"),
                "rollback": unit.get("rollback"),
                "local_implementation_paths": paths,
            }
        )
    return results


def run_parity_report_raw(root: Path, context_workspace: Path | None) -> tuple[dict, int]:
    command = [sys.executable, str(root / PARITY_CHECKER), "--root", str(root), "--json"]
    if context_workspace is not None:
        command.extend(["--context-workspace", str(context_workspace)])
    result = run(command, cwd=root, timeout=30, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("native semantic parity checker returned invalid JSON") from exc
    return payload, result.returncode


def run_parity_report(root: Path, context_workspace: Path | None) -> dict:
    payload, returncode = run_parity_report_raw(root, context_workspace)
    if returncode != 0 or payload.get("status") != "passed":
        raise RuntimeError(
            "native semantic parity failed: "
            + "; ".join(payload.get("failed_checks") or [payload.get("error") or "unknown"])
        )
    return payload


def reference_surface(root: Path, context_workspace: Path | None) -> dict:
    observable_path = root / "docs/agent/observable-codex-runtime-contract.json"
    observable = load_json(observable_path) if observable_path.is_file() else {}
    surface = {
        "observable_contract_goal": observable.get("goal"),
        "observable_contract_fingerprint": (
            fingerprint(observable) if observable else None
        ),
        "observable_in_scope_scenarios": len(
            observable.get("in_scope_scenarios") or []
        ),
    }
    if context_workspace is None:
        return surface
    path = context_workspace / STATE_RELATIVE
    if not path.is_file():
        return surface
    value = load_json(path)
    provider = value.get("provider") or {}
    direct = value.get("direct_mcp") or {}
    native = value.get("native_runtime") or {}
    surface.update({
        "schema": value.get("schema"),
        "verified_at": value.get("verified_at"),
        "bridge_version": provider.get("bridge_version"),
        "bridge_tools": sorted(provider.get("tools") or []),
        "direct_mcp_providers": sorted((direct.get("providers") or {}).keys()),
        "configured_mcp_count": native.get("configured_mcp_count"),
        "enabled_plugin_count": native.get("enabled_plugin_count"),
        "permission_profile": native.get("permission_profile"),
        "experience_gaps": value.get("experience_gaps") or {},
    })
    return surface


def categorize_parity(parity: dict, units: list[dict]) -> list[dict]:
    by_capability = {item["capability_id"]: item for item in units}
    results = []
    for item in parity.get("coverage") or []:
        capability_id = item.get("id")
        local = by_capability.get(capability_id)
        route = item.get("resolved_route")
        evidence = item.get("evidence_status")
        if item.get("parity_target") == "intentional_gap":
            category = "intentionally_unavailable"
        elif local and local.get("category") == "upstream_can_replace_local":
            category = "upstream_can_replace_local"
        elif route == "zero_quota_bridge" and evidence == "passed":
            category = "zero_quota_adaptable"
        elif route == "webcodex_native" and evidence == "passed":
            category = "already_supported"
        elif item.get("required") and route in (None, "unavailable"):
            category = "needs_human_review"
        else:
            category = "already_supported"
        results.append(
            {
                "id": capability_id,
                "category": category,
                "route": route,
                "required": item.get("required"),
                "evidence_status": evidence,
                "codex_model_fallback_allowed": False,
            }
        )
    return results


def semantic_diff(current: dict, baseline_path: Path | None) -> dict:
    if baseline_path is None:
        return {"status": "not_requested"}
    baseline = load_json(baseline_path)
    old = baseline.get("reference_surface")
    new = current.get("reference_surface")
    if old is None or new is None:
        return {
            "status": "unavailable",
            "reason": "both baseline and current reports need reference_surface",
        }
    changed = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changed[key] = {"before": old.get(key), "after": new.get(key)}
    old_units = {
        item.get("id"): item.get("category") for item in baseline.get("local_units") or []
    }
    new_units = {
        item.get("id"): item.get("category") for item in current.get("local_units") or []
    }
    unit_changes = {}
    for key in sorted(set(old_units) | set(new_units)):
        if old_units.get(key) != new_units.get(key):
            unit_changes[key] = {
                "before": old_units.get(key),
                "after": new_units.get(key),
            }
    return {
        "status": "changed" if changed or unit_changes else "unchanged",
        "reference_surface_changes": changed,
        "local_unit_changes": unit_changes,
    }


def build_inventory(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    context_workspace: Path | None,
    baseline: Path | None = None,
) -> dict:
    branch_sha = git(root, "rev-parse", branch)
    upstream_sha = git(root, "rev-parse", upstream_ref)
    units = assess_units(root, policy, branch, upstream_ref)
    parity = run_parity_report(root, context_workspace)
    payload = {
        "status": "passed",
        "schema_version": 1,
        "goal": policy.get("goal"),
        "branch": branch,
        "branch_sha": branch_sha,
        "upstream_ref": upstream_ref,
        "upstream_sha": upstream_sha,
        "invariants": {
            "native_codex_model_quota_budget": 0,
            "codex_model_fallback_allowed": False,
        },
        "watcher": policy.get("watcher") or {},
        "local_units": units,
        "parity_gaps": categorize_parity(parity, units),
        "reference_surface": reference_surface(root, context_workspace),
        "mutations_performed": False,
    }
    payload["semantic_diff"] = semantic_diff(payload, baseline)
    return payload


def stable_reference_surface(surface: dict | None) -> dict | None:
    if surface is None:
        return None
    return {
        key: value
        for key, value in surface.items()
        if key not in {"verified_at"}
    }


def retirement_trigger(inventory: dict) -> str:
    return fingerprint(
        {
            "branch_sha": inventory.get("branch_sha"),
            "upstream_sha": inventory.get("upstream_sha"),
            "units": [
                {
                    "id": item.get("id"),
                    "category": item.get("category"),
                    "upstream_satisfies": item.get("upstream_satisfies"),
                    "local_implementation_present": item.get("local_implementation_present"),
                }
                for item in inventory.get("local_units") or []
            ],
        }
    )


def retirement_outcomes_for_watch(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    inventory: dict,
    prior_observed: dict | None,
) -> tuple[dict[str, str], bool]:
    trigger = retirement_trigger(inventory)
    if (
        prior_observed
        and prior_observed.get("retirement_trigger") == trigger
        and isinstance(prior_observed.get("retirement_outcomes"), dict)
    ):
        return dict(prior_observed["retirement_outcomes"]), True

    outcomes: dict[str, str] = {}
    verification_ids = set()
    for item in inventory.get("local_units") or []:
        unit_id = item.get("id")
        category = item.get("category")
        if category == "upstream_can_replace_local":
            verification_ids.add(unit_id)
        elif category == "required_local":
            outcomes[unit_id] = "required_local"
        elif category == "upstream_native":
            outcomes[unit_id] = "upstream_native"
        else:
            outcomes[unit_id] = "needs_human_review"

    if verification_ids:
        report = retirement_report(
            root,
            policy,
            branch,
            upstream_ref,
            verification_ids,
        )
        for item in report.get("results") or []:
            outcomes[item.get("id")] = item.get("outcome") or "needs_human_review"
    return outcomes, False


def watch_semantic_snapshot(
    inventory: dict,
    retirement_outcomes: dict[str, str],
) -> dict:
    units = {}
    for item in inventory.get("local_units") or []:
        unit_id = item.get("id")
        units[unit_id] = {
            "category": item.get("category"),
            "retirement_outcome": retirement_outcomes.get(unit_id),
            "upstream_satisfies": item.get("upstream_satisfies"),
            "local_implementation_present": item.get("local_implementation_present"),
        }
    parity = {}
    for item in inventory.get("parity_gaps") or []:
        parity[item.get("id")] = {
            "category": item.get("category"),
            "route": item.get("route"),
            "required": item.get("required"),
            "evidence_status": item.get("evidence_status"),
            "codex_model_fallback_allowed": item.get("codex_model_fallback_allowed"),
        }
    return {
        "schema_version": 1,
        "goal": inventory.get("goal"),
        "branch": inventory.get("branch"),
        "branch_sha": inventory.get("branch_sha"),
        "upstream_ref": inventory.get("upstream_ref"),
        "upstream_sha": inventory.get("upstream_sha"),
        "invariants": inventory.get("invariants"),
        "local_units": units,
        "parity_gaps": parity,
        "reference_surface": stable_reference_surface(inventory.get("reference_surface")),
    }


def dict_changes(before: dict | None, after: dict | None) -> dict:
    before = before or {}
    after = after or {}
    changes = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changes[key] = {"before": before.get(key), "after": after.get(key)}
    return changes


def watch_semantic_diff(baseline: dict, current: dict) -> dict:
    upstream_change = None
    if baseline.get("upstream_sha") != current.get("upstream_sha"):
        upstream_change = {
            "before": baseline.get("upstream_sha"),
            "after": current.get("upstream_sha"),
        }
    branch_change = None
    if baseline.get("branch_sha") != current.get("branch_sha"):
        branch_change = {
            "before": baseline.get("branch_sha"),
            "after": current.get("branch_sha"),
        }
    invariant_changes = dict_changes(
        baseline.get("invariants"),
        current.get("invariants"),
    )
    unit_changes = dict_changes(
        baseline.get("local_units"),
        current.get("local_units"),
    )
    parity_changes = dict_changes(
        baseline.get("parity_gaps"),
        current.get("parity_gaps"),
    )
    reference_changes = dict_changes(
        baseline.get("reference_surface"),
        current.get("reference_surface"),
    )

    human_review = []
    for unit_id, item in (current.get("local_units") or {}).items():
        if (
            item.get("category") == "needs_human_review"
            or item.get("retirement_outcome") == "needs_human_review"
        ):
            human_review.append(f"local:{unit_id}")
    for capability_id, item in (current.get("parity_gaps") or {}).items():
        if item.get("category") == "needs_human_review":
            human_review.append(f"parity:{capability_id}")

    changed = any(
        (
            upstream_change,
            branch_change,
            invariant_changes,
            unit_changes,
            parity_changes,
            reference_changes,
        )
    )
    blocking = bool(invariant_changes or human_review)
    severity = "critical" if invariant_changes else ("high" if human_review else "normal")
    return {
        "status": "changed" if changed else "unchanged",
        "severity": severity,
        "blocking": blocking,
        "upstream_sha": upstream_change,
        "tracked_branch_sha": branch_change,
        "invariant_changes": invariant_changes,
        "local_unit_changes": unit_changes,
        "parity_changes": parity_changes,
        "reference_surface_changes": reference_changes,
        "needs_human_review": sorted(human_review),
    }


def parity_failure_event(
    report: dict,
    baseline: dict | None,
) -> dict:
    evidence = {
        "failed_checks": report.get("failed_checks") or [report.get("error") or "unknown"],
        "external_evidence": report.get("external_evidence"),
        "invariant": report.get("invariant"),
    }
    event_key = {
        "kind": "zero_quota_or_parity_drift",
        "baseline": fingerprint(baseline) if baseline else None,
        "evidence": evidence,
    }
    return {
        "schema": WATCH_EVENT_SCHEMA,
        "event_id": "maintenance-" + fingerprint(event_key)[:24],
        "created_at": int(time.time()),
        "kind": "zero_quota_or_parity_drift",
        "severity": "critical",
        "blocking": True,
        "requires_human_review": True,
        "baseline_fingerprint": fingerprint(baseline) if baseline else None,
        "current_fingerprint": None,
        "diff": {
            "status": "blocked",
            "severity": "critical",
            "blocking": True,
            "zero_quota_or_parity_failure": evidence,
        },
        "current_snapshot": None,
    }


def human_review_event(report: dict, baseline: dict | None) -> dict:
    evidence = {
        "needs_human_review": sorted(report.get("needs_human_review") or []),
    }
    event_key = {
        "kind": "maintenance_human_review",
        "baseline": fingerprint(baseline) if baseline else None,
        "evidence": evidence,
    }
    return {
        "schema": WATCH_EVENT_SCHEMA,
        "event_id": "maintenance-" + fingerprint(event_key)[:24],
        "created_at": int(time.time()),
        "kind": "maintenance_human_review",
        "severity": "high",
        "blocking": True,
        "requires_human_review": True,
        "baseline_fingerprint": fingerprint(baseline) if baseline else None,
        "current_fingerprint": None,
        "diff": {
            "status": "blocked",
            "severity": "high",
            "blocking": True,
            **evidence,
        },
        "current_snapshot": None,
    }


def compatibility_event(baseline: dict, current: dict, diff: dict) -> dict:
    event_key = {
        "kind": "compatibility_semantic_change",
        "baseline": fingerprint(baseline),
        "current": fingerprint(current),
        "diff": diff,
    }
    return {
        "schema": WATCH_EVENT_SCHEMA,
        "event_id": "maintenance-" + fingerprint(event_key)[:24],
        "created_at": int(time.time()),
        "kind": "compatibility_semantic_change",
        "severity": diff.get("severity"),
        "blocking": bool(diff.get("blocking")),
        "requires_human_review": bool(diff.get("needs_human_review")),
        "baseline_fingerprint": fingerprint(baseline),
        "current_fingerprint": fingerprint(current),
        "diff": diff,
        "current_snapshot": current,
    }


def watch_observe(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    context_workspace: Path | None,
    prior_state: dict | None,
) -> dict:
    parity, parity_code = run_parity_report_raw(root, context_workspace)
    if parity_code != 0 or parity.get("status") != "passed":
        return {
            "status": "blocked",
            "parity_failure": parity,
        }
    inventory = build_inventory(
        root,
        policy,
        branch,
        upstream_ref,
        context_workspace,
    )
    prior_observed = (prior_state or {}).get("last_observed")
    outcomes, reused = retirement_outcomes_for_watch(
        root,
        policy,
        branch,
        upstream_ref,
        inventory,
        prior_observed,
    )
    review = [
        f"local:{item.get('id')}"
        for item in inventory.get("local_units") or []
        if item.get("category") == "needs_human_review"
    ]
    review.extend(
        f"parity:{item.get('id')}"
        for item in inventory.get("parity_gaps") or []
        if item.get("category") == "needs_human_review"
    )
    review.extend(
        f"retirement:{unit_id}"
        for unit_id, outcome in outcomes.items()
        if outcome == "needs_human_review"
    )
    if review:
        return {
            "status": "blocked",
            "needs_human_review": sorted(set(review)),
        }
    return {
        "status": "passed",
        "snapshot": watch_semantic_snapshot(inventory, outcomes),
        "retirement_trigger": retirement_trigger(inventory),
        "retirement_outcomes": outcomes,
        "retirement_verification_reused": reused,
    }


def watch_check(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    context_workspace: Path | None,
    state_root: Path,
) -> dict:
    clean_real_worktree_required(root)
    with watch_state_lock(state_root):
        state = read_watch_state(state_root)
        observed = watch_observe(
            root,
            policy,
            branch,
            upstream_ref,
            context_workspace,
            state,
        )
        now = int(time.time())
        baseline = (state or {}).get("baseline")

        if observed.get("status") != "passed":
            existing = (state or {}).get("pending_event")
            if observed.get("parity_failure") is not None:
                event = parity_failure_event(
                    observed.get("parity_failure") or {},
                    baseline,
                )
            else:
                event = human_review_event(observed, baseline)
            if existing and existing.get("event_id") == event["event_id"]:
                event = existing
                appended = False
            else:
                _, appended = append_watch_event(state_root, event)
            next_state = {
                "schema": WATCH_STATE_SCHEMA,
                "baseline": baseline,
                "last_observed": (state or {}).get("last_observed"),
                "pending_event": event,
                "last_acknowledged_event": (state or {}).get("last_acknowledged_event"),
                "last_checked_at": now,
                "last_check_status": "blocked",
            }
            atomic_write_json(watch_state_path(state_root), next_state)
            return {
                "status": "blocked",
                "event": event,
                "event_appended": appended,
                "state": str(watch_state_path(state_root)),
                "history": str(watch_history_path(state_root)),
                "native_codex_model_quota_budget": 0,
                "desktop_mutations_performed": False,
                "real_branch_mutations_performed": False,
            }

        current = observed["snapshot"]
        observed_record = {
            "snapshot": current,
            "snapshot_fingerprint": fingerprint(current),
            "retirement_trigger": observed["retirement_trigger"],
            "retirement_outcomes": observed["retirement_outcomes"],
            "observed_at": now,
        }
        if baseline is None:
            next_state = {
                "schema": WATCH_STATE_SCHEMA,
                "baseline": current,
                "last_observed": observed_record,
                "pending_event": None,
                "last_acknowledged_event": (state or {}).get("last_acknowledged_event"),
                "last_checked_at": now,
                "last_check_status": "baseline_initialized",
            }
            atomic_write_json(watch_state_path(state_root), next_state)
            return {
                "status": "baseline_initialized",
                "baseline_fingerprint": fingerprint(current),
                "retirement_verification_reused": observed["retirement_verification_reused"],
                "state": str(watch_state_path(state_root)),
                "history": str(watch_history_path(state_root)),
                "native_codex_model_quota_budget": 0,
                "desktop_mutations_performed": False,
                "real_branch_mutations_performed": False,
            }

        diff = watch_semantic_diff(baseline, current)
        if diff["status"] == "unchanged":
            next_state = {
                "schema": WATCH_STATE_SCHEMA,
                "baseline": baseline,
                "last_observed": observed_record,
                "pending_event": None,
                "last_acknowledged_event": (state or {}).get("last_acknowledged_event"),
                "last_checked_at": now,
                "last_check_status": "unchanged",
            }
            atomic_write_json(watch_state_path(state_root), next_state)
            return {
                "status": "unchanged",
                "baseline_fingerprint": fingerprint(baseline),
                "retirement_verification_reused": observed["retirement_verification_reused"],
                "state": str(watch_state_path(state_root)),
                "history": str(watch_history_path(state_root)),
                "native_codex_model_quota_budget": 0,
                "desktop_mutations_performed": False,
                "real_branch_mutations_performed": False,
            }

        candidate = compatibility_event(baseline, current, diff)
        existing = (state or {}).get("pending_event")
        if existing and existing.get("event_id") == candidate["event_id"]:
            event = existing
            appended = False
        else:
            event = candidate
            _, appended = append_watch_event(state_root, event)
        next_state = {
            "schema": WATCH_STATE_SCHEMA,
            "baseline": baseline,
            "last_observed": observed_record,
            "pending_event": event,
            "last_acknowledged_event": (state or {}).get("last_acknowledged_event"),
            "last_checked_at": now,
            "last_check_status": "blocked" if event["blocking"] else "change_detected",
        }
        atomic_write_json(watch_state_path(state_root), next_state)
        return {
            "status": "blocked" if event["blocking"] else "change_detected",
            "event": event,
            "event_appended": appended,
            "retirement_verification_reused": observed["retirement_verification_reused"],
            "state": str(watch_state_path(state_root)),
            "history": str(watch_history_path(state_root)),
            "native_codex_model_quota_budget": 0,
            "desktop_mutations_performed": False,
            "real_branch_mutations_performed": False,
        }


def watch_status(state_root: Path) -> dict:
    state = read_watch_state(state_root)
    return {
        "status": "uninitialized" if state is None else "passed",
        "state": state,
        "state_path": str(watch_state_path(state_root)),
        "history_path": str(watch_history_path(state_root)),
        "mutations_performed": False,
    }


def watch_ack(state_root: Path, event_id: str) -> dict:
    with watch_state_lock(state_root):
        state = read_watch_state(state_root)
        if state is None:
            raise RuntimeError("watcher state is not initialized")
        pending = state.get("pending_event")
        if not pending:
            raise RuntimeError("there is no pending maintenance event")
        if pending.get("event_id") != event_id:
            raise RuntimeError(
                f"pending event is {pending.get('event_id')}; refusing stale acknowledgement"
            )
        if pending.get("blocking"):
            raise RuntimeError(
                "blocking maintenance events cannot advance the baseline; resolve the drift first"
            )
        current = pending.get("current_snapshot")
        if not isinstance(current, dict):
            raise RuntimeError("pending event does not contain an acknowledgeable snapshot")
        now = int(time.time())
        state["baseline"] = current
        state["pending_event"] = None
        state["last_acknowledged_event"] = {
            "event_id": event_id,
            "acknowledged_at": now,
            "baseline_fingerprint": fingerprint(current),
        }
        state["last_checked_at"] = now
        state["last_check_status"] = "acknowledged"
        atomic_write_json(watch_state_path(state_root), state)
        return {
            "status": "acknowledged",
            "event_id": event_id,
            "baseline_fingerprint": fingerprint(current),
            "state": str(watch_state_path(state_root)),
            "history": str(watch_history_path(state_root)),
            "desktop_mutations_performed": False,
            "real_branch_mutations_performed": False,
        }


def clean_real_worktree_required(root: Path) -> None:
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("source worktree must be clean before retirement rehearsal")


def add_rebased_worktree(
    root: Path,
    branch: str,
    upstream_ref: str,
    temp_parent: Path,
) -> tuple[Path, dict]:
    worktree = temp_parent / "worktree"
    add = run(
        ["git", "worktree", "add", "--detach", str(worktree), branch],
        cwd=root,
        timeout=60,
        check=False,
    )
    if add.returncode != 0:
        raise RuntimeError((add.stderr or add.stdout).strip()[-3000:])
    merge_base = git(root, "merge-base", branch, upstream_ref)
    rebase = run(
        ["git", "rebase", "--onto", upstream_ref, merge_base, "HEAD"],
        cwd=worktree,
        timeout=180,
        check=False,
    )
    if rebase.returncode != 0:
        conflicts = git(
            worktree, "diff", "--name-only", "--diff-filter=U", check=False
        ).splitlines()
        run(["git", "rebase", "--abort"], cwd=worktree, check=False)
        raise RuntimeError(
            "forward-port conflict: "
            + ", ".join(conflicts)
            + " "
            + (rebase.stderr or rebase.stdout).strip()[-1600:]
        )
    return worktree, {
        "merge_base": merge_base,
        "rebased_head": git(worktree, "rev-parse", "HEAD"),
    }


def restore_upstream_implementation(
    root: Path,
    worktree: Path,
    upstream_ref: str,
    paths: list[str],
) -> list[dict]:
    restored = []
    for relative in paths:
        exists_upstream = (
            run(
                ["git", "cat-file", "-e", f"{upstream_ref}:{relative}"],
                cwd=root,
                check=False,
            ).returncode
            == 0
        )
        target = worktree / relative
        if exists_upstream:
            result = run(
                ["git", "restore", "--source", upstream_ref, "--worktree", "--", relative],
                cwd=worktree,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"failed to restore {relative}")
            restored.append({"path": relative, "action": "restored_from_upstream"})
        elif target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            restored.append({"path": relative, "action": "removed_local_only_path"})
        else:
            restored.append({"path": relative, "action": "absent"})
    return restored


def validation_commands(kind: str) -> list[list[str]]:
    if kind == "vue_lsp_regression":
        return [["bash", "scripts/check_vue_lsp_patch.sh"]]
    if kind == "provider_environment_regression":
        return [
            [
                "cargo",
                "test",
                "--locked",
                "-p",
                "webcodex-runner",
                "--bin",
                "webcodex-runner",
                "mcp_gateway_execution_context_accepts_explicit_cwd_and_env_mapping",
            ],
            [
                "cargo",
                "test",
                "--locked",
                "-p",
                "webcodex-runner",
                "--bin",
                "webcodex-runner",
                "provider_execution_context_is_explicit_cleared_and_private",
            ],
        ]
    if kind == "provider_lifecycle_regression":
        return [
            [
                "cargo",
                "test",
                "--locked",
                "-p",
                "webcodex-runner",
                "--bin",
                "webcodex-runner",
                "provider_status_is_passive_and_tracks_connection_lifecycle",
            ],
            [
                "cargo",
                "test",
                "--locked",
                "-p",
                "webcodex-runner",
                "--bin",
                "webcodex-runner",
                "provider_status_reaps_an_exited_connection_without_restarting_it",
            ],
        ]
    raise RuntimeError(f"unknown retirement verification: {kind}")


def macos_test_environment(root: Path) -> tuple[dict[str, str], dict]:
    env = {
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(root / "target/self-maintenance-cargo"),
    }
    evidence = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "sdk_overlay": False,
    }
    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        return env, evidence
    sdk_result = run(["xcrun", "--show-sdk-path"], cwd=root, check=False)
    sdk = Path(sdk_result.stdout.strip()) if sdk_result.returncode == 0 else None
    if sdk is None or not sdk.is_dir():
        return env, {**evidence, "sdk": None}
    framework_root = sdk / "System/Library/Frameworks"
    core_tbd = framework_root / "CoreGraphics.framework/Versions/A/CoreGraphics.tbd"
    needs_overlay = not (framework_root / "AVFAudio.framework").is_dir()
    core_text = core_tbd.read_text(errors="replace") if core_tbd.is_file() else ""
    missing_core = [
        symbol
        for symbol in ("_CGPreflightPostEventAccess", "_CGPreflightScreenCaptureAccess")
        if core_text and symbol not in core_text
    ]
    needs_overlay = needs_overlay or bool(missing_core)
    if not needs_overlay:
        return env, {**evidence, "sdk": str(sdk)}
    overlay = root / "target/self-maintenance-sdk-overlay/Frameworks"
    shutil.rmtree(overlay.parent, ignore_errors=True)
    avf = overlay / "AVFAudio.framework"
    avf.mkdir(parents=True, exist_ok=True)
    (avf / "AVFAudio.tbd").write_text(
        """--- !tapi-tbd-v3
archs:           [ x86_64 ]
platform:        macosx
install-name:    '/System/Library/Frameworks/AVFAudio.framework/Versions/A/AVFAudio'
current-version: 1
compatibility-version: 1
exports:
  - archs:           [ x86_64 ]
    symbols:         [ ]
...
"""
    )
    if core_tbd.is_file():
        core_dir = overlay / "CoreGraphics.framework"
        core_dir.mkdir(parents=True, exist_ok=True)
        copied = core_dir / "CoreGraphics.tbd"
        shutil.copy2(core_tbd, copied)
        text = copied.read_text(errors="replace")
        missing = [
            symbol
            for symbol in ("_CGPreflightPostEventAccess", "_CGPreflightScreenCaptureAccess")
            if symbol not in text
        ]
        if missing:
            needle = "    symbols:         [ "
            if needle not in text:
                raise RuntimeError("CoreGraphics TBD symbols list not found for test overlay")
            text = text.replace(needle, needle + ", ".join(missing) + ", ", 1)
            copied.write_text(text)
    extra = f"-C link-arg=-F{overlay}"
    current = os.environ.get("RUSTFLAGS", "").strip()
    env["RUSTFLAGS"] = extra if not current else f"{current} {extra}"
    return env, {
        **evidence,
        "sdk": str(sdk),
        "sdk_overlay": True,
        "overlay": str(overlay),
        "missing_core_symbols": missing_core,
    }


def run_retirement_verification(root: Path, worktree: Path, kind: str) -> dict:
    commands = validation_commands(kind)
    results = []
    env, environment = macos_test_environment(root)
    for command in commands:
        completed = run(
            command,
            cwd=worktree,
            timeout=420,
            check=False,
            env=env,
        )
        item = {
            "command": command,
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout[-1600:],
            "stderr_tail": completed.stderr[-1600:],
        }
        results.append(item)
        if completed.returncode != 0:
            return {
                "status": "failed",
                "strength": "executed_regression",
                "environment": environment,
                "commands": results,
            }
    return {
        "status": "passed",
        "strength": "executed_regression",
        "environment": environment,
        "commands": results,
    }


def rehearse_retirement_unit(
    root: Path,
    policy: dict,
    unit: dict,
    branch: str,
    upstream_ref: str,
) -> dict:
    upstream_ok, upstream_evidence = probe_ref(
        root, upstream_ref, unit.get("upstream_satisfaction") or {}
    )
    paths = unit.get("local_implementation_paths") or []
    local_present, local_evidence = local_implementation_present(
        root, branch, upstream_ref, unit
    )
    base = {
        "id": unit.get("id"),
        "capability_id": unit.get("capability_id"),
        "upstream_satisfies": upstream_ok,
        "upstream_evidence": upstream_evidence,
        "local_implementation_present": local_present,
        "local_presence_evidence": local_evidence,
        "verification": unit.get("verification"),
        "mutations_performed_on_real_branch": False,
    }
    if upstream_ok and not local_present:
        return {**base, "outcome": "upstream_native", "regression": {"status": "not_needed"}}
    if not upstream_ok and local_present:
        return {**base, "outcome": "required_local", "regression": {"status": "not_run"}}
    if not upstream_ok and not local_present:
        return {
            **base,
            "outcome": "needs_human_review",
            "reason": "required capability is absent from both upstream and local diff",
            "regression": {"status": "not_run"},
        }

    target = root / "target" / "self-maintenance"
    target.mkdir(parents=True, exist_ok=True)
    temp_parent = Path(tempfile.mkdtemp(prefix=f"retire-{unit['id']}-", dir=target))
    worktree: Path | None = None
    try:
        worktree, rebase = add_rebased_worktree(
            root, branch, upstream_ref, temp_parent
        )
        restored = restore_upstream_implementation(
            root, worktree, upstream_ref, paths
        )
        tree_ok, tree_evidence = probe_tree(
            worktree, unit.get("upstream_satisfaction") or {}
        )
        if not tree_ok:
            return {
                **base,
                "outcome": "needs_human_review",
                "reason": "upstream implementation did not satisfy policy after retirement restore",
                "rebase": rebase,
                "restored": restored,
                "post_restore_probe": tree_evidence,
                "regression": {"status": "not_run"},
            }
        regression = run_retirement_verification(
            root, worktree, unit.get("verification")
        )
        outcome = "retirable" if regression["status"] == "passed" else "needs_human_review"
        return {
            **base,
            "outcome": outcome,
            "rebase": rebase,
            "restored": restored,
            "post_restore_probe": tree_evidence,
            "regression": regression,
        }
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return {
            **base,
            "outcome": "needs_human_review",
            "reason": str(exc),
            "regression": {"status": "not_run"},
        }
    finally:
        if worktree is not None:
            run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=root,
                timeout=60,
                check=False,
            )
        shutil.rmtree(temp_parent, ignore_errors=True)
        run(["git", "worktree", "prune"], cwd=root, check=False)


def retirement_report(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    unit_filter: set[str] | None = None,
) -> dict:
    clean_real_worktree_required(root)
    units = [
        unit
        for unit in policy.get("local_units") or []
        if not unit_filter or unit.get("id") in unit_filter
    ]
    unknown = sorted(
        (unit_filter or set()) - {unit.get("id") for unit in policy.get("local_units") or []}
    )
    if unknown:
        raise RuntimeError("unknown retirement unit(s): " + ", ".join(unknown))
    results = [
        rehearse_retirement_unit(root, policy, unit, branch, upstream_ref)
        for unit in units
    ]
    return {
        "status": (
            "needs_human_review"
            if any(item["outcome"] == "needs_human_review" for item in results)
            else "passed"
        ),
        "schema_version": 1,
        "branch": branch,
        "upstream_ref": upstream_ref,
        "results": results,
        "mutations_performed_on_real_branch": False,
        "persistent_mutations_performed": False,
    }


def upstream_compatibility(
    root: Path,
    branch: str,
    upstream_ref: str,
    *,
    fetch: bool = False,
    run_checks: bool = False,
) -> dict:
    command = [
        sys.executable,
        str(root / UPSTREAM_CHECKER),
        "--root",
        str(root),
        "--branch",
        branch,
        "--upstream-ref",
        upstream_ref,
        "--json",
    ]
    if fetch:
        command.append("--fetch")
    if run_checks:
        command.append("--run-checks")
    result = run(command, cwd=root, timeout=540, check=False)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("upstream compatibility checker returned invalid JSON") from exc
    return value


def candidate_readiness(root: Path, candidate: Path | None) -> dict:
    if candidate is None:
        return {"status": "not_provided", "safe_to_adopt": False}
    script = root / "scripts/local_desktop_lifecycle.py"
    result = run(
        [sys.executable, str(script), "verify", "--candidate", str(candidate)],
        cwd=root,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return {
            "status": "failed",
            "safe_to_adopt": False,
            "detail": (result.stderr or result.stdout)[-2000:],
        }
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "failed",
            "safe_to_adopt": False,
            "detail": "candidate verifier returned invalid JSON",
        }
    return {
        "status": "validated_candidate",
        "safe_to_adopt": True,
        "identity": value.get("candidate"),
    }


def decide_upgrade(
    compatibility: dict,
    retirement: dict,
    inventory: dict,
) -> tuple[str, list[str]]:
    reasons = []
    if compatibility.get("status") not in {"passed"}:
        reasons.append(f"upstream compatibility is {compatibility.get('status')}")
        return "blocked", reasons
    review = [
        item["id"]
        for item in retirement.get("results") or []
        if item.get("outcome") == "needs_human_review"
    ]
    if review:
        reasons.append("retirement needs review: " + ", ".join(review))
        return "needs_adapter", reasons
    parity_review = [
        item["id"]
        for item in inventory.get("parity_gaps") or []
        if item.get("category") == "needs_human_review"
    ]
    if parity_review:
        reasons.append("parity needs review: " + ", ".join(parity_review))
        return "needs_adapter", reasons
    reasons.append("forward-port rehearsal passed and every tracked capability has a safe route")
    return "safe_to_upgrade", reasons


def autopilot_report(
    root: Path,
    policy: dict,
    branch: str,
    upstream_ref: str,
    context_workspace: Path | None,
    baseline: Path | None,
    candidate: Path | None,
    *,
    fetch: bool = False,
    run_upstream_checks: bool = False,
) -> dict:
    clean_real_worktree_required(root)
    inventory = build_inventory(
        root, policy, branch, upstream_ref, context_workspace, baseline
    )
    compatibility = upstream_compatibility(
        root,
        branch,
        upstream_ref,
        fetch=fetch,
        run_checks=run_upstream_checks,
    )
    retirement = retirement_report(root, policy, branch, upstream_ref)
    decision, reasons = decide_upgrade(compatibility, retirement, inventory)
    candidate_state = candidate_readiness(root, candidate)
    return {
        "status": "passed" if decision == "safe_to_upgrade" else decision,
        "schema_version": 1,
        "goal": policy.get("goal"),
        "decision": decision,
        "reasons": reasons,
        "upstream": compatibility,
        "inventory": inventory,
        "retirement": retirement,
        "candidate": candidate_state,
        "adoption": {
            "automatic": False,
            "operator_confirmation_required": True,
            "candidate_safe_to_adopt": candidate_state.get("safe_to_adopt", False),
        },
        "native_codex_model_quota_budget": 0,
        "codex_model_fallback_allowed": False,
        "mutations_performed_on_real_branch": False,
        "desktop_mutations_performed": False,
        "persistent_mutations_performed": bool(fetch),
        "persistent_mutation_detail": (
            "git fetch updated remote-tracking refs only" if fetch else None
        ),
    }


def emit(value: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if "decision" in value:
        print(f"decision={value['decision']}")
        for reason in value.get("reasons") or []:
            print(f"reason={reason}")
        return
    if value.get("event"):
        event = value["event"]
        print(f"status={value.get('status')}")
        print(f"event={event.get('event_id')}")
        print(f"severity={event.get('severity')}")
        print(f"blocking={event.get('blocking')}")
        return
    if value.get("status") in {
        "baseline_initialized",
        "unchanged",
        "acknowledged",
        "uninitialized",
    }:
        print(f"status={value.get('status')}")
        return
    for item in value.get("results") or value.get("local_units") or []:
        print(f"{item.get('id')}: {item.get('outcome') or item.get('category')}")
    print(f"status={value.get('status')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    def shared(command):
        command.add_argument("--branch")
        command.add_argument("--upstream-ref")
        command.add_argument("--json", action="store_true")

    inventory = sub.add_parser("inventory")
    shared(inventory)
    inventory.add_argument("--context-workspace", type=Path)
    inventory.add_argument("--baseline", type=Path)

    retirement = sub.add_parser("retirement")
    shared(retirement)
    retirement.add_argument("--unit", action="append")

    autopilot = sub.add_parser("autopilot")
    shared(autopilot)
    autopilot.add_argument("--context-workspace", type=Path)
    autopilot.add_argument("--baseline", type=Path)
    autopilot.add_argument("--candidate", type=Path)
    autopilot.add_argument("--fetch", action="store_true")
    autopilot.add_argument("--run-upstream-checks", action="store_true")

    watcher = sub.add_parser("watch-check")
    shared(watcher)
    watcher.add_argument("--context-workspace", type=Path)
    watcher.add_argument("--state-root", type=Path, default=DEFAULT_WATCH_STATE_ROOT)
    watcher.add_argument("--fetch", action="store_true")

    watcher_status = sub.add_parser("watch-status")
    watcher_status.add_argument("--state-root", type=Path, default=DEFAULT_WATCH_STATE_ROOT)
    watcher_status.add_argument("--json", action="store_true")

    watcher_ack = sub.add_parser("watch-ack")
    watcher_ack.add_argument("--state-root", type=Path, default=DEFAULT_WATCH_STATE_ROOT)
    watcher_ack.add_argument("--event-id", required=True)
    watcher_ack.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    policy_path = args.policy.resolve() if args.policy else None
    try:
        policy = load_policy(root, policy_path)
        maintenance = policy.get("maintenance") or {}
        branch = getattr(args, "branch", None) or maintenance.get("default_branch")
        upstream_ref = getattr(args, "upstream_ref", None) or maintenance.get(
            "default_upstream_ref"
        )
        if args.command == "inventory":
            value = build_inventory(
                root,
                policy,
                branch,
                upstream_ref,
                args.context_workspace.resolve() if args.context_workspace else None,
                args.baseline.resolve() if args.baseline else None,
            )
        elif args.command == "retirement":
            value = retirement_report(
                root,
                policy,
                branch,
                upstream_ref,
                set(args.unit or []) or None,
            )
        elif args.command == "autopilot":
            value = autopilot_report(
                root,
                policy,
                branch,
                upstream_ref,
                args.context_workspace.resolve() if args.context_workspace else None,
                args.baseline.resolve() if args.baseline else None,
                args.candidate.resolve() if args.candidate else None,
                fetch=args.fetch,
                run_upstream_checks=args.run_upstream_checks,
            )
        elif args.command == "watch-check":
            if args.fetch:
                if "/" not in upstream_ref:
                    raise RuntimeError(
                        "--fetch requires upstream ref in <remote>/<branch> form"
                    )
                remote, remote_branch = upstream_ref.split("/", 1)
                run(
                    ["git", "fetch", remote, remote_branch, "--tags"],
                    cwd=root,
                    timeout=120,
                )
            value = watch_check(
                root,
                policy,
                branch,
                upstream_ref,
                args.context_workspace.resolve() if args.context_workspace else None,
                args.state_root.expanduser().resolve(),
            )
            value["persistent_mutations_performed"] = bool(args.fetch)
            value["persistent_mutation_detail"] = (
                "git fetch updated remote-tracking refs only" if args.fetch else None
            )
        elif args.command == "watch-status":
            value = watch_status(args.state_root.expanduser().resolve())
        elif args.command == "watch-ack":
            value = watch_ack(
                args.state_root.expanduser().resolve(),
                args.event_id,
            )
        else:
            raise AssertionError(args.command)
        emit(value, args.json)
        if value.get("decision") == "blocked" or value.get("status") in {
            "failed",
            "blocked",
        }:
            return 1
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        else:
            print(f"self-maintenance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
