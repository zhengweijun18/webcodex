#!/usr/bin/env python3
"""Focused regressions for zero-Codex-quota native semantic parity policy."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "check_native_semantic_parity.py"
SPEC = importlib.util.spec_from_file_location("check_native_semantic_parity", SCRIPT)
assert SPEC and SPEC.loader
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)

ROOT = Path(__file__).parents[2]
POLICY = json.loads((ROOT / parity.POLICY).read_text())
REFERENCE = json.loads((ROOT / parity.REFERENCE).read_text())


def write_external_fixture(root: Path, *, model_turns_allowed: bool = False) -> None:
    state = {
        "schema": "webcodex-codex-target-mode.v3",
        "verified_at": "2026-09-20T00:00:00+08:00",
        "goal": "maximum_native_parity_with_zero_codex_model_inference_usage",
        "native_runtime": {
            "codex_model_turns_allowed": model_turns_allowed,
            "acp_coding_agent_enabled": False,
        },
        "native_mcp_readonly_proxy": {
            "quota_mode": "zero_codex_model_turn",
            "model_turn_started": False,
        },
        "native_mcp_effectful_proxy": {
            "quota_mode": "zero_codex_model_turn",
            "model_turn_started": False,
            "generic_destructive_passthrough": False,
        },
        "quota_proof": {
            "codex_model_turn_started": False,
            "rate_limits_before_after_equal": True,
            "usage_before_after_equal": True,
        },
    }
    state_path = root / parity.STATE_RELATIVE
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state))
    bridge = root / parity.BRIDGE_RELATIVE
    bridge.parent.mkdir(parents=True)
    bridge.write_text(
        """
        async function probe() {
          await codexRpc("config/read", {});
          await request("thread/start", { cwd: root, ephemeral: true });
          await request("mcpServerStatus/list", {});
          await request("mcpServer/tool/call", {});
        }
        """
    )


class NativeSemanticParityTests(unittest.TestCase):
    def test_repository_policy_and_reference_are_valid(self) -> None:
        self.assertEqual(parity.validate_policy(POLICY), [])
        self.assertEqual(parity.validate_reference(POLICY, REFERENCE), [])

    def test_policy_rejects_codex_model_route(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["capabilities"][0]["route_order"] = ["codex_model", "unavailable"]
        failures = parity.validate_policy(policy)
        self.assertTrue(any("forbidden route" in item for item in failures))

    def test_external_state_rejects_codex_model_turns_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_external_fixture(workspace, model_turns_allowed=True)
            ok, _, failures = parity.validate_external_state(POLICY, workspace)
        self.assertFalse(ok)
        self.assertIn(
            "native_runtime.codex_model_turns_allowed must be false",
            failures,
        )

    def test_bridge_rejects_non_allowlisted_turn_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_external_fixture(workspace)
            bridge = workspace / parity.BRIDGE_RELATIVE
            bridge.write_text(bridge.read_text() + '\nawait request("turn/start", {});\n')
            ok, data, failures = parity.audit_bridge(POLICY, workspace)
        self.assertFalse(ok)
        self.assertIn("turn/start", data["methods"])
        self.assertTrue(any("non-allowlisted" in item for item in failures))

    def test_valid_external_fixture_proves_zero_quota_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_external_fixture(workspace)
            ok, data, failures = parity.validate_external_state(POLICY, workspace)
        self.assertTrue(ok, failures)
        self.assertEqual(data["native_runtime"]["codex_model_turns_allowed"], False)
        self.assertEqual(data["quota_proof"]["usage_before_after_equal"], True)


if __name__ == "__main__":
    unittest.main()
