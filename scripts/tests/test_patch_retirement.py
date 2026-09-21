#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_patch_retirement", ROOT / "scripts/check_patch_retirement.py"
)
assert SPEC and SPEC.loader
retirement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retirement)


class PatchRetirementTests(unittest.TestCase):
    def group(self) -> dict:
        return {
            "id": "demo",
            "description": "demo",
            "implementation_paths": ["src/demo.rs"],
            "verifier": {"kind": "behavioral_capabilities", "scope": "quick"},
        }

    def test_contract_rejects_unsafe_implementation_path(self) -> None:
        contract = {
            "retirement_groups": [
                {
                    **self.group(),
                    "implementation_paths": ["../outside"],
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "unsafe implementation path"):
            retirement.group_map(contract)

    def test_stable_python3_does_not_inherit_legacy_invoking_interpreter(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["python3"], returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(
            retirement.shutil, "which", return_value="/usr/local/bin/python3"
        ), mock.patch.object(
            retirement.sys, "executable", "/usr/bin/python3"
        ), mock.patch.object(
            retirement.subprocess, "run", return_value=completed
        ) as run:
            selected = retirement.stable_python3()
        self.assertEqual(selected, "/usr/local/bin/python3")
        self.assertEqual(run.call_args.args[0][0], "/usr/local/bin/python3")

    def test_no_local_delta_is_already_upstream_native_after_current_behavior_passes(self) -> None:
        with mock.patch.object(retirement, "path_differs", return_value=False), \
             mock.patch.object(
                 retirement,
                 "run_verifier",
                 return_value={"status": "passed", "exit_code": 0},
             ):
            result = retirement.rehearse_group(
                ROOT, "maintenance", "upstream/main", self.group()
            )
        self.assertEqual(result["outcome"], "upstream_native")
        self.assertTrue(result["retirement_ready"])
        self.assertFalse(result["mutations_performed_on_real_branch"])

    def test_upstream_behavior_failure_keeps_local_patch_required(self) -> None:
        with mock.patch.object(retirement, "path_differs", return_value=True), \
             mock.patch.object(
                 retirement,
                 "run_verifier",
                 side_effect=[
                     {"status": "passed", "exit_code": 0},
                     {"status": "failed", "exit_code": 1},
                 ],
             ), \
             mock.patch.object(retirement, "add_worktree", return_value=ROOT / "fake-upstream"), \
             mock.patch.object(retirement, "remove_worktree"), \
             mock.patch.object(retirement.tempfile, "mkdtemp", return_value=str(ROOT / "target/fake-retirement")), \
             mock.patch.object(retirement.shutil, "rmtree"), \
             mock.patch.object(retirement, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
            result = retirement.rehearse_group(
                ROOT, "maintenance", "upstream/main", self.group()
            )
        self.assertEqual(result["outcome"], "required_local")
        self.assertFalse(result["retirement_ready"])
        self.assertFalse(result["mutations_performed_on_real_branch"])


if __name__ == "__main__":
    unittest.main()
