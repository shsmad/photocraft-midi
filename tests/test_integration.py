"""Интеграционные тесты с живым PhotoCraft (помечены `integration`, пропускаются без env)."""

from __future__ import annotations

import os

from pathlib import Path

import pytest

from photocraftmidi.mcp_client import MCPClient, discover_cli

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("PHOTOCRAFT_CONTROL_PORT"), reason="нужен живой PhotoCraft"),
]


async def test_ui_inspect_live() -> None:
    port = int(os.environ["PHOTOCRAFT_CONTROL_PORT"])
    token = Path(os.environ["PHOTOCRAFT_CONTROL_TOKEN_FILE"]).expanduser()
    cli = discover_cli()
    assert cli is not None
    client = MCPClient(cli, port, token)
    await client.start()
    try:
        ui = await client.ui_inspect()
        assert isinstance(ui, dict)
        assert "tool" in ui
    finally:
        await client.close()
