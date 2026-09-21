#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


doctor = load_module("fork_doctor", "scripts/fork_doctor.py")


class ForkDoctorEvolutionTests(unittest.TestCase):
    def test_deployment_gap_accepts_runtime_short_sha_for_same_head(self) -> None:
        source = "a" * 40
        installed = "a" * 12
        with mock.patch.object(doctor.platform, "system", return_value="Darwin"), \
             mock.patch.object(doctor.Path, "is_dir", return_value=True), \
             mock.patch.object(
                 doctor,
                 "installed_runtime",
                 return_value={"commit": installed, "version": "0.4.1"},
             ), \
             mock.patch.object(doctor, "git", return_value=source), \
             mock.patch.object(doctor, "resolve_commit", return_value=source):
            status, detail, data = doctor.check_deployment_gap(
                ROOT, Path("/Applications/WebCodex Desktop.app")
            )
        self.assertEqual(status, "passed")
        self.assertEqual(data["deployment_gap_state"], "closed")
        self.assertFalse(data["deployment_gap"])

    def test_deployment_gap_marks_unresolvable_runtime_unverifiable(self) -> None:
        with mock.patch.object(doctor.platform, "system", return_value="Darwin"), \
             mock.patch.object(doctor.Path, "is_dir", return_value=True), \
             mock.patch.object(
                 doctor,
                 "installed_runtime",
                 return_value={"commit": "deadbeef", "version": "0.4.1"},
             ), \
             mock.patch.object(doctor, "git", return_value="b" * 40), \
             mock.patch.object(doctor, "resolve_commit", return_value=None):
            status, _, data = doctor.check_deployment_gap(
                ROOT, Path("/Applications/WebCodex Desktop.app")
            )
        self.assertEqual(status, "warning")
        self.assertEqual(data["deployment_gap_state"], "unverifiable")

    def test_static_retirement_signal_never_claims_behavioral_readiness(self) -> None:
        with mock.patch.object(doctor, "git", return_value="src/runtime.rs\n"):
            status, _, data = doctor.check_patch_retirement(
                ROOT, "maintenance", "upstream/main"
            )
        self.assertEqual(status, "passed")
        self.assertIsNone(data["retirement_ready"])
        self.assertTrue(data["behavioral_gate_required"])
        self.assertEqual(data["evidence_strength"], "static_diff_only")

    def test_behavioral_retirement_is_opt_in(self) -> None:
        status, detail, _ = doctor.check_behavioral_patch_retirement(
            ROOT, "maintenance", "upstream/main", False
        )
        self.assertEqual(status, "skipped")
        self.assertIn("not requested", detail)

    def test_behavioral_retirement_reports_checker_outcomes_without_mutation(self) -> None:
        payload = {
            "status": "passed",
            "retirement_ready_groups": ["a"],
            "retirement_blocked_groups": ["b"],
            "mutations_performed_on_real_branch": False,
        }
        completed = subprocess.CompletedProcess(
            args=["checker"], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        with mock.patch.object(doctor.Path, "is_file", return_value=True), \
             mock.patch.object(doctor, "run", return_value=completed):
            status, detail, data = doctor.check_behavioral_patch_retirement(
                ROOT, "maintenance", "upstream/main", True
            )
        self.assertEqual(status, "passed")
        self.assertIn("1 ready, 1 retained", detail)
        self.assertFalse(data["mutations_performed_on_real_branch"])

    def test_installed_enhanced_runtime_accepts_bundled_node_and_zero_quota_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app = Path(td) / "WebCodex Desktop.app"
            tools = app / "Contents/Resources/webcodex-tools"
            node = tools / "node/node"
            bridge = tools / "codex-context-bridge"
            node.parent.mkdir(parents=True)
            bridge.mkdir(parents=True)
            node.write_text("node")
            for name in ("bridge-lib.mjs", "server.mjs", "self-check.mjs"):
                (bridge / name).write_text("fixture")
            (bridge / "package.json").write_text(json.dumps({"version": "0.5.0"}))
            completed = [
                subprocess.CompletedProcess(
                    args=[str(node), "--version"],
                    returncode=0,
                    stdout="v24.21.0\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[str(node), str(bridge / "self-check.mjs")],
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "status": "pass",
                            "bridge_version": "0.5.0",
                            "native_model_turns": 0,
                        }
                    ),
                    stderr="",
                ),
            ]
            with mock.patch.object(doctor.platform, "system", return_value="Darwin"), \
                 mock.patch.object(doctor, "run", side_effect=completed):
                status, detail, data = doctor.check_installed_enhanced_runtime(ROOT, app)
        self.assertEqual(status, "passed")
        self.assertIn("bundled Node + Context Bridge", detail)
        self.assertEqual(data["bundled_node_version"], "v24.21.0")
        self.assertEqual(data["native_model_turns"], 0)

    def test_installed_enhanced_runtime_warns_when_bundle_resources_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            app = Path(td) / "WebCodex Desktop.app"
            app.mkdir()
            status, detail, data = doctor.check_installed_enhanced_runtime(ROOT, app)
        self.assertEqual(status, "warning")
        self.assertIn("missing single-install", detail)
        self.assertTrue(data["missing_resources"])


if __name__ == "__main__":
    unittest.main()
