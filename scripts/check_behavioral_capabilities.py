#!/usr/bin/env python3
"""Behavioral conformance probes for the maintained Native Context capability set.

These probes are deliberately black-box or executable tests. They do not inspect
patch names or require a particular implementation shape, so an upstream
implementation can satisfy them and make a local patch retirement candidate.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def cargo_binary() -> str:
    explicit = os.environ.get("CARGO")
    if explicit:
        return explicit
    found = shutil.which("cargo")
    if found:
        return found
    home = Path.home() / ".cargo" / "bin" / "cargo"
    if home.is_file():
        return str(home)
    raise RuntimeError("cargo not found")


def run_probe(root: Path, name: str, argv: list[str], timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return {
            "name": name,
            "status": "passed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "failed",
            "reason": "timeout",
            "exit_code": None,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }


def check(root: Path, scope: str, *, fail_fast: bool = False) -> dict:
    cargo = cargo_binary()
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found")
    probes = [
        (
            "native_context_bridge_black_box",
            [node, "tooling/tools/codex-context-bridge/self-check.mjs"],
            60,
        ),
        (
            "native_context_runtime_contract",
            [cargo, "test", "-p", "webcodex", "native_context", "--lib"],
            240,
        ),
        (
            "scoped_project_instructions",
            [
                cargo,
                "test",
                "-p",
                "webcodex-core",
                "fair_share_keeps_nested_scoped_rules_when_root_is_large",
            ],
            180,
        ),
        (
            "workflow_native_context_continuity",
            [
                cargo,
                "test",
                "-p",
                "webcodex-workflow-session",
                "session_store_persists_and_restores_basic_session",
            ],
            180,
        ),
    ]
    if scope == "quick":
        probes = probes[:1]
    results = []
    for probe in probes:
        result = run_probe(root, *probe)
        results.append(result)
        if fail_fast and result["status"] != "passed":
            break
    failures = [item["name"] for item in results if item["status"] != "passed"]
    return {
        "status": "passed" if not failures else "failed",
        "scope": scope,
        "fail_fast": fail_fast,
        "probes": results,
        "failed_probes": failures,
        "mutations_performed": False,
        "native_model_turns_started": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("quick", "full"), default="full")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failed required probe",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check(args.root.resolve(), args.scope, fail_fast=args.fail_fast)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for probe in result["probes"]:
            print(f"[{probe['status'].upper():6}] {probe['name']}")
        print(f"\nstatus={result['status']}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
