#!/usr/bin/env python3
"""Safe local WebCodex Desktop verify/install/rollback lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_APP = Path("/Applications/WebCodex Desktop.app")
DEFAULT_STATE_ROOT = Path.home() / "Library/Application Support/dev.webcodex.desktop"
RUNTIMES = ("webcodex", "webcodex-server", "webcodex-runner")


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


def desktop_running() -> bool:
    checks = [
        ["pgrep", "-x", "WebCodex"],
        ["pgrep", "-f", "/Applications/WebCodex Desktop.app/Contents/Resources/webcodex-runtime/webcodex-runner"],
        ["pgrep", "-f", "/Applications/WebCodex Desktop.app/Contents/Resources/webcodex-runtime/webcodex-server"],
    ]
    return any(run(command, check=False).returncode == 0 for command in checks)


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
        "mutations_performed": False,
    }
    print(json.dumps(payload, indent=2))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    print(json.dumps({"status": "passed", "candidate": verify_app(args.candidate)}, indent=2))
    return 0


def command_install(args: argparse.Namespace) -> int:
    if args.confirm != "INSTALL":
        raise SystemExit("--confirm INSTALL is required")
    if desktop_running():
        raise SystemExit("WebCodex Desktop/Server/Runner is running; quit it before install")
    candidate = verify_app(args.candidate)
    previous = verify_app(args.app) if args.app.exists() else None
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
        "operation": "install",
        "installed": installed,
        "previous": previous,
        "backup": str(backup) if backup else None,
    }
    receipt = write_receipt(args.state_root, payload)
    print(json.dumps({"status": "passed", "receipt": str(receipt), **payload}, indent=2))
    return 0


def command_rollback(args: argparse.Namespace) -> int:
    if args.confirm != "ROLLBACK":
        raise SystemExit("--confirm ROLLBACK is required")
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
    receipt = write_receipt(args.state_root, payload)
    print(json.dumps({"status": "passed", "receipt": str(receipt), **payload}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_STATE_ROOT / "backups")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    verify = sub.add_parser("verify")
    verify.add_argument("--candidate", type=Path, required=True)
    install = sub.add_parser("install")
    install.add_argument("--candidate", type=Path, required=True)
    install.add_argument("--confirm", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--backup", type=Path, required=True)
    rollback.add_argument("--confirm", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "verify":
            return command_verify(args)
        if args.command == "install":
            return command_install(args)
        if args.command == "rollback":
            return command_rollback(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"local Desktop lifecycle failed: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
