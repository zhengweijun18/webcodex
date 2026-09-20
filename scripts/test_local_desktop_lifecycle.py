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
    def test_desktop_process_status_uses_process_table_suffixes(self) -> None:
        process_table = "\n".join(
            [
                "/Applications/WebCodex Desktop.app/Contents/MacOS/WebCodex",
                "/Applications/WebCodex Desktop.app/Contents/Resources/webcodex-runtime/webcodex-runner --config /tmp/runner.toml",
                "/Applications/WebCodex Desktop.app/Contents/Resources/webcodex-runtime/webcodex-server --stop-on-stdin-eof",
            ]
        )
        with mock.patch.object(
            lifecycle,
            "run",
            return_value=mock.Mock(returncode=0, stdout=process_table, stderr=""),
        ) as process_run:
            status = lifecycle.desktop_process_status()
        self.assertEqual(status, {"desktop": True, "runner": True, "server": True})
        process_run.assert_called_once_with(["ps", "-axo", "command="], check=False)

    def test_success_release_state_promotes_candidate_to_last_known_good(self) -> None:
        candidate = identity("new")
        previous = identity("old")
        transaction = {"operation": "adopt", "status": "healthy"}
        state = lifecycle.release_state_for_success(
            candidate,
            candidate,
            previous,
            "/backups/old.app",
            transaction,
        )
        self.assertEqual(state["installed"]["state"], "healthy")
        self.assertEqual(state["candidate"]["state"], "healthy")
        self.assertEqual(state["last_known_good"]["commit"], "new")
        self.assertEqual(state["rollback_target"]["identity"]["commit"], "old")
        self.assertIsNone(state["failed_candidate"])

    def test_failed_release_state_keeps_restored_last_known_good(self) -> None:
        candidate = identity("bad")
        restored = identity("good")
        state = lifecycle.release_state_for_failure(
            candidate,
            restored,
            "/backups/good.app",
            {"operation": "adopt", "status": "failed_auto_rolled_back"},
            prior_last_known_good=restored,
            restored_healthy=False,
        )
        self.assertEqual(state["installed"]["state"], "verified_restored")
        self.assertEqual(state["last_known_good"]["commit"], "good")
        self.assertEqual(state["failed_candidate"]["commit"], "bad")
        self.assertEqual(state["candidate"]["state"], "failed")

    def test_failed_release_without_prior_lkg_does_not_promote_unlaunched_restore(self) -> None:
        candidate = identity("bad")
        restored = identity("restored")
        state = lifecycle.release_state_for_failure(
            candidate,
            restored,
            "/backups/restored.app",
            {"operation": "adopt", "status": "failed_auto_rolled_back"},
            prior_last_known_good=None,
            restored_healthy=False,
        )
        self.assertEqual(state["installed"]["state"], "verified_restored")
        self.assertIsNone(state["last_known_good"])

    def test_auto_rollback_restores_previous_identity_and_records_failure(self) -> None:
        candidate = identity("bad")
        previous = identity("good")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup = root / "good.app"
            backup.mkdir()
            args = argparse.Namespace(
                app=root / "installed.app",
                state_root=root / "state",
                backup_dir=root / "backups",
                quit_timeout=1.0,
                health_timeout=1.0,
                no_relaunch=False,
            )
            with mock.patch.object(lifecycle, "desktop_running", return_value=False), mock.patch.object(
                lifecycle, "verify_app", side_effect=[previous, previous]
            ), mock.patch.object(lifecycle, "atomic_replace") as replace:
                result = lifecycle.auto_rollback_adoption(
                    args,
                    candidate,
                    previous,
                    str(backup),
                    False,
                    {"status": "failed", "reason": "desktop_health_timeout"},
                    previous,
                )
            replace.assert_called_once_with(backup, args.app)
            state = json.loads(
                lifecycle.release_state_path(args.state_root).read_text()
            )
        self.assertEqual(result["status"], "failed_auto_rolled_back")
        self.assertTrue(result["auto_rollback_performed"])
        self.assertEqual(state["last_known_good"]["commit"], "good")
        self.assertEqual(state["failed_candidate"]["commit"], "bad")

    def test_health_requires_runner_and_server_not_just_one_process(self) -> None:
        current = identity("healthy")
        with mock.patch.object(
            lifecycle, "verify_app", return_value=current
        ), mock.patch.object(
            lifecycle,
            "desktop_process_status",
            side_effect=[
                {"desktop": True, "runner": True, "server": False},
                {"desktop": True, "runner": True, "server": True},
            ],
        ), mock.patch.object(lifecycle.time, "sleep"):
            result = lifecycle.check_installed_health(
                Path("/installed.app"),
                current,
                1.0,
                require_running=True,
            )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["process_status"]["runner"])
        self.assertTrue(result["process_status"]["server"])

    def test_promote_current_requires_healthy_runtime_and_sets_lkg(self) -> None:
        current = identity("good")
        args = argparse.Namespace(
            confirm="PROMOTE",
            app=Path("/installed.app"),
            state_root=Path("/state"),
            backup_dir=Path("/backups"),
        )
        with mock.patch.object(
            lifecycle, "lifecycle_lock"
        ) as lock, mock.patch.object(
            lifecycle, "verify_app", return_value=current
        ), mock.patch.object(
            lifecycle,
            "desktop_process_status",
            return_value={"desktop": True, "runner": True, "server": True},
        ), mock.patch.object(
            lifecycle, "newest_other_identity_backup", return_value=None
        ), mock.patch.object(
            lifecycle, "write_release_state", return_value=Path("/state/release.json")
        ) as write_state:
            lock.return_value.__enter__.return_value = None
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(lifecycle.command_promote_current(args), 0)
        state = write_state.call_args.args[1]
        self.assertEqual(state["installed"]["state"], "healthy")
        self.assertEqual(state["last_known_good"]["commit"], "good")
        self.assertFalse(json.loads(output.getvalue())["desktop_mutations_performed"])

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
            health_timeout=30.0,
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

    def test_adopt_health_failure_uses_auto_rollback(self) -> None:
        candidate = identity("bad")
        previous = identity("good")
        install_result = {
            "status": "passed",
            "installed_at": 123,
            "operation": "adopt",
            "installed": candidate,
            "previous": previous,
            "backup": "/backups/good.app",
            "receipt": None,
            "history": None,
            "mutations_performed": True,
        }
        args = argparse.Namespace(
            confirm="ADOPT",
            candidate=Path("/candidate.app"),
            app=Path("/installed.app"),
            state_root=Path("/state"),
            backup_dir=Path("/backups"),
            quit_timeout=40.0,
            health_timeout=1.0,
            no_relaunch=False,
        )
        failure = {
            "status": "failed_auto_rolled_back",
            "operation": "adopt",
            "auto_rollback_performed": True,
        }
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            lifecycle, "verify_app", side_effect=[candidate, previous, candidate, previous]
        ), mock.patch.object(
            lifecycle, "lifecycle_lock"
        ) as lock, mock.patch.object(
            lifecycle,
            "desktop_process_status",
            return_value={"desktop": False, "runner": False, "server": False},
        ), mock.patch.object(
            lifecycle, "perform_install", return_value=install_result
        ), mock.patch.object(
            lifecycle, "read_release_state", return_value=None
        ), mock.patch.object(
            lifecycle, "write_release_state"
        ), mock.patch.object(
            lifecycle, "run"
        ), mock.patch.object(
            lifecycle,
            "check_installed_health",
            return_value={"status": "failed", "reason": "desktop_health_timeout"},
        ), mock.patch.object(
            lifecycle, "auto_rollback_adoption", return_value=failure
        ) as auto_rollback:
            lock.return_value.__enter__.return_value = None
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(lifecycle.command_adopt(args), 1)
        auto_rollback.assert_called_once()
        self.assertIsNone(auto_rollback.call_args.args[-1])
        self.assertEqual(json.loads(output.getvalue())["status"], "failed_auto_rolled_back")

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
