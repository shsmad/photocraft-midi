"""Точка входа продолжительного процесса: `python -m photocraftmidi`.

Слушает X-Touch Mini, транслирует CC в команды PhotoCraft через MCP-bridge,
отдаёт LED-обратную связь. При недоступности MIDI/bridge — ждёт и переподключается.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import math
import os
import sys

from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import animations
from .mapping import Action, EncoderSpec, LogSpec, Mapping, default_config_path, load_config
from .mcp_client import MCPClient, McpConnectionError, McpError, discover_cli
from .midi import XTouchMidi

logger = logging.getLogger("photocraftmidi")
COALESCE_SECONDS = 0.040  # окно коалесценции событий энкодера
FAST_STEP = 2  # во сколько раз крупнее шаг быстрого поворота относительного энкодера
EPSILON_REL = 1e-3  # порог сравнения float при синке (доля диапазона)
RETRY_SECONDS = 2.0  # пауза между попытками переподключения MIDI/bridge
LED_BRUSH_TRANSFER_OPACITY = "brush.transfer.opacity"  # led: opacity кисти "from pressure"


def _close(a: float, b: float, min_v: float, max_v: float) -> bool:
    """Считать значения совпадающими с точностью EPSILON_REL от диапазона."""
    return abs(a - b) <= max(1e-9, EPSILON_REL * abs(max_v - min_v))


def configure_logging(spec: LogSpec) -> None:
    """Лог в stderr и в файл (путь/уровень/ротация из [log]; env переопределяет уровень)."""
    level_name = os.environ.get("PHOTOCRAFT_MIDI_LOG", spec.level).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = Path(spec.path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root = logging.getLogger("photocraftmidi")
    root.setLevel(level)
    root.propagate = False

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=spec.max_bytes, backupCount=spec.backups, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


class EncoderState:
    """Внутреннее значение абсолютного энкодера и последняя сырая позиция устройства."""

    def __init__(self) -> None:
        self.value: float | None = None
        self.last_raw: int | None = None


class BridgeApp:
    def __init__(self, midi: XTouchMidi, client: MCPClient, mapping: Mapping) -> None:
        self._midi = midi
        self._client = client
        self._mapping = mapping
        self._scheme = mapping.encoder_scheme
        self._sync = mapping.sync
        self._states: dict[str, EncoderState] = {}
        self._mirror: dict[str, Any] = {}
        self._led_brush: dict[str, Any] | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self._last_touch: dict[int, float] = {}
        self._enc_buffer: dict[int, int] = {}
        self._enc_tasks: dict[int, asyncio.Task[None]] = {}
        self._tool_group: dict[int, str] = {}
        self._encoders_by_key: dict[str, EncoderSpec] = {}
        for spec in mapping.buttons.values():
            if isinstance(spec.led, str) and spec.led.startswith("tool:"):
                self._tool_group[spec.cc] = spec.led.split(":", 1)[1]
        for spec in mapping.encoders.values():
            if spec.key is not None:
                self._encoders_by_key[spec.key] = spec
                self._states[spec.key] = EncoderState()
        if not self._tool_group and not mapping.buttons and not mapping.encoders:
            raise ValueError("config.toml: карта пуста")

    async def run(self, events: asyncio.Queue[tuple[str, int, int]]) -> None:
        logger.info("запуск: buttons=%d encoders=%d tool_group=%s sync=%s",
                    len(self._mapping.buttons), len(self._mapping.encoders),
                    sorted(self._tool_group), self._sync.enabled)
        try:
            while True:
                await self._ensure_midi()
                await self._wait_for_bridge()
                await animations.blink_all(self._midi)
                await animations.snake(self._midi)
                self._last_snapshot = None  # новое подключение — заново логируем первое состояние
                try:
                    await self._sync_once()  # первое состояние: опрос + логирование + LED/счётчики
                    await self._event_loop(events)
                except McpConnectionError:
                    logger.warning("bridge потерян — переподключаюсь")
                finally:
                    await self._client.close()
        finally:
            self._midi.close()
            await self._client.close()

    async def _ensure_midi(self) -> None:
        """Открыть MIDI-порт X-TOUCH MINI, повторяя попытки до успеха."""
        while not self._midi.is_open():
            try:
                self._midi.close()
                self._midi.open()
                logger.info("MIDI X-TOUCH MINI открыт")
            except RuntimeError as exc:
                logger.warning("MIDI недоступен: %s", exc)
                await asyncio.sleep(RETRY_SECONDS)

    async def _wait_for_bridge(self) -> None:
        """Подключить MCP-bridge; пока ждём (MIDI открыт) — крутим анимацию ожидания."""
        if self._client.connected:
            return
        bounce = asyncio.create_task(animations.bounce(self._midi))
        try:
            while not self._client.connected:
                try:
                    await self._client.start()
                except McpConnectionError as exc:
                    logger.warning("bridge недоступен: %s", exc)
                    await asyncio.sleep(RETRY_SECONDS)
        finally:
            bounce.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bounce

    async def _event_loop(self, events: asyncio.Queue[tuple[str, int, int]]) -> None:
        """Обработка событий; синк встроен как таймаут ожидания. McpConnectionError — наверх."""
        interval = self._sync.interval_ms / 1000.0
        while True:
            if self._sync.enabled:
                try:
                    kind, cc, value = await asyncio.wait_for(events.get(), timeout=interval)
                except TimeoutError:
                    if not self._midi.is_open():
                        return  # устройство пропало — наверх, переоткроем MIDI
                    await self._sync_once()
                    continue
            else:
                kind, cc, value = await events.get()
            if kind != "cc":
                continue
            self._last_touch[cc] = asyncio.get_running_loop().time()
            if cc in self._mapping.encoders:
                await self._handle_encoder(cc, value)
            elif cc in self._mapping.buttons:
                await self._handle_button(cc, value)

    async def _handle_button(self, cc: int, value: int) -> None:
        spec = self._mapping.buttons.get(cc)
        if spec is None:
            logger.debug("кнопка вне карты: CC %d", cc)
            return
        logger.info("кнопка CC=%d (%s) '%s' value=%d", cc, spec.kind, spec.led, value)
        arguments = copy.deepcopy(spec.action.args)
        mapped: Any | None = None
        if spec.inject is not None:
            # значение CC и есть целевое состояние: `states` задаёт карту значения -> целевое,
            # иначе 127=вкл / 0=выкл; синк держит защёлку/LED в соответствии с приложением.
            target: Any = spec.states.get(value, value > 0) if spec.states else (value > 0)
            mapped = target
            logger.debug("CC=%d inject=%s value=%d -> target=%s", cc, spec.inject, value, target)
            _inject_value(arguments, spec.action.mcp, spec.inject, target)
        for key, reset_value in spec.resets.items():
            # при наличии `states` кнопка ставит энкодеру именно целевое значение из карты
            use = mapped if (mapped is not None and (key == spec.inject or spec.states is not None)) else reset_value
            self._reset_encoder(key, float(use))
        try:
            await self._call_action(spec.action, arguments)
        except McpConnectionError:
            raise
        except McpError as exc:
            logger.warning("кнопка CC=%d: %s", cc, exc)
        finally:
            await self._refresh_led(cc)

    def _reset_encoder(self, key: str, value: float) -> None:
        """Сбросить абсолютный энкодер к value: выставить счётчик на устройстве и значение.

        Значение ведём в приложении (по дельтам от точки сброса) — это даёт ровно `value`
        без скачка; счётчик на устройстве переставляем, чтобы был запас хода и LED-кольцо.
        """
        enc = self._encoders_by_key.get(key)
        if enc is None or enc.mode != "absolute" or enc.key is None:
            return
        raw = round(inverse_map(value, enc.min, enc.max, enc.scale))
        self._midi.set_encoder(enc.cc, raw)
        state = self._states[enc.key]
        state.value = value
        state.last_raw = raw
        logger.debug("энкодер CC=%d (%s) сброшен к %s (raw=%d)", enc.cc, key, value, raw)

    async def _handle_encoder(self, cc: int, raw: int) -> None:
        spec = self._mapping.encoders[cc]
        if spec.mode == "steps":
            delta = decode_relative(raw, self._scheme)
            if delta == 0:
                return
            if spec.buffer:
                self._enc_buffer[cc] = self._enc_buffer.get(cc, 0) + delta
                self._schedule_flush(cc)
                return
            await self._apply_steps(cc, delta)
            return

        # absolute: значение ведём по дельтам (первый сэмпл — из позиции, если не сброшено)
        delta = self._absolute_delta(spec, raw)
        if delta is None:  # первый сэмпл — синхронизируем значение
            await self._apply_absolute(cc)
            return
        if delta == 0:
            return
        if spec.buffer:
            self._enc_buffer[cc] = self._enc_buffer.get(cc, 0) + delta
            self._schedule_flush(cc)
            return
        self._advance_absolute(spec, delta)
        await self._apply_absolute(cc)

    def _absolute_delta(self, spec: EncoderSpec, raw: int) -> int | None:
        if spec.key is None:
            raise RuntimeError(f"CC {spec.cc}: absolute-энкодер без key")
        state = self._states[spec.key]
        if state.last_raw is None:
            state.last_raw = raw
            if state.value is None:
                state.value = map_absolute(raw, spec.min, spec.max, spec.scale)
            return None
        delta = raw - state.last_raw
        state.last_raw = raw
        return delta

    def _advance_absolute(self, spec: EncoderSpec, delta: int) -> None:
        if spec.key is None:
            raise RuntimeError(f"CC {spec.cc}: absolute-энкодер без key")
        state = self._states[spec.key]
        base = state.value if state.value is not None else spec.min
        if spec.scale == "log":
            new = base * (spec.max / spec.min) ** (delta / 127.0)
        else:
            new = base + delta * (spec.max - spec.min) / 127.0
        state.value = max(spec.min, min(spec.max, new))

    def _schedule_flush(self, cc: int) -> None:
        if cc in self._enc_tasks:
            return
        self._enc_tasks[cc] = asyncio.create_task(self._coalesced_encoder(cc))

    async def _coalesced_encoder(self, cc: int) -> None:
        await asyncio.sleep(COALESCE_SECONDS)
        self._enc_tasks.pop(cc, None)
        buffered = self._enc_buffer.pop(cc, None)
        if buffered is None:
            return
        spec = self._mapping.encoders[cc]
        if spec.mode == "steps":
            await self._apply_steps(cc, buffered)
            return
        self._advance_absolute(spec, buffered)
        await self._apply_absolute(cc)

    async def _apply_absolute(self, cc: int) -> None:
        spec = self._mapping.encoders[cc]
        if spec.command is None or spec.key is None:
            raise RuntimeError(f"CC {cc}: absolute-энкодер без command/key")
        state = self._states[spec.key]
        if state.value is None:
            return
        value = state.value
        logger.info("encoder CC=%d %s -> %.4f", cc, spec.key, value)
        arguments = _inject_value(copy.deepcopy(spec.command.args), spec.command.mcp, spec.param or spec.key, value)
        try:
            await self._call_action(spec.command, arguments)
        except McpConnectionError:
            raise
        except McpError as exc:
            logger.warning("encoder CC=%d: %s", cc, exc)

    async def _apply_steps(self, cc: int, deltas: int) -> None:
        if deltas == 0:
            return
        spec = self._mapping.encoders[cc]
        action = spec.incr if deltas > 0 else spec.decr
        if action is None:
            raise RuntimeError(f"CC {cc}: steps-энкодер без incr/decr")
        steps = abs(deltas)
        logger.info("encoder CC=%d шаговая команда x%d", cc, steps)
        for _ in range(steps):
            try:
                await self._call_action(action, copy.deepcopy(action.args))
            except McpConnectionError:
                raise
            except McpError as exc:
                logger.warning("encoder CC=%d: %s", cc, exc)
                break

    async def _call_action(self, action: Action, arguments: dict[str, Any]) -> Any:
        if action.mcp == "command_run":
            logger.debug("command_run %s %s", arguments["id"], arguments.get("params"))
            return await self._client.command_run(arguments["id"], arguments.get("params"))
        if action.mcp == "ui_set":
            logger.debug("ui_set %s", arguments["fields"])
            return await self._client.ui_set(arguments["fields"])
        if action.mcp == "ui_inspect":
            return await self._client.ui_inspect()
        raise ValueError(f"неизвестный mcp: {action.mcp}")

    async def _refresh_led(self, cc: int) -> None:
        """Обновить LED для кнопки cc сразу после нажатия (свежий ui_inspect, без quiet)."""
        spec = self._mapping.buttons[cc]
        if spec.led is False:
            return
        try:
            ui = await self._client.ui_inspect()
        except McpConnectionError:
            raise
        except McpError as exc:
            logger.warning("LED refresh: %s", exc)
            return
        if isinstance(ui, dict):
            self._mirror = ui
        if spec.led == LED_BRUSH_TRANSFER_OPACITY:
            try:
                brush_raw = await self._client.command_run("brush.get")
            except McpConnectionError:
                raise
            except McpError as exc:
                logger.warning("LED refresh: brush.get: %s", exc)
            else:
                if isinstance(brush_raw, dict):
                    self._led_brush = brush_raw
        if isinstance(spec.led, str) and spec.led.startswith("tool:"):
            self._apply_tool_group(ui, force=True)
        else:
            self._apply_led(cc, ui, force=True)

    async def _sync_once(self) -> None:
        """Один опрос ui_inspect и зеркалирование состояния в X-Touch."""
        try:
            ui = await self._client.ui_inspect()
        except McpConnectionError:
            raise
        except McpError as exc:
            logger.warning("синк: ui_inspect не удался: %s", exc)
            return
        if not isinstance(ui, dict):
            return
        brush_full: dict[str, Any] | None = None
        if any(spec.led == LED_BRUSH_TRANSFER_OPACITY for spec in self._mapping.buttons.values()):
            try:
                brush_raw = await self._client.command_run("brush.get")
            except McpConnectionError:
                raise
            except McpError as exc:
                logger.warning("синк: brush.get не удался: %s", exc)
            else:
                brush_full = brush_raw if isinstance(brush_raw, dict) else None
        self._led_brush = brush_full
        self._mirror = ui
        self._log_state(ui, brush_full)
        self._sync_encoders(ui)
        self._apply_tool_group(ui)
        for cc in self._mapping.buttons:
            self._apply_led(cc, ui)

    def _log_state(self, ui: dict[str, Any], brush_full: dict[str, Any] | None = None) -> None:
        """Залогировать состояние, влияющее на X-Touch: полное при первом чтении, далее — изменения."""
        snapshot = self._snapshot(ui, brush_full)
        if self._last_snapshot is None:
            logger.info("первое состояние: %s", snapshot)
        else:
            changes = {k: v for k, v in snapshot.items() if self._last_snapshot.get(k) != v}
            if changes:
                logger.info("состояние изменилось: %s", changes)
        self._last_snapshot = snapshot

    def _snapshot(self, ui: dict[str, Any], brush_full: dict[str, Any] | None = None) -> dict[str, Any]:
        views_raw = ui.get("views")
        views = views_raw if isinstance(views_raw, list) else []
        view_raw = views[0] if views else {}
        view: dict[str, Any] = view_raw if isinstance(view_raw, dict) else {}
        brush_raw = ui.get("brush")
        brush: dict[str, Any] = brush_raw if isinstance(brush_raw, dict) else {}
        doc_raw = ui.get("document")
        doc: dict[str, Any] = doc_raw if isinstance(doc_raw, dict) else {}
        panels_raw = ui.get("panels")
        panels: dict[str, Any] = panels_raw if isinstance(panels_raw, dict) else {}

        def r(value: Any, nd: int) -> Any:
            return round(value, nd) if isinstance(value, int | float) else value

        return {
            "tool": ui.get("tool"),
            "rotation": r(view.get("rotation"), 2),
            "zoom": r(view.get("zoom"), 3),
            "hardness": r(brush.get("hardness"), 4),
            "opacity": r(brush.get("opacity"), 4),
            "size": r(brush.get("size"), 2),
            "canUndo": doc.get("canUndo"),
            "canRedo": doc.get("canRedo"),
            "layer_visible": self._active_layer_visible(ui),
            "transferOpacity": self._brush_opacity_from_pressure(brush_full),
            "panels": panels,
        }

    def _sync_encoders(self, ui: dict[str, Any]) -> None:
        """Подтянуть счётчики/значения энкодеров под реальное состояние app."""
        views_raw = ui.get("views")
        views = views_raw if isinstance(views_raw, list) else []
        view_raw = views[0] if views else {}
        view: dict[str, Any] = view_raw if isinstance(view_raw, dict) else {}
        brush_raw = ui.get("brush")
        brush: dict[str, Any] = brush_raw if isinstance(brush_raw, dict) else {}
        sources = {
            "rotation": view.get("rotation"),
            "zoom": view.get("zoom"),
            "hardness": brush.get("hardness"),
            "opacity": brush.get("opacity"),
            "layer_opacity": self._active_layer_prop(ui, "opacity"),
            "layer_fill": self._active_layer_prop(ui, "fill"),
        }
        for key, enc in self._encoders_by_key.items():
            if enc.mode != "absolute" or enc.key is None:
                continue
            value = sources.get(key)
            if value is None or self._is_quiet(enc.cc):
                continue
            target = float(value)
            state = self._states[key]
            if state.value is not None and _close(state.value, target, enc.min, enc.max):
                continue
            raw = round(inverse_map(target, enc.min, enc.max, enc.scale))
            self._midi.set_encoder(enc.cc, raw)
            state.value = target
            state.last_raw = raw
            logger.debug("синк: %s -> %s (raw=%d)", key, target, raw)

    def _apply_tool_group(self, ui: dict[str, Any], *, force: bool = False) -> None:
        active = ui.get("tool")
        if active is None:
            return
        active_norm = _norm_tool(active)
        for cc, tool in self._tool_group.items():
            if not force and self._is_quiet(cc):
                continue
            self._midi.set_led(cc, _norm_tool(tool) == active_norm)

    def _apply_led(self, cc: int, ui: dict[str, Any], *, force: bool = False) -> None:
        spec = self._mapping.buttons.get(cc)
        if spec is None or spec.led is False:
            return
        if not force and self._is_quiet(cc):
            return
        led = spec.led
        if led is True or led == "layer.visible":
            if spec.inject is not None:
                state = self._read_toggle(ui, spec.inject)
                if state is not None:
                    self._midi.set_led(cc, state)
        elif led in ("undo", "redo"):
            doc = ui.get("document")
            field = "canUndo" if led == "undo" else "canRedo"
            if isinstance(doc, dict):
                self._midi.set_led(cc, bool(doc.get(field)))
        elif led == LED_BRUSH_TRANSFER_OPACITY:
            state = self._brush_opacity_from_pressure(self._led_brush)
            if state is not None:
                self._midi.set_led(cc, state)
        # led вида "tool:*" обрабатывается _apply_tool_group

    @staticmethod
    def _brush_opacity_from_pressure(brush: object) -> bool | None:
        """Горит ли LED кнопки: opacity кисти управляется давлением (transfer.opacity.control)."""
        if not isinstance(brush, dict):
            return None
        transfer = brush.get("transfer")
        if not isinstance(transfer, dict):
            return None
        opacity = transfer.get("opacity")
        if not isinstance(opacity, dict) or "control" not in opacity:
            return None
        return opacity["control"] == "penPressure"

    def _read_toggle(self, ui: dict[str, Any], inject: str) -> bool | None:
        """Реальное состояние тоггла из ui_inspect, либо None, если источник недоступен."""
        if inject.startswith("panels."):
            panels = ui.get("panels")
            key = inject.split(".", 1)[1]
            if not isinstance(panels, dict) or key not in panels:
                return None
            return bool(panels[key])
        if inject == "visible":
            return self._active_layer_visible(ui)
        return None

    @staticmethod
    def _active_layer_visible(ui: dict[str, Any]) -> bool | None:
        doc = ui.get("document")
        if not isinstance(doc, dict):
            return None
        active = doc.get("activeLayer")
        for layer in doc.get("layers") or []:
            if isinstance(layer, dict) and layer.get("id") == active:
                return bool(layer.get("visible", True))
        return None

    @staticmethod
    def _active_layer_prop(ui: dict[str, Any], field: str) -> Any:
        """Значение поля активного слоя из ui_inspect (opacity/fill), либо None."""
        doc = ui.get("document")
        if not isinstance(doc, dict):
            return None
        active = doc.get("activeLayer")
        for layer in doc.get("layers") or []:
            if isinstance(layer, dict) and layer.get("id") == active:
                return layer.get(field)
        return None

    def _is_quiet(self, cc: int) -> bool:
        last = self._last_touch.get(cc)
        if last is None:
            return False
        return (asyncio.get_running_loop().time() - last) * 1000.0 < self._sync.quiet_ms


def _inject_value(args: dict[str, Any], mcp: str, path: str, value: Any) -> dict[str, Any]:
    """Записать значение по пути (поддерживает вложенность, напр. `panels.options_bar`)."""
    if mcp == "ui_set":
        root = args.setdefault("fields", {})
    elif mcp == "command_run":
        root = args.setdefault("params", {})
    else:
        raise ValueError(f"инъекция значения не поддерживается для mcp={mcp!r}")
    parts = path.split(".")
    node = root
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return args


def _norm_tool(name: object) -> str:
    """Нормализовать имя инструмента для сравнения ('Clone Stamp' == 'cloneStamp')."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def decode_relative(value: int, scheme: str) -> int:
    """Относительный энкодер: сырое значение CC -> знаковая дельта шага.

    X-Touch (relative2): 65 = +1, 63 = -1, 67/69/... = крупный +шаг,
    61/59/... = крупный -шаг. FAST_STEP — во сколько раз крупнее быстрый шаг.
    """
    if scheme == "relative2":
        if value == 65:
            return 1
        if value >= 67:
            return FAST_STEP
        if value == 63:
            return -1
        if value <= 61:
            return -FAST_STEP
        return 0
    if scheme == "relative1":
        if value == 1:
            return 1
        if value == 127:
            return -1
        return 0
    if scheme == "relative3":
        if value == 1:
            return 1
        if value == 65:
            return -1
        return 0
    return 0


def map_absolute(raw: int, min_v: float, max_v: float, scale: str) -> float:
    """Абсолютный энкодер: сырое 0..127 -> значение в [min, max] (linear|log)."""
    frac = max(0.0, min(1.0, raw / 127.0))
    if scale == "log":
        if min_v <= 0:
            raise ValueError("log-шкала требует min > 0")
        return min_v * (max_v / min_v) ** frac
    return min_v + frac * (max_v - min_v)


def inverse_map(value: float, min_v: float, max_v: float, scale: str) -> float:
    """Обратно map_absolute: значение -> сырое 0..127 (для выставления счётчика энкодера)."""
    v = max(min_v, min(max_v, value))
    if scale == "log":
        if min_v <= 0:
            raise ValueError("log-шкала требует min > 0")
        frac = math.log(v / min_v) / math.log(max_v / min_v)
    else:
        frac = (v - min_v) / (max_v - min_v) if max_v != min_v else 0.0
    return frac * 127.0


async def _run_async() -> None:
    load_dotenv()
    mapping = load_config(default_config_path())
    configure_logging(mapping.log)
    cli = discover_cli()
    if cli is None:
        raise RuntimeError("photocraft-cli не найден: задайте PHOTOCRAFT_CLI или запустите PhotoCraft AppImage")
    port = _require_env_int("PHOTOCRAFT_CONTROL_PORT")
    token_file = _require_env_path("PHOTOCRAFT_CONTROL_TOKEN_FILE")

    logger.info("config: %s; cli: %s; bridge: 127.0.0.1:%d; log: %s",
                default_config_path(), cli, port, mapping.log.path)

    events: asyncio.Queue[tuple[str, int, int]] = asyncio.Queue()
    midi = XTouchMidi(events, asyncio.get_running_loop())
    client = MCPClient(cli, port, token_file)
    app = BridgeApp(midi, client, mapping)
    await app.run(events)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} не задан (см. .env / .env.example)")
    return value


def _require_env_int(name: str) -> int:
    try:
        return int(_require_env(name))
    except ValueError as exc:
        raise RuntimeError(f"{name} должен быть целым числом") from exc


def _require_env_path(name: str) -> Path:
    return Path(_require_env(name)).expanduser()


def main() -> int:
    if "--monitor" in sys.argv[1:]:
        from .midi import run_monitor

        try:
            run_monitor()
        except KeyboardInterrupt:
            pass
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        return 0
    try:
        asyncio.run(_run_async())
    except KeyboardInterrupt:
        logger.info("остановлен по Ctrl+C")
        return 0
    except (RuntimeError, McpConnectionError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
