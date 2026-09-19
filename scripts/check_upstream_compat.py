#!/usr/bin/env python3
"""Disposable forward-port rehearsal against a newer upstream ref."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 30,
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


def has_native_vue(root: Path, ref: str) -> bool:
    patterns = (
        "VueLanguageServer",
        "vue-language-server",
        '("vue", "vue")',
    )
    for pattern in patterns:
        result = run(
            ["git", "grep", "-n", "-F", pattern, ref, "--", "crates", "docs"],
            cwd=root,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def fetch_upstream(root: Path, remote: str, branch: str) -> None:
    run(["git", "fetch", remote, branch, "--tags"], cwd=root, timeout=120)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--branch", default="vue-lsp-native-main")
    parser.add_argument("--upstream-ref", default="upstream/main")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--fetch-remote", default="upstream")
    parser.add_argument("--fetch-branch", default="main")
    parser.add_argument("--run-checks", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SystemExit("source worktree must be clean before compatibility rehearsal")

    if args.fetch:
        fetch_upstream(root, args.fetch_remote, args.fetch_branch)

    branch_sha = git(root, "rev-parse", args.branch)
    upstream_sha = git(root, "rev-parse", args.upstream_ref)
    merge_base = git(root, "merge-base", args.branch, args.upstream_ref)
    native_vue = has_native_vue(root, args.upstream_ref)

    result = {
        "status": "pending",
        "branch": args.branch,
        "branch_sha": branch_sha,
        "upstream_ref": args.upstream_ref,
        "upstream_sha": upstream_sha,
        "merge_base": merge_base,
        "upstream_native_vue": native_vue,
        "rebase": None,
        "checks": None,
        "mutations_performed_on_real_branch": False,
    }

    target_root = root / "target"
    target_root.mkdir(exist_ok=True)
    temp_parent = Path(tempfile.mkdtemp(prefix="upstream-compat-", dir=target_root))
    worktree = temp_parent / "worktree"
    worktree_registered = False

    try:
        add = run(
            ["git", "worktree", "add", "--detach", str(worktree), args.branch],
            cwd=root,
            timeout=60,
            check=False,
        )
        if add.returncode != 0:
            result["status"] = "failed"
            result["rebase"] = {
                "status": "worktree_add_failed",
                "detail": (add.stderr or add.stdout).strip()[-3000:],
            }
            return emit(result, args.json, 1)
        worktree_registered = True

        rebase = run(
            ["git", "rebase", "--onto", args.upstream_ref, merge_base, "HEAD"],
            cwd=worktree,
            timeout=120,
            check=False,
        )
        if rebase.returncode != 0:
            conflicts = git(worktree, "diff", "--name-only", "--diff-filter=U", check=False)
            result["status"] = "conflict"
            result["rebase"] = {
                "status": "conflict",
                "conflicted_files": [line for line in conflicts.splitlines() if line],
                "detail": (rebase.stderr or rebase.stdout).strip()[-3000:],
            }
            run(["git", "rebase", "--abort"], cwd=worktree, check=False)
            return emit(result, args.json, 2)

        rebased_head = git(worktree, "rev-parse", "HEAD")
        result["rebase"] = {"status": "passed", "rebased_head": rebased_head}

        if args.run_checks:
            vue_root = (
                Path.home()
                / "Library/Application Support/dev.webcodex.desktop/local-tools/vue-lsp-2.2.12"
            )
            env = {"CARGO_NET_OFFLINE": "true"}
            server = vue_root / "bin/vue-language-server"
            tsdk = vue_root / "node_modules/typescript/lib"
            if server.is_file() and tsdk.is_dir():
                env.update(
                    {
                        "WEBCODEX_RUN_REAL_VUE_LSP": "1",
                        "WEBCODEX_VUE_LANGUAGE_SERVER": str(server),
                        "WEBCODEX_VUE_TSDK": str(tsdk),
                    }
                )
            check = run(
                ["bash", "scripts/check_vue_lsp_patch.sh"],
                cwd=worktree,
                timeout=420,
                env=env,
                check=False,
            )
            result["checks"] = {
                "status": "passed" if check.returncode == 0 else "failed",
                "stdout_tail": check.stdout[-3000:],
                "stderr_tail": check.stderr[-3000:],
            }
            if check.returncode != 0:
                result["status"] = "failed"
                return emit(result, args.json, 1)
        else:
            result["checks"] = {"status": "skipped"}

        result["status"] = "passed"
        return emit(result, args.json, 0)
    finally:
        if worktree_registered:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, timeout=60, check=False)
        shutil.rmtree(temp_parent, ignore_errors=True)
        run(["git", "worktree", "prune"], cwd=root, check=False)


def emit(result: dict, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status={result['status']}")
        print(f"branch={result['branch']} @ {result['branch_sha']}")
        print(f"upstream={result['upstream_ref']} @ {result['upstream_sha']}")
        print(f"merge_base={result['merge_base']}")
        print(f"upstream_native_vue={result['upstream_native_vue']}")
        print(f"rebase={result['rebase']}")
        print(f"checks={result['checks'] and result['checks'].get('status')}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
