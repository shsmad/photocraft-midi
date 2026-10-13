"""Минимальный MCP-клиент (JSON-RPC 2.0) поверх stdio `photocraft-cli mcp --bridge`.

Аутентификацию токеном берёт на себя сам CLI; здесь только handshake
andериализация инструментов: initialize -> notifications/initialized -> tools/call.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import logging
import os
import shutil

from pathlib import Path
from typing import Any

logger = logging.getLogger("photocraftmidi.mcp")

INITIALIZE_TIMEOUT = 15.0
CALL_TIMEOUT = 20.0
# Клиентский таймаут ниже серверного (55 c bridge против 60 c desktop), чтобы
# ошибка бриджа была видна клиенту, а не серверу.


class McpError(Exception):
    """Логическая ошибка MCP (не транспортная): серверный tool вернул isError."""


class McpConnectionError(McpError):
    """Транспортная ошибка: мост умер, таймаут, процесс недоступен."""


def discover_cli() -> Path | None:
    """Найти `photocraft-cli`: PHOTOCRAFT_CLI -> PATH -> AppImage-маунт."""
    override = os.environ.get("PHOTOCRAFT_CLI")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
        logger.warning("PHOTOCRAFT_CLI=%s указан, но файла нет", path)
    found = shutil.which("photocraft-cli")
    if found:
        return Path(found)
    # Маунт AppImage: имя точки монтирования меняется на каждом запуске
    # (напр. /tmp/.mount_photocNfBIii), поэтому ищем по широкому шаблону.
    for candidate in glob.glob("/tmp/.mount_*/usr/bin/photocraft-cli"):  # noqa: S108
        return Path(candidate)
    return None


class MCPClient:
    """Serialized JSON-RPC по stdio подсоединённого к GUI CLI-бриджа."""

    def __init__(
        self,
        cli: Path,
        port: int,
        token_file: Path,
        *,
        call_timeout: float = CALL_TIMEOUT,
    ) -> None:
        self._cmd = [
            str(cli),
            "mcp",
            "--bridge",
            f"127.0.0.1:{port}",
            "--control-token-file",
            str(token_file),
        ]
        self._call_timeout = call_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._id = 0
        self._initialized = False

    @property
    def connected(self) -> bool:
        """Мост запущен, handshake пройден и процесс жив."""
        return self._initialized and self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Запустить мост и пройти MCP handshake (идемпотентно)."""
        if self.connected:
            return
        await self.close()
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader = asyncio.create_task(self._read_loop())
        try:
            data = await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "photocraft-midi", "version": "0.1.0"},
                },
                timeout=INITIALIZE_TIMEOUT,
            )
        except McpError:
            raise
        except Exception as exc:
            raise McpConnectionError(f"initialize: {exc}") from exc
        try:
            protocol = data["result"]["protocolVersion"]
        except (KeyError, TypeError) as exc:
            raise McpConnectionError(f"initialize: неожиданный ответ: {data!r}") from exc
        logger.info("MCP bridge готов: protocolVersion=%s", protocol)
        await self._notify("notifications/initialized")
        self._initialized = True

    async def close(self) -> None:
        self._initialized = False
        if self._proc is None:
            return
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        with contextlib.suppress(Exception):
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=2.0)
        self._proc = None

    async def tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Вызвать MCP-инструмент и вернуть декодированный результат."""
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        data = await self._request("tools/call", params, timeout=self._call_timeout)
        try:
            result = data["result"]
        except (KeyError, TypeError) as exc:
            raise McpConnectionError(f"{name}: неожиданный ответ {data!r}") from exc
        if isinstance(result, dict) and result.get("isError"):
            detail = _content_text(result)
            raise McpError(detail or f"инструмент {name!r} вернул ошибку: {result!r}")
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        if structured is not None:
            return structured
        text = _content_text(result)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
        return result

    async def command_run(self, command_id: str, params: dict[str, Any] | None = None) -> Any:
        arguments: dict[str, Any] = {"id": command_id}
        if params is not None:
            arguments["params"] = params
        return await self.tool("command_run", arguments)

    async def ui_set(self, fields: dict[str, Any]) -> Any:
        return await self.tool("ui_set", {"fields": fields})

    async def command_list(self, filter_text: str | None = None) -> Any:
        arguments: dict[str, Any] = {}
        if filter_text is not None:
            arguments["filter"] = filter_text
        return await self.tool("command_list", arguments)

    async def ui_inspect(self) -> Any:
        return await self.tool("ui_inspect")

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise McpConnectionError("клиент не запущен")
        self._id += 1
        rid = self._id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, ensure_ascii=False)
        try:
            self._proc.stdin.write((payload + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(rid, None)
            raise McpConnectionError(f"{method}({rid}): таймаут {timeout:.0f} c — мост мог подвиснуть") from exc
        except (ConnectionError, OSError) as exc:
            self._pending.pop(rid, None)
            raise McpConnectionError(f"{method}({rid}): транспортная ошибка: {exc}") from exc

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpConnectionError("клиент не запущен")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            raise McpConnectionError("клиент не запущен: нет stdout")
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                self._initialized = False
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(McpConnectionError("MCP мост завершился"))
                self._pending.clear()
                return
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("не-JSON строка от моста: %s", raw)
                continue
            rid = data.get("id")
            if isinstance(rid, int) and rid in self._pending:
                fut = self._pending.pop(rid)
                if fut.done():
                    continue
                if isinstance(data.get("error"), dict):
                    err = data["error"]
                    fut.set_exception(McpError(err.get("message") or json.dumps(err, ensure_ascii=False)))
                else:
                    fut.set_result(data)


def _content_text(result: Any) -> str:
    """Собрать текст из content-блоков MCP-ответа."""
    if not isinstance(result, dict):
        return ""
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return ""
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(t for t in texts if isinstance(t, str))
