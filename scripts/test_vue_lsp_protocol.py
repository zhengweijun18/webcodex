#!/usr/bin/env python3
"""Real standard-LSP smoke for the Vue SFC fork patch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--tsdk", required=True)
    return parser.parse_args()


class LspClient:
    def __init__(self, process: subprocess.Popen[bytes], root: Path) -> None:
        self.process = process
        self.root = root

    def send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.process.stdin.write(
            f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        )
        self.process.stdin.flush()

    def read_one(self, deadline: float) -> dict:
        assert self.process.stdout is not None
        fd = self.process.stdout.fileno()
        header = b""
        while b"\r\n\r\n" not in header:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("LSP header timeout")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError("LSP header timeout")
            chunk = os.read(fd, 1)
            if not chunk:
                raise RuntimeError("language server stdout closed")
            header += chunk

        raw_header, body = header.split(b"\r\n\r\n", 1)
        length = None
        for line in raw_header.decode(errors="replace").split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
                break
        if length is None:
            raise RuntimeError(f"missing Content-Length: {raw_header!r}")

        while len(body) < length:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("LSP body timeout")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError("LSP body timeout")
            chunk = os.read(fd, length - len(body))
            if not chunk:
                raise RuntimeError("language server stdout closed")
            body += chunk
        return json.loads(body[:length])

    def wait_id(self, request_id: int, timeout: float = 25) -> dict:
        deadline = time.time() + timeout
        while True:
            message = self.read_one(deadline)
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                return message
            if "id" in message and "method" in message:
                method = message["method"]
                if method == "workspace/configuration":
                    items = (message.get("params") or {}).get("items") or []
                    result = [None for _ in items]
                elif method == "workspace/workspaceFolders":
                    result = [{"uri": self.root.as_uri(), "name": "project"}]
                else:
                    result = None
                self.send(
                    {"jsonrpc": "2.0", "id": message["id"], "result": result}
                )


def checked_result(message: dict, label: str):
    if "error" in message:
        raise RuntimeError(f"{label} error: {message['error']}")
    return message.get("result")


def main() -> int:
    args = parse_args()
    server = Path(args.server).resolve()
    tsdk = Path(args.tsdk).resolve()
    if not server.is_file():
        raise SystemExit(f"Vue language server not found: {server}")
    if not tsdk.is_dir():
        raise SystemExit(f"TypeScript SDK not found: {tsdk}")

    with tempfile.TemporaryDirectory(prefix="webcodex-vue-lsp-") as tmp:
        root = Path(tmp) / "project"
        (root / "src").mkdir(parents=True)
        (root / "tsconfig.json").write_text(
            '{\n  "compilerOptions": {"target":"ES2020","module":"ESNext","strict":true},\n'
            '  "include":["src/**/*.vue","src/**/*.ts"]\n}\n'
        )
        (root / "vue.config.js").write_text("module.exports = {};\n")
        app = root / "src/App.vue"
        app.write_text(
            '<script setup lang="ts">\n'
            'const message = "world";\n'
            'function greet(name: string) {\n'
            '  return `hello ${name}`;\n'
            '}\n'
            'const output = greet(message);\n'
            '</script>\n\n'
            '<template>\n'
            '  <main>{{ output }}</main>\n'
            '</template>\n'
        )

        env = os.environ.copy()
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env[key] = "http://127.0.0.1:0"
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"

        process = subprocess.Popen(
            [str(server), "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        client = LspClient(process, root)

        try:
            client.send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "processId": os.getpid(),
                        "clientInfo": {"name": "WebCodex agent"},
                        "rootUri": root.as_uri(),
                        "workspaceFolders": [
                            {"uri": root.as_uri(), "name": "project"}
                        ],
                        "initializationOptions": {
                            "typescript": {
                                "tsdk": str(tsdk),
                                "disableAutoImportCache": True,
                            },
                            "vue": {"hybridMode": False},
                        },
                        "capabilities": {
                            "general": {
                                "positionEncodings": ["utf-8", "utf-16", "utf-32"]
                            },
                            "workspace": {
                                "configuration": True,
                                "workspaceFolders": True,
                            },
                            "textDocument": {
                                "documentSymbol": {
                                    "hierarchicalDocumentSymbolSupport": True
                                },
                                "definition": {"linkSupport": True},
                                "references": {},
                                "hover": {
                                    "contentFormat": ["markdown", "plaintext"]
                                },
                            },
                        },
                    },
                }
            )
            checked_result(client.wait_id(1, 30), "initialize")
            client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            text = app.read_text()
            uri = app.as_uri()
            client.send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "vue",
                            "version": 1,
                            "text": text,
                        }
                    },
                }
            )

            client.send(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "textDocument/documentSymbol",
                    "params": {"textDocument": {"uri": uri}},
                }
            )
            symbols = checked_result(client.wait_id(2, 30), "documentSymbol") or []
            if not symbols:
                raise RuntimeError("documentSymbol returned no Vue symbols")

            client.send(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "textDocument/definition",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {"line": 5, "character": 16},
                    },
                }
            )
            definition = checked_result(client.wait_id(3, 30), "definition")
            if not definition:
                raise RuntimeError("definition returned no location")

            client.send(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "textDocument/references",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {"line": 2, "character": 10},
                        "context": {"includeDeclaration": True},
                    },
                }
            )
            references = checked_result(client.wait_id(4, 30), "references") or []
            if len(references) < 2:
                raise RuntimeError(
                    f"expected at least two greet references, got {len(references)}"
                )

            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "symbol_count": len(symbols),
                        "definition_count": (
                            len(definition) if isinstance(definition, list) else 1
                        ),
                        "reference_count": len(references),
                        "server": str(server),
                        "tsdk": str(tsdk),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        finally:
            try:
                client.send(
                    {"jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": None}
                )
                client.wait_id(99, 3)
                client.send({"jsonrpc": "2.0", "method": "exit", "params": None})
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
