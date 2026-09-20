#!/usr/bin/env python3
"""Focused regression tests for the local Desktop lifecycle safety contract."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("local_desktop_lifecycle.py")
SPEC = importlib.util.spec_from_file_location("local_desktop_lifecycle", SCRIPT)
assert SPEC and SPEC.loader
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)


def identity(commit: str, built_at: int = 1) -> dict:
    return {
        "app": "/tmp/WebCodex Desktop.app",
        "codesign": "verified",
        "commit": commit,
        "version": "0.4.1",
        "built_at": built_at,
        "runtimes": {},
    }


class LifecycleTests(unittest.TestCase):
    def test_mutation_history_is_append_only_deduplicated_and_receipt_compatible(self) -> None:
        payload = {
            "installed_at": 123,
            "operation": "install",
            "installed": identity("new"),
            "previous": identity("old"),
            "backup": "/tmp/old.app",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt, history = lifecycle.record_mutation(root, payload)
            second_receipt, second_history = lifecycle.record_mutation(root, payload)
            self.assertEqual(json.loads(receipt.read_text()), payload)
            self.assertEqual(second_receipt, receipt)
            self.assertEqual(second_history, history)
            lines = history.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["schema"], "webcodex-local-desktop-history.v1")
        self.assertFalse(record["reconstructed"])
        self.assertEqual(record["payload"], payload)

    def test_lifecycle_lock_rejects_overlapping_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with lifecycle.lifecycle_lock(root, timeout=0.05):
                with self.assertRaisesRegex(RuntimeError, "another Desktop lifecycle mutation"):
                    with lifecycle.lifecycle_lock(root, timeout=0.05):
                        self.fail("overlapping lifecycle lock unexpectedly succeeded")

    def test_install_same_identity_is_noop_even_while_running(self) -> None:
        current = identity("abc123")
        args = argparse.Namespace(
            confirm="INSTALL",
            candidate=Path("/candidate.app"),
            app=Path("/installed.app"),
            state_root=Path("/state"),
            backup_dir=Path("/backups"),
        )
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            lifecycle, "verify_app", side_effect=[current, current]
        ), mock.patch.object(lifecycle, "desktop_running", return_value=True), mock.patch.object(
            lifecycle, "perform_install"
        ) as perform:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(lifecycle.command_install(args), 0)
        perform.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["result"], "already_installed")
        self.assertFalse(payload["mutations_performed"])

    def test_adopt_same_identity_does_not_quit_or_relaunch(self) -> None:
        current = identity("abc123")
        args = argparse.Namespace(
            confirm="ADOPT",
            candidate=Path("/candidate.app"),
            app=Path("/installed.app"),
            state_root=Path("/state"),
            backup_dir=Path("/backups"),
            quit_timeout=40.0,
            no_relaunch=False,
        )
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            lifecycle, "verify_app", side_effect=[current, current]
        ), mock.patch.object(lifecycle, "desktop_running", return_value=True), mock.patch.object(
            lifecycle, "run"
        ) as run_call, mock.patch.object(lifecycle, "perform_install") as perform:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(lifecycle.command_adopt(args), 0)
        run_call.assert_not_called()
        perform.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["result"], "already_installed")

    def test_prune_plan_only_targets_current_identity_standard_backups(self) -> None:
        current = identity("current")
        older = identity("older")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            newest = root / "WebCodex-Desktop-backup-0.4.1-current-20260920-104000.app"
            duplicate = root / "WebCodex-Desktop-backup-0.4.1-current-20260920-103900.app"
            old = root / "WebCodex-Desktop-backup-0.4.1-older-20260920-100000.app"
            official = root / "WebCodex Desktop Official 0.4.1-current.app"
            for path in (newest, duplicate, old, official):
                path.mkdir()
            mapping = {newest: current, duplicate: current, old: older}
            args = argparse.Namespace(backup_dir=root, keep_current=1)
            with mock.patch.object(lifecycle, "verify_app", side_effect=lambda path: mapping[path]):
                plan = lifecycle.build_prune_plan(args, current)
        self.assertEqual([item["app"] for item in plan["kept"]], [str(newest)])
        self.assertEqual([item["app"] for item in plan["remove"]], [str(duplicate)])
        self.assertEqual([item["app"] for item in plan["protected_other_identity"]], [str(old)])


if __name__ == "__main__":
    unittest.main()
