#!/usr/bin/env python3
"""Focused tests for zero-quota self-maintaining fork orchestration."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "self_maintenance.py"
SPEC = importlib.util.spec_from_file_location("self_maintenance", SCRIPT)
assert SPEC and SPEC.loader
self_maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_maintenance)

ROOT = Path(__file__).parents[2]
POLICY = json.loads((ROOT / self_maintenance.POLICY).read_text())


class SelfMaintenanceTests(unittest.TestCase):
    def test_subprocess_environment_includes_standard_rust_bin(self) -> None:
        with mock.patch.object(
            self_maintenance.subprocess, "run"
        ) as subprocess_run:
            subprocess_run.return_value = mock.Mock(
                stdout="", stderr="", returncode=0
            )
            self_maintenance.run(["cargo", "--version"], cwd=ROOT)
        env = subprocess_run.call_args.kwargs["env"]
        self.assertIn(str(Path.home() / ".cargo/bin"), env["PATH"].split(":"))

    def test_policy_keeps_codex_reference_only_and_zero_quota(self) -> None:
        policy = self_maintenance.load_policy(ROOT)
        invariants = policy["invariants"]
        self.assertEqual(invariants["codex_role"], "reference_only")
        self.assertEqual(invariants["native_codex_model_quota_budget"], 0)
        self.assertFalse(invariants["codex_model_fallback_allowed"])
        self.assertFalse(invariants["real_branch_mutation_during_rehearsal"])

    def test_unit_categories_distinguish_upstream_retirement_and_required_local(self) -> None:
        unit = {
            "id": "example",
            "capability_id": "example_capability",
            "upstream_satisfaction": {"alternatives": []},
            "local_implementation_paths": ["src/example.rs"],
        }
        policy = {"local_units": [unit]}
        cases = [
            ((True, True), "upstream_can_replace_local"),
            ((True, False), "upstream_native"),
            ((False, True), "required_local"),
            ((False, False), "needs_human_review"),
        ]
        for (upstream, local), expected in cases:
            with self.subTest(expected=expected), mock.patch.object(
                self_maintenance,
                "probe_ref",
                return_value=(upstream, {"evidence": []}),
            ), mock.patch.object(
                self_maintenance,
                "local_implementation_present",
                return_value=(local, {"strategy": "test"}),
            ):
                result = self_maintenance.assess_units(
                    ROOT, policy, "branch", "upstream"
                )[0]
                self.assertEqual(result["category"], expected)

    def test_retirement_report_never_mutates_real_branch(self) -> None:
        unit = POLICY["local_units"][0]
        with mock.patch.object(
            self_maintenance, "clean_real_worktree_required"
        ), mock.patch.object(
            self_maintenance,
            "rehearse_retirement_unit",
            return_value={
                "id": unit["id"],
                "outcome": "required_local",
                "mutations_performed_on_real_branch": False,
            },
        ):
            result = self_maintenance.retirement_report(
                ROOT,
                {"local_units": [unit]},
                "vue-lsp-native-main",
                "upstream/main",
            )
        self.assertFalse(result["mutations_performed_on_real_branch"])
        self.assertFalse(result["persistent_mutations_performed"])
        self.assertEqual(result["results"][0]["outcome"], "required_local")

    def test_autopilot_default_is_rehearsal_only(self) -> None:
        inventory = {"parity_gaps": []}
        compatibility = {"status": "passed"}
        retirement = {"status": "passed", "results": []}
        with mock.patch.object(
            self_maintenance, "clean_real_worktree_required"
        ), mock.patch.object(
            self_maintenance, "build_inventory", return_value=inventory
        ), mock.patch.object(
            self_maintenance, "upstream_compatibility", return_value=compatibility
        ) as upstream, mock.patch.object(
            self_maintenance, "retirement_report", return_value=retirement
        ), mock.patch.object(
            self_maintenance,
            "candidate_readiness",
            return_value={"status": "not_provided", "safe_to_adopt": False},
        ):
            result = self_maintenance.autopilot_report(
                ROOT,
                POLICY,
                "vue-lsp-native-main",
                "upstream/main",
                None,
                None,
                None,
            )
        self.assertEqual(result["decision"], "safe_to_upgrade")
        self.assertFalse(result["desktop_mutations_performed"])
        self.assertFalse(result["persistent_mutations_performed"])
        self.assertFalse(result["adoption"]["automatic"])
        self.assertFalse(upstream.call_args.kwargs["fetch"])

    def test_semantic_diff_detects_reference_surface_change(self) -> None:
        current = {
            "reference_surface": {"bridge_version": "0.5.0"},
            "local_units": [{"id": "a", "category": "upstream_native"}],
        }
        baseline = {
            "reference_surface": {"bridge_version": "0.4.0"},
            "local_units": [{"id": "a", "category": "required_local"}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            path.write_text(json.dumps(baseline))
            diff = self_maintenance.semantic_diff(current, path)
        self.assertEqual(diff["status"], "changed")
        self.assertIn("bridge_version", diff["reference_surface_changes"])
        self.assertIn("a", diff["local_unit_changes"])

    def test_upgrade_decision_blocks_conflicted_forward_port(self) -> None:
        decision, reasons = self_maintenance.decide_upgrade(
            {"status": "conflict"},
            {"results": []},
            {"parity_gaps": []},
        )
        self.assertEqual(decision, "blocked")
        self.assertTrue(reasons)

    def test_provider_retirement_requires_executed_tests_not_compile_only(self) -> None:
        commands = self_maintenance.validation_commands("provider_environment_regression")
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command[:2], ["cargo", "test"])
        flattened = " ".join(" ".join(command) for command in commands)
        self.assertIn(
            "mcp_gateway_execution_context_accepts_explicit_cwd_and_env_mapping",
            flattened,
        )
        self.assertIn(
            "provider_execution_context_is_explicit_cleared_and_private",
            flattened,
        )


if __name__ == "__main__":
    unittest.main()
