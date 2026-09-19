#!/usr/bin/env python3
"""Pinned Vue LSP toolchain status/install for local WebCodex Desktop."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VUE_VERSION = "2.2.12"
TYPESCRIPT_VERSION = "5.9.3"
DEFAULT_ROOT = (
    Path.home()
    / "Library/Application Support/dev.webcodex.desktop/local-tools"
    / f"vue-lsp-{VUE_VERSION}"
)
DEFAULT_PLIST = (
    Path.home()
    / "Library/LaunchAgents/dev.webcodex.desktop.vue-lsp-env.plist"
)
LABEL = "dev.webcodex.desktop.vue-lsp-env"


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=check,
    )


def paths(root: Path) -> dict[str, Path]:
    return {
        "server": root / "bin/vue-language-server",
        "vue_package": root / "node_modules/@vue/language-server/package.json",
        "ts_package": root / "node_modules/typescript/package.json",
        "tsdk": root / "node_modules/typescript/lib",
    }


def package_version(path: Path) -> str:
    return str(json.loads(path.read_text())["version"])


def status(root: Path, plist: Path) -> tuple[bool, dict]:
    values = paths(root)
    missing = [str(path) for path in values.values() if not path.exists()]
    result = {
        "root": str(root),
        "expected_vue_version": VUE_VERSION,
        "expected_typescript_version": TYPESCRIPT_VERSION,
        "missing": missing,
        "launch_agent": str(plist),
    }
    if missing:
        result["status"] = "missing"
        return False, result

    vue_version = package_version(values["vue_package"])
    ts_version = package_version(values["ts_package"])
    wrapper = run([str(values["server"]), "--version"], check=False)
    result.update(
        {
            "vue_version": vue_version,
            "typescript_version": ts_version,
            "wrapper_version": wrapper.stdout.strip(),
            "wrapper_exit": wrapper.returncode,
            "plist_exists": plist.is_file(),
        }
    )

    if sys.platform == "darwin":
        launch_server = run(
            ["launchctl", "getenv", "WEBCODEX_VUE_LANGUAGE_SERVER"],
            check=False,
        ).stdout.strip()
        launch_tsdk = run(
            ["launchctl", "getenv", "WEBCODEX_VUE_TSDK"],
            check=False,
        ).stdout.strip()
        result["launchctl_server"] = launch_server
        result["launchctl_tsdk"] = launch_tsdk
    else:
        launch_server = os.environ.get("WEBCODEX_VUE_LANGUAGE_SERVER", "")
        launch_tsdk = os.environ.get("WEBCODEX_VUE_TSDK", "")

    healthy = (
        vue_version == VUE_VERSION
        and ts_version == TYPESCRIPT_VERSION
        and wrapper.returncode == 0
        and wrapper.stdout.strip() == VUE_VERSION
        and launch_server == str(values["server"])
        and launch_tsdk == str(values["tsdk"])
        and (sys.platform != "darwin" or plist.is_file())
    )
    result["status"] = "passed" if healthy else "drift"
    return healthy, result


def install(root: Path, plist: Path, proxy: str | None) -> dict:
    if sys.platform != "darwin":
        raise RuntimeError("automatic persisted Vue LSP installation is currently macOS-only")
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("node and npm must be installed")

    state_parent = root.parent
    state_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".vue-lsp-stage-", dir=state_parent))
    backup: Path | None = None
    root_replaced = False
    previous_plist = plist.read_bytes() if plist.is_file() else None
    previous_server = run(
        ["launchctl", "getenv", "WEBCODEX_VUE_LANGUAGE_SERVER"],
        check=False,
    ).stdout.strip()
    previous_tsdk = run(
        ["launchctl", "getenv", "WEBCODEX_VUE_TSDK"],
        check=False,
    ).stdout.strip()

    try:
        package = {
            "private": True,
            "dependencies": {
                "@vue/language-server": VUE_VERSION,
                "typescript": TYPESCRIPT_VERSION,
            },
        }
        (stage / "package.json").write_text(json.dumps(package, indent=2) + "\n")

        env = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf",
            "GIT_CONFIG_VALUE_0": "ssh://git@github.com/",
            "GIT_CONFIG_KEY_1": "url.https://github.com/.insteadOf",
            "GIT_CONFIG_VALUE_1": "git@github.com:",
        }
        if proxy:
            env.update(
                {
                    "HTTP_PROXY": proxy,
                    "HTTPS_PROXY": proxy,
                    "http_proxy": proxy,
                    "https_proxy": proxy,
                    "npm_config_proxy": proxy,
                    "npm_config_https_proxy": proxy,
                }
            )
        run(
            [
                npm,
                "install",
                "--prefix",
                str(stage),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            env=env,
            timeout=300,
        )

        bin_dir = stage / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "vue-language-server"
        wrapper.write_text(
            "#!/bin/sh\n"
            "set -e\n"
            'BASE="$(cd "$(dirname "$0")/.." && pwd)"\n'
            f'exec "{node}" "$BASE/node_modules/@vue/language-server/bin/vue-language-server.js" "$@"\n'
        )
        wrapper.chmod(0o755)

        staged_vue = package_version(stage / "node_modules/@vue/language-server/package.json")
        staged_ts = package_version(stage / "node_modules/typescript/package.json")
        wrapper_version = run([str(wrapper), "--version"]).stdout.strip()
        if (
            staged_vue != VUE_VERSION
            or staged_ts != TYPESCRIPT_VERSION
            or wrapper_version != VUE_VERSION
        ):
            raise RuntimeError("staged Vue LSP toolchain identity mismatch")

        if root.exists():
            backup = state_parent / f"{root.name}.backup-{int(time.time())}"
            os.replace(root, backup)
        os.replace(stage, root)
        root_replaced = True

        server = root / "bin/vue-language-server"
        tsdk = root / "node_modules/typescript/lib"
        payload = {
            "Label": LABEL,
            "ProgramArguments": [
                "/bin/sh",
                "-c",
                'launchctl setenv WEBCODEX_VUE_LANGUAGE_SERVER "$1/bin/vue-language-server"; '
                'launchctl setenv WEBCODEX_VUE_TSDK "$1/node_modules/typescript/lib"',
                "sh",
                str(root),
            ],
            "RunAtLoad": True,
        }
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)
        plist.chmod(0o600)

        uid = str(os.getuid())
        run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)
        run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
        run(["launchctl", "setenv", "WEBCODEX_VUE_LANGUAGE_SERVER", str(server)])
        run(["launchctl", "setenv", "WEBCODEX_VUE_TSDK", str(tsdk)])

        healthy, result = status(root, plist)
        if not healthy:
            raise RuntimeError(f"installed Vue LSP toolchain failed status check: {result}")
        result["backup"] = str(backup) if backup else None
        return result
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if root_replaced and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        if backup is not None and backup.exists():
            os.replace(backup, root)

        if previous_plist is None:
            plist.unlink(missing_ok=True)
        else:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_bytes(previous_plist)
            plist.chmod(0o600)

        uid = str(os.getuid())
        run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)
        if previous_plist is not None:
            run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                check=False,
            )
        if previous_server:
            run(
                ["launchctl", "setenv", "WEBCODEX_VUE_LANGUAGE_SERVER", previous_server],
                check=False,
            )
        else:
            run(
                ["launchctl", "unsetenv", "WEBCODEX_VUE_LANGUAGE_SERVER"],
                check=False,
            )
        if previous_tsdk:
            run(
                ["launchctl", "setenv", "WEBCODEX_VUE_TSDK", previous_tsdk],
                check=False,
            )
        else:
            run(["launchctl", "unsetenv", "WEBCODEX_VUE_TSDK"], check=False)
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    value.add_argument("--plist", type=Path, default=DEFAULT_PLIST)
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--confirm", required=True)
    install_cmd.add_argument("--proxy")
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "status":
            healthy, result = status(args.root, args.plist)
            print(json.dumps(result, indent=2))
            return 0 if healthy else 1
        if args.command == "install":
            if args.confirm != "INSTALL":
                raise SystemExit("--confirm INSTALL is required")
            print(json.dumps(install(args.root, args.plist, args.proxy), indent=2))
            return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Vue LSP toolchain operation failed: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
