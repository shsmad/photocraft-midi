"""Тест MCP-клиента против фейкового stdio-сервера (без PhotoCraft)."""

from __future__ import annotations

import stat

from pathlib import Path

from photocraftmidi.mcp_client import MCPClient

FAKE_CLI = '''#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "id" not in msg:
        continue
    rid = msg["id"]
    if msg["method"] == "initialize":
        res = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0"}}
    elif msg["method"] == "tools/call":
        res = {
            "content": [{"type": "text", "text": json.dumps({"ok": True, "name": msg["params"]["name"]})}],
            "isError": False,
        }
    else:
        res = {}
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}), flush=True)
'''


def _fake_cli(tmp_path: Path) -> Path:
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE_CLI, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


async def test_handshake_and_tool(tmp_path: Path) -> None:
    client = MCPClient(_fake_cli(tmp_path), 7878, tmp_path / "token")
    assert not client.connected
    await client.start()
    assert client.connected
    assert await client.command_run("edit.undo", {}) == {"ok": True, "name": "command_run"}
    assert await client.ui_set({"tool": "brush"}) == {"ok": True, "name": "ui_set"}
    await client.close()
    assert not client.connected


async def test_start_is_idempotent(tmp_path: Path) -> None:
    client = MCPClient(_fake_cli(tmp_path), 7878, tmp_path / "token")
    await client.start()
    await client.start()  # повторный старт не ломает
    assert client.connected
    await client.close()
