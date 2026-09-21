#!/usr/bin/env python3
"""Behavioral patch-retirement rehearsal for maintained local fork capabilities.

The real maintenance branch is read-only. Rehearsal happens only in disposable
Git worktrees under target/. A retirement group is eligible only when:
1. the current branch passes its executable behavior verifier;
2. pure upstream passes the same verifier; and
3. a rebased current branch with the group's local implementation paths restored
   from upstream still passes the verifier.

This is deliberately stricter than file-diff absence and never installs,
restarts, publishes, or mutates Desktop/runtime state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


DEFAULT_CONTRACT = Path("docs/agent/local-fork-capability-contract.json")


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 60,
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
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
    )


def git(root: Path, *args: str, check: bool = True) -> str:
    return run(["git", *args], cwd=root, check=check).stdout.strip()


def stable_cargo() -> str | None:
    explicit = os.environ.get("CARGO")
    if explicit:
        return explicit
    found = shutil.which("cargo")
    if found:
        return found
    fallback = Path.home() / ".cargo/bin/cargo"
    return str(fallback) if fallback.is_file() else None


def verifier_environment(root: Path) -> dict[str, str]:
    env = {
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(root / "target/patch-retirement-cargo"),
    }
    cargo = stable_cargo()
    if cargo:
        env["CARGO"] = cargo
        env["PATH"] = str(Path(cargo).parent) + os.pathsep + os.environ.get("PATH", "")
    return env


def clean_real_worktree_required(root: Path) -> None:
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError("real maintenance worktree must be clean before retirement rehearsal")


def load_contract(root: Path, contract_path: Path | None) -> dict[str, Any]:
    path = contract_path or (root / DEFAULT_CONTRACT)
    if not path.is_absolute():
        path = root / path
    value = json.loads(path.read_text(encoding="utf-8"))
    groups = value.get("retirement_groups")
    if not isinstance(groups, list) or not groups:
        raise RuntimeError(f"retirement_groups missing from {path}")
    return value


def group_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group in contract.get("retirement_groups") or []:
        group_id = group.get("id")
        paths = group.get("implementation_paths")
        verifier = group.get("verifier")
        if (
            not isinstance(group_id, str)
            or not group_id
            or not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) and path for path in paths)
            or not isinstance(verifier, dict)
        ):
            raise RuntimeError("invalid retirement group contract")
        for relative in paths:
            if (
                relative.startswith("/")
                or ".." in Path(relative).parts
                or Path(relative).is_absolute()
            ):
                raise RuntimeError(
                    f"retirement group {group_id} has unsafe implementation path: {relative!r}"
                )
        if group_id in result:
            raise RuntimeError(f"duplicate retirement group: {group_id}")
        result[group_id] = group
    return result


def bounded_process_result(
    completed: subprocess.CompletedProcess[str],
    command: list[str],
) -> dict[str, Any]:
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "command": command,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
    }


def run_verifier(
    root: Path,
    candidate_root: Path,
    verifier: dict[str, Any],
) -> dict[str, Any]:
    kind = verifier.get("kind")
    env = verifier_environment(root)
    if kind == "behavioral_capabilities":
        scope = verifier.get("scope", "full")
        if scope not in {"quick", "full"}:
            raise RuntimeError(f"invalid behavioral verifier scope: {scope}")
        command = [
            sys.executable,
            str(root / "scripts/check_behavioral_capabilities.py"),
            "--root",
            str(candidate_root),
            "--scope",
            scope,
            "--json",
        ]
        completed = run(command, cwd=root, timeout=600, env=env, check=False)
        return bounded_process_result(completed, command)
    if kind == "desktop_lifecycle_contract":
        target = candidate_root / "scripts/local_desktop_lifecycle.py"
        if not target.is_file():
            return {
                "status": "failed",
                "exit_code": None,
                "command": [],
                "stdout_tail": "",
                "stderr_tail": f"candidate lifecycle script is missing: {target}",
            }
        harness_root = root / "target/patch-retirement-harness"
        harness_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="desktop-lifecycle-", dir=harness_root) as temp:
            temp_root = Path(temp)
            shutil.copy2(target, temp_root / "local_desktop_lifecycle.py")
            shutil.copy2(
                root / "scripts/test_local_desktop_lifecycle.py",
                temp_root / "test_local_desktop_lifecycle.py",
            )
            command = [sys.executable, str(temp_root / "test_local_desktop_lifecycle.py")]
            completed = run(command, cwd=temp_root, timeout=120, env=env, check=False)
            return bounded_process_result(completed, command)
    raise RuntimeError(f"unknown retirement verifier kind: {kind}")


def path_differs(root: Path, branch: str, upstream_ref: str, relative: str) -> bool:
    diff = run(
        ["git", "diff", "--quiet", upstream_ref, branch, "--", relative],
        cwd=root,
        check=False,
    )
    return diff.returncode == 1


def add_worktree(root: Path, ref: str, parent: Path, name: str) -> Path:
    worktree = parent / name
    completed = run(
        ["git", "worktree", "add", "--detach", str(worktree), ref],
        cwd=root,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"failed to create worktree for {ref}")
    return worktree


def add_rebased_branch_worktree(
    root: Path,
    branch: str,
    upstream_ref: str,
    parent: Path,
) -> tuple[Path, dict[str, str]]:
    merge_base = git(root, "merge-base", branch, upstream_ref)
    worktree = add_worktree(root, branch, parent, "patch-removed")
    completed = run(
        ["git", "rebase", "--onto", upstream_ref, merge_base, "HEAD"],
        cwd=worktree,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        conflicts = git(worktree, "diff", "--name-only", "--diff-filter=U", check=False)
        run(["git", "rebase", "--abort"], cwd=worktree, check=False)
        raise RuntimeError(
            "forward-port conflict before retirement rehearsal: "
            + ", ".join(line for line in conflicts.splitlines() if line)
        )
    return worktree, {
        "merge_base": merge_base,
        "rebased_head": git(worktree, "rev-parse", "HEAD"),
    }


def restore_upstream_paths(
    root: Path,
    worktree: Path,
    upstream_ref: str,
    paths: list[str],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for relative in paths:
        target = worktree / relative
        exists_upstream = (
            run(
                ["git", "cat-file", "-e", f"{upstream_ref}:{relative}"],
                cwd=root,
                check=False,
            ).returncode
            == 0
        )
        if exists_upstream:
            completed = run(
                ["git", "restore", "--source", upstream_ref, "--worktree", "--", relative],
                cwd=worktree,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    completed.stderr.strip() or f"failed to restore {relative} from upstream"
                )
            action = "restored_from_upstream"
        elif target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
            action = "removed_local_only_path"
        else:
            action = "absent"
        actions.append({"path": relative, "action": action})
    return actions


def remove_worktree(root: Path, worktree: Path | None) -> None:
    if worktree is None:
        return
    run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=root,
        timeout=60,
        check=False,
    )


def rehearse_group(
    root: Path,
    branch: str,
    upstream_ref: str,
    group: dict[str, Any],
) -> dict[str, Any]:
    group_id = group["id"]
    paths = list(group["implementation_paths"])
    verifier = dict(group["verifier"])
    local_delta = [path for path in paths if path_differs(root, branch, upstream_ref, path)]
    current = run_verifier(root, root, verifier)
    base: dict[str, Any] = {
        "id": group_id,
        "description": group.get("description"),
        "implementation_paths": paths,
        "local_delta_paths": local_delta,
        "current_behavior": current,
        "mutations_performed_on_real_branch": False,
    }
    if current["status"] != "passed":
        return {
            **base,
            "outcome": "needs_human_review",
            "retirement_ready": False,
            "reason": "current branch does not satisfy its own behavioral verifier",
        }
    if not local_delta:
        return {
            **base,
            "outcome": "upstream_native",
            "retirement_ready": True,
            "upstream_behavior": {"status": "not_needed"},
            "patch_removed_behavior": {"status": "not_needed"},
        }

    target = root / "target/patch-retirement"
    target.mkdir(parents=True, exist_ok=True)
    temp_parent = Path(tempfile.mkdtemp(prefix=f"{group_id}-", dir=target))
    upstream_worktree: Path | None = None
    removed_worktree: Path | None = None
    try:
        upstream_worktree = add_worktree(root, upstream_ref, temp_parent, "upstream")
        upstream_behavior = run_verifier(root, upstream_worktree, verifier)
        if upstream_behavior["status"] != "passed":
            return {
                **base,
                "outcome": "required_local",
                "retirement_ready": False,
                "upstream_behavior": upstream_behavior,
                "patch_removed_behavior": {"status": "not_run"},
                "reason": "pure upstream does not satisfy the behavioral verifier",
            }

        removed_worktree, rebase = add_rebased_branch_worktree(
            root, branch, upstream_ref, temp_parent
        )
        restored = restore_upstream_paths(root, removed_worktree, upstream_ref, paths)
        patch_removed_behavior = run_verifier(root, removed_worktree, verifier)
        ready = patch_removed_behavior["status"] == "passed"
        return {
            **base,
            "outcome": "retirable" if ready else "needs_human_review",
            "retirement_ready": ready,
            "upstream_behavior": upstream_behavior,
            "patch_removed_behavior": patch_removed_behavior,
            "rebase": rebase,
            "restored": restored,
            "reason": (
                "upstream and patch-removed rehearsal both satisfy behavior"
                if ready
                else "upstream satisfies behavior but the patch-removed maintenance branch does not"
            ),
        }
    finally:
        remove_worktree(root, removed_worktree)
        remove_worktree(root, upstream_worktree)
        shutil.rmtree(temp_parent, ignore_errors=True)
        run(["git", "worktree", "prune"], cwd=root, check=False)


def check(
    root: Path,
    contract_path: Path | None,
    branch: str,
    upstream_ref: str,
    selected: set[str] | None,
) -> dict[str, Any]:
    clean_real_worktree_required(root)
    contract = load_contract(root, contract_path)
    groups = group_map(contract)
    unknown = sorted((selected or set()) - set(groups))
    if unknown:
        raise RuntimeError("unknown retirement group(s): " + ", ".join(unknown))
    chosen = [
        group for group_id, group in groups.items() if not selected or group_id in selected
    ]
    results = [rehearse_group(root, branch, upstream_ref, group) for group in chosen]
    ready = [item["id"] for item in results if item["retirement_ready"]]
    blocked = [item["id"] for item in results if not item["retirement_ready"]]
    return {
        "status": (
            "passed"
            if not any(item["outcome"] == "needs_human_review" for item in results)
            else "needs_human_review"
        ),
        "branch": branch,
        "branch_sha": git(root, "rev-parse", branch),
        "upstream_ref": upstream_ref,
        "upstream_sha": git(root, "rev-parse", upstream_ref),
        "results": results,
        "retirement_ready_groups": ready,
        "retirement_blocked_groups": blocked,
        "mutations_performed_on_real_branch": False,
        "persistent_mutations_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--branch", default="vue-lsp-native-main")
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = check(
            args.root.resolve(),
            args.contract,
            args.branch,
            args.upstream_ref,
            set(args.group) or None,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        result = {
            "status": "failed",
            "error": str(exc),
            "mutations_performed_on_real_branch": False,
            "persistent_mutations_performed": False,
        }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status={result['status']}")
        for item in result.get("results", []):
            print(f"{item['id']}: {item['outcome']}")
    return 0 if result["status"] in {"passed", "needs_human_review"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
