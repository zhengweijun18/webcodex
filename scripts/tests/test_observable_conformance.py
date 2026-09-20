#!/usr/bin/env python3
"""Focused tests for the observable Codex runtime conformance checker."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "check_observable_conformance.py"
SPEC = importlib.util.spec_from_file_location("check_observable_conformance", SCRIPT)
assert SPEC and SPEC.loader
conformance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(conformance)

ROOT = Path(__file__).parents[2]
CONTRACT = json.loads((ROOT / conformance.CONTRACT).read_text())


class ObservableConformanceTests(unittest.TestCase):
    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(conformance.validate_contract(CONTRACT), [])

    def test_contract_rejects_nonzero_quota_or_model_fallback(self) -> None:
        contract = copy.deepcopy(CONTRACT)
        contract["invariants"]["native_codex_model_quota_budget"] = 1
        contract["invariants"]["codex_model_fallback_allowed"] = True
        failures = conformance.validate_contract(contract)
        self.assertTrue(any("native_codex_model_quota_budget" in item for item in failures))
        self.assertTrue(any("codex_model_fallback_allowed" in item for item in failures))

    def test_private_and_model_surfaces_must_be_explicitly_out_of_scope(self) -> None:
        contract = copy.deepcopy(CONTRACT)
        contract["explicitly_out_of_scope"] = []
        failures = conformance.validate_contract(contract)
        self.assertTrue(any("exclusions must be explicit" in item for item in failures))

    def test_missing_static_evidence_fails_scenario(self) -> None:
        scenario = copy.deepcopy(CONTRACT["in_scope_scenarios"][0])
        scenario["evidence"] = [{"path": "missing-file", "all_tokens": ["x"]}]
        passed, evidence = conformance.static_evidence(ROOT, scenario)
        self.assertFalse(passed)
        self.assertTrue(evidence[0]["missing"])

    def test_static_only_never_claims_defined_scope_100_percent(self) -> None:
        with mock.patch.object(
            conformance,
            "external_zero_quota",
            return_value={
                "status": "passed",
                "native_codex_model_quota_budget": 0,
                "observed_native_codex_model_quota_consumed": 0,
                "codex_model_fallback_allowed": False,
                "failed_checks": [],
            },
        ):
            report = conformance.evaluate(
                ROOT,
                CONTRACT,
                run_tests=False,
                context_workspace=Path("/tmp/context"),
            )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["measurement"]["static_or_requested_evidence_percent"], 100.0)
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])
        self.assertFalse(report["measurement"]["full_conformance_evidence_ready"])

    def test_static_without_context_workspace_does_not_require_external_evidence(self) -> None:
        report = conformance.evaluate(
            ROOT,
            CONTRACT,
            run_tests=False,
            context_workspace=None,
        )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["measurement"]["static_or_requested_evidence_percent"], 100.0)
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])
        self.assertEqual(report["external_zero_quota_evidence"]["status"], "not_requested")

    def test_zero_quota_failure_prevents_full_conformance(self) -> None:
        with mock.patch.object(
            conformance,
            "external_zero_quota",
            return_value={
                "status": "failed",
                "native_codex_model_quota_budget": 0,
                "observed_native_codex_model_quota_consumed": None,
                "codex_model_fallback_allowed": False,
                "failed_checks": ["quota drift"],
            },
        ), mock.patch.object(
            conformance,
            "run_dynamic_test",
            return_value={
                "status": "passed",
                "test_filter": "fixture",
                "exit_code": 0,
                "tests_passed": 1,
            },
        ):
            report = conformance.evaluate(
                ROOT,
                CONTRACT,
                run_tests=True,
                context_workspace=Path("/tmp/context"),
            )
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])

    def test_dynamic_zero_tests_is_not_a_pass(self) -> None:
        result = {
            "status": "failed",
            "test_filter": "fixture",
            "exit_code": 0,
            "tests_passed": 0,
        }
        with mock.patch.object(
            conformance,
            "run_dynamic_test",
            return_value=result,
        ), mock.patch.object(
            conformance,
            "external_zero_quota",
            return_value={
                "status": "passed",
                "native_codex_model_quota_budget": 0,
                "observed_native_codex_model_quota_consumed": 0,
                "codex_model_fallback_allowed": False,
                "failed_checks": [],
            },
        ):
            report = conformance.evaluate(
                ROOT,
                CONTRACT,
                run_tests=True,
                context_workspace=Path("/tmp/context"),
            )
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])

    def test_selected_subset_can_never_claim_full_conformance(self) -> None:
        scenario_id = CONTRACT["in_scope_scenarios"][0]["id"]
        with mock.patch.object(
            conformance,
            "run_dynamic_test",
            return_value={
                "status": "passed",
                "test_filter": "fixture",
                "exit_code": 0,
                "tests_passed": 1,
                "duration_secs": 0.01,
            },
        ):
            report = conformance.evaluate(
                ROOT,
                CONTRACT,
                run_tests=True,
                context_workspace=Path("/tmp/context"),
                selected_scenarios={scenario_id},
            )
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["measurement"]["selection_complete"])
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])

    def test_dynamic_timeout_is_reported_as_timeout(self) -> None:
        scenario_id = CONTRACT["in_scope_scenarios"][0]["id"]
        with mock.patch.object(
            conformance,
            "run_dynamic_test",
            return_value={
                "status": "timeout",
                "test_filter": "fixture",
                "reason": "timeout",
                "duration_secs": 1.0,
            },
        ):
            report = conformance.evaluate(
                ROOT,
                CONTRACT,
                run_tests=True,
                context_workspace=None,
                selected_scenarios={scenario_id},
            )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["scenarios"][0]["status"], "timeout")
        self.assertIsNone(report["measurement"]["defined_scope_conformance_percent"])

    def test_bounded_process_timeout_terminates_process_group(self) -> None:
        result = conformance.run_bounded_process(
            [
                sys.executable,
                "-c",
                "import subprocess,time; subprocess.Popen(['sleep','30']); time.sleep(30)",
            ],
            cwd=ROOT,
            env=None,
            timeout_secs=1,
        )
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["process_group_terminated"])


if __name__ == "__main__":
    unittest.main()
