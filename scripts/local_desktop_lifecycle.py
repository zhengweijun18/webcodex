#!/usr/bin/env python3
"""Safe local WebCodex Desktop verify/install/rollback lifecycle."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_APP = Path("/Applications/WebCodex Desktop.app")
DEFAULT_STATE_ROOT = Path.home() / "Library/Application Support/dev.webcodex.desktop"
RUNTIMES = ("webcodex", "webcodex-server", "webcodex-runner")
IDENTITY_FIELDS = ("commit", "version", "built_at")
LIFECYCLE_LOCK_TIMEOUT = 60.0
RELEASE_STATE_SCHEMA = "webcodex-desktop-release-state.v1"


def run(args: list[str], *, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=check,
    )


def parse_version(line: str) -> dict:
    match = re.search(
        r"^(?P<name>\S+)\s+(?P<version>\S+)\s+\(commit\s+(?P<commit>[0-9a-f]+),"
        r"\s+dirty=(?P<dirty>true|false),\s+built_at=(?P<built_at>\d+)\)$",
        line.strip(),
    )
    if not match:
        raise RuntimeError(f"unexpected runtime version: {line.strip()}")
    value = match.groupdict()
    value["dirty"] = value["dirty"] == "true"
    value["built_at"] = int(value["built_at"])
    return value


def verify_app(app: Path) -> dict:
    if not app.is_dir():
        raise RuntimeError(f"Desktop app is missing: {app}")
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    runtime = app / "Contents/Resources/webcodex-runtime"
    values = {}
    for name in RUNTIMES:
        binary = runtime / name
        if not binary.is_file():
            raise RuntimeError(f"missing bundled runtime binary: {binary}")
        output = run([str(binary), "--version"]).stdout.splitlines()[0]
        values[name] = parse_version(output)
    commits = {value["commit"] for value in values.values()}
    versions = {value["version"] for value in values.values()}
    built_at = {value["built_at"] for value in values.values()}
    if len(commits) != 1 or len(versions) != 1 or len(built_at) != 1:
        raise RuntimeError("bundled runtimes do not share one source identity")
    if any(value["dirty"] for value in values.values()):
        raise RuntimeError("bundled runtime reports dirty=true")
    return {
        "app": str(app),
        "codesign": "verified",
        "commit": next(iter(commits)),
        "version": next(iter(versions)),
        "built_at": next(iter(built_at)),
        "runtimes": values,
    }


def desktop_process_status() -> dict[str, bool]:
    # Parse one process-table snapshot instead of relying on BSD pgrep -f.
    # On the supported Intel macOS host pgrep intermittently fails to match the
    # runner argv even though ps reports the exact command.
    result = run(["ps", "-axo", "command="], check=False)
    if result.returncode != 0:
        return {"desktop": False, "runner": False, "server": False}
    commands = result.stdout.splitlines()
    markers = {
        "desktop": "/Contents/MacOS/WebCodex",
        "runner": "webcodex-runtime/webcodex-runner",
        "server": "webcodex-runtime/webcodex-server",
    }
    return {
        name: any(marker in command for command in commands)
        for name, marker in markers.items()
    }


def desktop_running() -> bool:
    return any(desktop_process_status().values())


def critical_runtime_healthy(status: dict[str, bool]) -> bool:
    return bool(status.get("runner")) and bool(status.get("server"))


def same_identity(left: dict, right: dict) -> bool:
    return all(left.get(field) == right.get(field) for field in IDENTITY_FIELDS)


def wait_for_desktop_stopped(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not desktop_running():
            return True
        time.sleep(0.25)
    return not desktop_running()


@contextmanager
def lifecycle_lock(state_root: Path, timeout: float = LIFECYCLE_LOCK_TIMEOUT):
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "lifecycle.lock"
    with lock_path.open("a+") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("another Desktop lifecycle mutation is still running")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def safe_name(prefix: str, identity: dict) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{identity['version']}-{identity['commit'][:12]}-{stamp}.app"


def atomic_replace(candidate: Path, destination: Path) -> None:
    parent = destination.parent
    temp = parent / f".{destination.name}.new-{os.getpid()}"
    old = parent / f".{destination.name}.old-{os.getpid()}"
    shutil.rmtree(temp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    run(["ditto", str(candidate), str(temp)], timeout=180)
    run(["codesign", "--verify", "--deep", "--strict", str(temp)])
    try:
        if destination.exists():
            os.replace(destination, old)
        os.replace(temp, destination)
        run(["codesign", "--verify", "--deep", "--strict", str(destination)])
    except Exception:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if old.exists():
            os.replace(old, destination)
        raise
    else:
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def write_receipt(state_root: Path, payload: dict) -> Path:
    path = state_root / "patched-install-receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def history_event_id(payload: dict) -> str:
    previous = payload.get("previous") or payload.get("replaced") or {}
    rollback_source = payload.get("rollback_source") or {}
    identity = {
        "installed_at": payload.get("installed_at"),
        "operation": payload.get("operation"),
        "installed_commit": (payload.get("installed") or {}).get("commit"),
        "previous_commit": previous.get("commit"),
        "rollback_source_commit": rollback_source.get("commit"),
        "backup": payload.get("backup") or payload.get("safety_backup"),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"desktop-{digest[:24]}"


def append_history_record(
    state_root: Path,
    payload: dict,
    *,
    reconstructed: bool = False,
    evidence: dict | None = None,
) -> Path:
    path = state_root / "patched-install-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event_id = history_event_id(payload)
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                if json.loads(line).get("event_id") == event_id:
                    return path
            except json.JSONDecodeError:
                continue
    record = {
        "schema": "webcodex-local-desktop-history.v1",
        "event_id": event_id,
        "recorded_at": int(time.time()),
        "reconstructed": reconstructed,
        "evidence": evidence,
        "payload": payload,
    }
    with path.open("a") as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def record_mutation(state_root: Path, payload: dict) -> tuple[Path, Path]:
    history = append_history_record(state_root, payload)
    receipt = write_receipt(state_root, payload)
    return receipt, history


def release_state_path(state_root: Path) -> Path:
    return state_root / "desktop-release-state.json"


def read_release_state(state_root: Path) -> dict | None:
    path = release_state_path(state_root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid Desktop release state: {path}") from exc
    if value.get("schema") != RELEASE_STATE_SCHEMA:
        raise RuntimeError(f"unsupported Desktop release state schema: {path}")
    return value


def write_release_state(state_root: Path, payload: dict) -> Path:
    path = release_state_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": RELEASE_STATE_SCHEMA,
        "updated_at": int(time.time()),
        **payload,
    }
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def release_state_for_success(
    candidate: dict,
    installed: dict,
    previous: dict | None,
    backup: str | None,
    transaction: dict,
) -> dict:
    return {
        "installed": {"state": "healthy", "identity": installed},
        "candidate": {"state": "healthy", "identity": candidate},
        "last_known_good": installed,
        "rollback_target": (
            {"identity": previous, "app": backup} if previous is not None and backup else None
        ),
        "failed_candidate": None,
        "last_transaction": transaction,
    }


def release_state_for_failure(
    candidate: dict,
    restored: dict | None,
    backup: str | None,
    transaction: dict,
    *,
    prior_last_known_good: dict | None = None,
    restored_healthy: bool = False,
) -> dict:
    return {
        "installed": (
            {
                "state": "healthy" if restored_healthy else "verified_restored",
                "identity": restored,
            }
            if restored is not None
            else {"state": "failed", "identity": candidate}
        ),
        "candidate": {"state": "failed", "identity": candidate},
        "last_known_good": restored if restored_healthy else prior_last_known_good,
        "rollback_target": (
            {"identity": restored, "app": backup} if restored is not None and backup else None
        ),
        "failed_candidate": candidate,
        "last_transaction": transaction,
    }


def check_installed_health(
    app: Path,
    expected: dict,
    timeout: float,
    *,
    require_running: bool,
) -> dict:
    installed = verify_app(app)
    if not same_identity(installed, expected):
        return {
            "status": "failed",
            "reason": "installed_identity_mismatch",
            "installed": installed,
        }
    if require_running:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process_status = desktop_process_status()
            if critical_runtime_healthy(process_status):
                final = verify_app(app)
                if same_identity(final, expected):
                    return {
                        "status": "passed",
                        "require_running": True,
                        "desktop_running": True,
                        "process_status": process_status,
                        "installed": final,
                    }
                return {
                    "status": "failed",
                    "reason": "runtime_identity_changed",
                    "installed": final,
                }
            time.sleep(0.25)
        return {
            "status": "failed",
            "reason": "desktop_health_timeout",
            "desktop_running": desktop_running(),
            "process_status": desktop_process_status(),
            "timeout_secs": timeout,
        }
    return {
        "status": "passed",
        "require_running": False,
        "desktop_running": desktop_running(),
        "installed": installed,
    }


def already_installed_payload(args: argparse.Namespace, candidate: dict, installed: dict, operation: str) -> dict:
    return {
        "status": "passed",
        "operation": operation,
        "result": "already_installed",
        "installed": installed,
        "candidate": candidate,
        "desktop_running": desktop_running(),
        "receipt": str(args.state_root / "patched-install-receipt.json"),
        "history": str(args.state_root / "patched-install-history.jsonl"),
        "release_state": str(release_state_path(args.state_root)),
        "mutations_performed": False,
    }


def perform_install(
    args: argparse.Namespace,
    candidate: dict,
    previous: dict | None,
    operation: str,
    *,
    record: bool = True,
) -> dict:
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = None
    if previous is not None:
        backup = args.backup_dir / safe_name("WebCodex-Desktop-backup", previous)
        run(["ditto", str(args.app), str(backup)], timeout=180)
        verify_app(backup)
    atomic_replace(args.candidate, args.app)
    installed = verify_app(args.app)
    payload = {
        "installed_at": int(time.time()),
        "operation": operation,
        "installed": installed,
        "previous": previous,
        "backup": str(backup) if backup else None,
    }
    result = {
        "status": "passed",
        "mutations_performed": True,
        **payload,
    }
    if record:
        receipt, history = record_mutation(args.state_root, payload)
        result["receipt"] = str(receipt)
        result["history"] = str(history)
    else:
        result["receipt"] = None
        result["history"] = None
    return result


def build_prune_plan(args: argparse.Namespace, current: dict) -> dict:
    if args.keep_current < 1:
        raise RuntimeError("--keep-current must be at least 1")
    matching = []
    protected = []
    invalid = []
    if args.backup_dir.is_dir():
        for app in sorted(args.backup_dir.glob("WebCodex-Desktop-backup-*.app"), reverse=True):
            try:
                identity = verify_app(app)
            except Exception as exc:
                invalid.append({"app": str(app), "error": str(exc)})
                continue
            item = {"app": str(app), "identity": identity}
            if same_identity(identity, current):
                matching.append(item)
            else:
                protected.append(item)
    keep = matching[: args.keep_current]
    remove = matching[args.keep_current :]
    return {
        "current": current,
        "keep_current": args.keep_current,
        "kept": keep,
        "remove": remove,
        "protected_other_identity": protected,
        "invalid": invalid,
    }


def newest_other_identity_backup(args: argparse.Namespace, current: dict) -> dict | None:
    if not args.backup_dir.is_dir():
        return None
    def backup_time(app: Path) -> float:
        stat = app.stat()
        return float(getattr(stat, "st_birthtime", stat.st_mtime))

    backups = sorted(
        args.backup_dir.glob("*.app"),
        key=backup_time,
        reverse=True,
    )
    for app in backups:
        try:
            identity = verify_app(app)
        except Exception:
            continue
        if not same_identity(identity, current):
            return {"identity": identity, "app": str(app)}
    return None


def command_status(args: argparse.Namespace) -> int:
    identity = verify_app(args.app)
    backups = []
    if args.backup_dir.is_dir():
        for app in sorted(args.backup_dir.glob("*.app")):
            try:
                backups.append(verify_app(app))
            except Exception as exc:
                backups.append({"app": str(app), "error": str(exc)})
    payload = {
        "status": "passed",
        "installed": identity,
        "desktop_running": desktop_running(),
        "backups": backups,
        "receipt": str(args.state_root / "patched-install-receipt.json"),
        "history": str(args.state_root / "patched-install-history.jsonl"),
        "release_state": read_release_state(args.state_root),
        "mutations_performed": False,
    }
    print(json.dumps(payload, indent=2))
    return 0


def command_promote_current(args: argparse.Namespace) -> int:
    if args.confirm != "PROMOTE":
        raise SystemExit("--confirm PROMOTE is required")
    with lifecycle_lock(args.state_root):
        installed = verify_app(args.app)
        process_status = desktop_process_status()
        if not critical_runtime_healthy(process_status):
            raise RuntimeError(
                "current Desktop cannot be promoted to Last Known Good: "
                "runner and server must both be running"
            )
        rollback_target = newest_other_identity_backup(args, installed)
        state = write_release_state(
            args.state_root,
            {
                "installed": {"state": "healthy", "identity": installed},
                "candidate": None,
                "last_known_good": installed,
                "rollback_target": rollback_target,
                "failed_candidate": None,
                "last_transaction": {
                    "operation": "promote_current",
                    "status": "healthy_observed",
                    "process_status": process_status,
                },
            },
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "operation": "promote_current",
                    "last_known_good": installed,
                    "rollback_target": rollback_target,
                    "process_status": process_status,
                    "release_state": str(state),
                    "desktop_mutations_performed": False,
                    "mutations_performed": True,
                },
                indent=2,
            )
        )
    return 0


def command_verify(args: argparse.Namespace) -> int:
    print(json.dumps({"status": "passed", "candidate": verify_app(args.candidate)}, indent=2))
    return 0


def command_install(args: argparse.Namespace) -> int:
    if args.confirm != "INSTALL":
        raise SystemExit("--confirm INSTALL is required")
    candidate = verify_app(args.candidate)
    previous = verify_app(args.app) if args.app.exists() else None
    if previous is not None and same_identity(candidate, previous):
        print(json.dumps(already_installed_payload(args, candidate, previous, "install"), indent=2))
        return 0
    with lifecycle_lock(args.state_root):
        candidate = verify_app(args.candidate)
        previous = verify_app(args.app) if args.app.exists() else None
        if previous is not None and same_identity(candidate, previous):
            print(json.dumps(already_installed_payload(args, candidate, previous, "install"), indent=2))
            return 0
        if desktop_running():
            raise SystemExit("WebCodex Desktop/Server/Runner is running; quit it before install")
        result = perform_install(args, candidate, previous, "install")
        write_release_state(
            args.state_root,
            {
                "installed": {"state": "adopted", "identity": result["installed"]},
                "candidate": {"state": "adopted", "identity": candidate},
                "last_known_good": (
                    (read_release_state(args.state_root) or {}).get("last_known_good")
                ),
                "rollback_target": (
                    {"identity": previous, "app": result.get("backup")}
                    if previous is not None and result.get("backup")
                    else None
                ),
                "failed_candidate": None,
                "last_transaction": {
                    "operation": "install",
                    "status": "verified_installed_not_health_promoted",
                },
            },
        )
        print(json.dumps(result, indent=2))
    return 0


def request_desktop_quit() -> None:
    quit_result = run(
        ["osascript", "-e", 'tell application "WebCodex Desktop" to quit'],
        check=False,
    )
    if quit_result.returncode != 0:
        raise RuntimeError(f"failed to request Desktop quit: {quit_result.stderr.strip()}")


def stop_desktop_for_transaction(timeout: float) -> None:
    if not desktop_running():
        return
    request_desktop_quit()
    if not wait_for_desktop_stopped(timeout):
        raise RuntimeError("Desktop did not stop before transaction timeout")


def auto_rollback_adoption(
    args: argparse.Namespace,
    candidate: dict,
    previous: dict | None,
    backup: str | None,
    was_running: bool,
    health: dict,
    prior_last_known_good: dict | None,
) -> dict:
    restored = None
    restore_health = None
    backup_path = Path(backup) if backup else None
    if desktop_running():
        stop_desktop_for_transaction(args.quit_timeout)
    if previous is not None and backup_path is not None:
        source = verify_app(backup_path)
        if not same_identity(source, previous):
            raise RuntimeError("pre-transaction backup identity changed before auto rollback")
        atomic_replace(backup_path, args.app)
        restored = verify_app(args.app)
        if not same_identity(restored, previous):
            raise RuntimeError("auto rollback restored an unexpected Desktop identity")
        if was_running and not args.no_relaunch:
            run(["open", str(args.app)], check=True)
            restore_health = check_installed_health(
                args.app,
                restored,
                args.health_timeout,
                require_running=True,
            )
            if restore_health.get("status") != "passed":
                raise RuntimeError("auto rollback restored files but previous Desktop health check failed")
    transaction = {
        "operation": "adopt",
        "status": "failed_auto_rolled_back" if restored is not None else "failed_no_rollback_target",
        "candidate": candidate,
        "health": health,
        "restored": restored,
        "restore_health": restore_health,
    }
    restored_healthy = bool(
        restore_health is not None and restore_health.get("status") == "passed"
    )
    if restored is not None:
        payload = {
            "installed_at": int(time.time()),
            "operation": "adopt_auto_rollback",
            "installed": restored,
            "replaced": candidate,
            "rollback_source": restored,
            "safety_backup": backup,
            "failed_candidate": candidate,
            "health_failure": health,
        }
        receipt, history = record_mutation(args.state_root, payload)
    else:
        receipt = args.state_root / "patched-install-receipt.json"
        history = args.state_root / "patched-install-history.jsonl"
    state = write_release_state(
        args.state_root,
        release_state_for_failure(
            candidate,
            restored,
            backup,
            transaction,
            prior_last_known_good=prior_last_known_good,
            restored_healthy=restored_healthy,
        ),
    )
    return {
        "status": transaction["status"],
        "operation": "adopt",
        "candidate": candidate,
        "health": health,
        "restored": restored,
        "restore_health": restore_health,
        "receipt": str(receipt),
        "history": str(history),
        "release_state": str(state),
        "mutations_performed": True,
        "auto_rollback_performed": restored is not None,
    }


def command_adopt(args: argparse.Namespace) -> int:
    if args.confirm != "ADOPT":
        raise SystemExit("--confirm ADOPT is required")
    candidate = verify_app(args.candidate)
    previous = verify_app(args.app) if args.app.exists() else None
    if previous is not None and same_identity(candidate, previous):
        print(json.dumps(already_installed_payload(args, candidate, previous, "adopt"), indent=2))
        return 0
    with lifecycle_lock(args.state_root):
        candidate = verify_app(args.candidate)
        previous = verify_app(args.app) if args.app.exists() else None
        if previous is not None and same_identity(candidate, previous):
            print(json.dumps(already_installed_payload(args, candidate, previous, "adopt"), indent=2))
            return 0
        prior_state = read_release_state(args.state_root) or {}
        prior_last_known_good = prior_state.get("last_known_good")
        pre_process_status = desktop_process_status()
        was_running = any(pre_process_status.values())
        if (
            previous is not None
            and critical_runtime_healthy(pre_process_status)
            and (
                prior_last_known_good is None
                or not same_identity(prior_last_known_good, previous)
            )
        ):
            prior_last_known_good = previous
        if was_running:
            stop_desktop_for_transaction(args.quit_timeout)
        result = perform_install(args, candidate, previous, "adopt", record=False)
        write_release_state(
            args.state_root,
            {
                "installed": {"state": "adopted", "identity": result["installed"]},
                "candidate": {"state": "adopted", "identity": candidate},
                "last_known_good": prior_last_known_good,
                "rollback_target": (
                    {"identity": previous, "app": result.get("backup")}
                    if previous is not None and result.get("backup")
                    else None
                ),
                "failed_candidate": None,
                "last_transaction": {
                    "operation": "adopt",
                    "status": "installed_awaiting_health",
                },
            },
        )
        relaunched = False
        if not args.no_relaunch:
            run(["open", str(args.app)], check=True)
            relaunched = True
        health = check_installed_health(
            args.app,
            result["installed"],
            args.health_timeout,
            require_running=not args.no_relaunch,
        )
        if health.get("status") != "passed":
            failure = auto_rollback_adoption(
                args,
                candidate,
                previous,
                result.get("backup"),
                was_running,
                health,
                prior_last_known_good,
            )
            print(json.dumps(failure, indent=2))
            return 1
        result["health"] = health
        result["relaunch_requested"] = relaunched
        result["transaction_status"] = (
            "healthy" if not args.no_relaunch else "validated_unlaunched"
        )
        payload = {
            "installed_at": result["installed_at"],
            "operation": "adopt",
            "installed": result["installed"],
            "previous": previous,
            "backup": result.get("backup"),
            "health": health,
        }
        receipt, history = record_mutation(args.state_root, payload)
        result["receipt"] = str(receipt)
        result["history"] = str(history)
        state = write_release_state(
            args.state_root,
            (
                release_state_for_success(
                    candidate,
                    result["installed"],
                    previous,
                    result.get("backup"),
                    {
                        "operation": "adopt",
                        "status": result["transaction_status"],
                        "health": health,
                    },
                )
                if not args.no_relaunch
                else {
                    "installed": {"state": "adopted", "identity": result["installed"]},
                    "candidate": {"state": "adopted", "identity": candidate},
                    "last_known_good": prior_last_known_good,
                    "rollback_target": (
                        {"identity": previous, "app": result.get("backup")}
                        if previous is not None and result.get("backup")
                        else None
                    ),
                    "failed_candidate": None,
                    "last_transaction": {
                        "operation": "adopt",
                        "status": "validated_unlaunched",
                        "health": health,
                    },
                }
            ),
        )
        result["release_state"] = str(state)
        print(json.dumps(result, indent=2))
    return 0


def command_prune_backups(args: argparse.Namespace) -> int:
    current = verify_app(args.app)
    plan = build_prune_plan(args, current)
    if args.confirm is None:
        print(json.dumps({"status": "passed", "result": "preview", "mutations_performed": False, **plan}, indent=2))
        return 0
    if args.confirm != "PRUNE":
        raise SystemExit("--confirm PRUNE is required to delete redundant backups")
    with lifecycle_lock(args.state_root):
        current = verify_app(args.app)
        plan = build_prune_plan(args, current)
        removed = []
        for item in plan["remove"]:
            app = Path(item["app"])
            identity = verify_app(app)
            if not same_identity(identity, current):
                raise RuntimeError(f"backup identity changed before prune: {app}")
            shutil.rmtree(app)
            removed.append(str(app))
        print(
            json.dumps(
                {
                    "status": "passed",
                    "result": "pruned",
                    "mutations_performed": bool(removed),
                    "removed": removed,
                    **plan,
                },
                indent=2,
            )
        )
    return 0


def command_rollback(args: argparse.Namespace) -> int:
    if args.confirm != "ROLLBACK":
        raise SystemExit("--confirm ROLLBACK is required")
    with lifecycle_lock(args.state_root):
        if desktop_running():
            raise SystemExit("WebCodex Desktop/Server/Runner is running; quit it before rollback")
        source = verify_app(args.backup)
        current = verify_app(args.app) if args.app.exists() else None
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        safety_backup = None
        if current is not None:
            safety_backup = args.backup_dir / safe_name("WebCodex-Desktop-pre-rollback", current)
            run(["ditto", str(args.app), str(safety_backup)], timeout=180)
            verify_app(safety_backup)
        atomic_replace(args.backup, args.app)
        installed = verify_app(args.app)
        payload = {
            "installed_at": int(time.time()),
            "operation": "rollback",
            "installed": installed,
            "replaced": current,
            "safety_backup": str(safety_backup) if safety_backup else None,
            "rollback_source": source,
        }
        receipt, history = record_mutation(args.state_root, payload)
        state = write_release_state(
            args.state_root,
            {
                "installed": {"state": "adopted", "identity": installed},
                "candidate": None,
                "last_known_good": source,
                "rollback_target": (
                    {"identity": current, "app": str(safety_backup)}
                    if current is not None and safety_backup is not None
                    else None
                ),
                "failed_candidate": None,
                "last_transaction": {
                    "operation": "rollback",
                    "status": "verified_installed_not_health_promoted",
                },
            },
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "receipt": str(receipt),
                    "history": str(history),
                    "release_state": str(state),
                    **payload,
                },
                indent=2,
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_STATE_ROOT / "backups")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    promote = sub.add_parser("promote-current")
    promote.add_argument("--confirm", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--candidate", type=Path, required=True)
    install = sub.add_parser("install")
    install.add_argument("--candidate", type=Path, required=True)
    install.add_argument("--confirm", required=True)
    adopt = sub.add_parser("adopt")
    adopt.add_argument("--candidate", type=Path, required=True)
    adopt.add_argument("--confirm", required=True)
    adopt.add_argument("--quit-timeout", type=float, default=40.0)
    adopt.add_argument("--health-timeout", type=float, default=30.0)
    adopt.add_argument("--no-relaunch", action="store_true")
    prune = sub.add_parser("prune-backups")
    prune.add_argument("--keep-current", type=int, default=1)
    prune.add_argument("--confirm")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--backup", type=Path, required=True)
    rollback.add_argument("--confirm", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "promote-current":
            return command_promote_current(args)
        if args.command == "verify":
            return command_verify(args)
        if args.command == "install":
            return command_install(args)
        if args.command == "adopt":
            return command_adopt(args)
        if args.command == "prune-backups":
            return command_prune_backups(args)
        if args.command == "rollback":
            return command_rollback(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"local Desktop lifecycle failed: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
