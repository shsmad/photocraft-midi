"""Загрузка и валидация `config.toml`: карта CC -> действия PhotoCraft."""

from __future__ import annotations

import os
import tomllib

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"

_ALLOWED_MCP = frozenset({"command_run", "ui_set", "ui_inspect", "command_list"})
_ALLOWED_KIND = frozenset({"momentary", "toggle"})
_ALLOWED_SCHEMES = frozenset({"relative1", "relative2", "relative3"})
_ENCODER_CC_MIN = 0
_ENCODER_CC_MAX = 127

ActionKind = Literal["momentary", "toggle"]


@dataclass(frozen=True)
class Action:
    """Один вызов MCP-инструмента: имя инструмента и его аргументы."""

    mcp: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ButtonSpec:
    cc: int
    kind: ActionKind
    action: Action
    led: bool | str
    resets: dict[str, float]
    inject: str | None
    states: dict[int, Any] | None = None


@dataclass(frozen=True)
class EncoderSpec:
    cc: int
    mode: Literal["steps", "absolute"]
    buffer: bool = False
    incr: Action | None = None
    decr: Action | None = None
    command: Action | None = None
    key: str | None = None
    param: str | None = None  # имя параметра команды (по умолчанию = key)
    min: float = 0.0
    max: float = 1.0
    scale: str = "linear"


@dataclass(frozen=True)
class SyncSpec:
    enabled: bool = True
    interval_ms: int = 1000
    quiet_ms: int = 500


@dataclass(frozen=True)
class LogSpec:
    path: str = "photocraft-midi.log"  # относительно текущего каталога
    level: str = "INFO"
    max_bytes: int = 1_000_000
    backups: int = 3


@dataclass(frozen=True)
class Mapping:
    buttons: dict[int, ButtonSpec]
    encoders: dict[int, EncoderSpec]
    encoder_scheme: str
    sync: SyncSpec
    log: LogSpec


def default_config_path() -> Path:
    """Путь к config.toml по умолчанию либо из `PHOTOCRAFT_MIDI_CONFIG`."""
    override = os.environ.get("PHOTOCRAFT_MIDI_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH


def _parse_action(raw: dict[str, Any]) -> Action:
    mcp = raw.get("mcp")
    if not isinstance(mcp, str) or mcp not in _ALLOWED_MCP:
        raise ValueError(f"запись action: неизвестный mcp={mcp!r}, ждём один из {sorted(_ALLOWED_MCP)}")
    args = raw.get("args")
    if not isinstance(args, dict):
        raise ValueError(f"запись action ({mcp}): `args` должен быть объектом")
    return Action(mcp=mcp, args=args)


def _parse_buttons(cc: int, raw: dict[str, Any]) -> ButtonSpec:
    kind = raw.get("kind")
    if kind not in _ALLOWED_KIND:
        raise ValueError(f"CC {cc}: неизвестный kind={kind!r}, ждём {'|'.join(sorted(_ALLOWED_KIND))}")
    led = raw.get("led", False)
    if not isinstance(led, bool) and not isinstance(led, str):
        raise ValueError(f"CC {cc}: `led` должен быть bool или строкой")
    resets = raw.get("resets", {})
    if not isinstance(resets, dict) or not all(isinstance(k, str) for k in resets):
        raise ValueError(f"CC {cc}: `resets` должен быть таблицей {{key = value}}")
    inject = raw.get("inject")
    if inject is not None and not isinstance(inject, str):
        raise ValueError(f"CC {cc}: `inject` должен быть строкой")
    states_raw = raw.get("states")
    states: dict[int, Any] | None = None
    if states_raw is not None:
        if not isinstance(states_raw, dict):
            raise ValueError(f"CC {cc}: `states` должен быть таблицей {{CC-значение = целевое}}")
        try:
            states = {int(k): v for k, v in states_raw.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CC {cc}: ключи `states` должны быть числами") from exc
    return ButtonSpec(
        cc=cc,
        kind=kind,
        action=_parse_action(raw),
        led=led,
        resets={k: float(v) for k, v in resets.items()},
        inject=inject,
        states=states,
    )


def _parse_encoder(cc: int, raw: dict[str, Any]) -> EncoderSpec:
    buffer = bool(raw.get("buffer", False))

    if "incr" in raw and "decr" in raw:
        return EncoderSpec(
            cc=cc,
            mode="steps",
            buffer=buffer,
            incr=_parse_action(raw["incr"]),
            decr=_parse_action(raw["decr"]),
        )

    if "command" in raw:
        command = _parse_action(raw["command"])
        key = raw.get("key")
        if not isinstance(key, str):
            raise ValueError(f"CC {cc}: для `command` нужен `key` (внутреннее имя значения)")
        param = raw.get("param", key)
        if not isinstance(param, str):
            raise ValueError(f"CC {cc}: `param` должен быть строкой")
        try:
            min_v, max_v = float(raw.get("min", 0.0)), float(raw.get("max", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CC {cc}: min/max должны быть числами") from exc
        scale = raw.get("scale", "linear")
        if scale not in ("linear", "log"):
            raise ValueError(f"CC {cc}: scale={scale!r}, ждём linear|log")
        return EncoderSpec(
            cc=cc, mode="absolute", buffer=buffer, command=command, key=key, param=param,
            min=min_v, max=max_v, scale=scale,
        )

    raise ValueError(f"CC {cc}: encoder требует `incr`+`decr`, либо `command`+`key`")


def load_config(path: str | Path | None = None) -> Mapping:
    """Прочитать и провалидировать карту из TOML."""
    chosen = Path(path) if path else default_config_path()
    if not chosen.exists():
        raise FileNotFoundError(f"config.toml не найден: {chosen}")

    with chosen.open("rb") as handle:
        data = tomllib.load(handle)

    buttons: dict[int, ButtonSpec] = {}
    encoders: dict[int, EncoderSpec] = {}

    xt = data.get("xtouch", {})
    scheme = xt.get("encoder_scheme", "relative2")
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"[xtouch] encoder_scheme={scheme!r}: ждём {'|'.join(sorted(_ALLOWED_SCHEMES))}")

    layer_a = data.get("layer_a", {})
    raw_buttons = layer_a.get("button", {})
    if not isinstance(raw_buttons, dict):
        raise ValueError("[layer_a.button] должен быть таблицей")
    for key, entry in raw_buttons.items():
        try:
            cc = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"[layer_a.button] ключ {key!r} не число") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"CC {cc}: запись должна быть объектом")
        buttons[cc] = _parse_buttons(cc, entry)

    raw_encoders = layer_a.get("encoder", {})
    if not isinstance(raw_encoders, dict):
        raise ValueError("[layer_a.encoder] должен быть таблицей")
    for key, entry in raw_encoders.items():
        try:
            cc = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"[layer_a.encoder] ключ {key!r} не число") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"CC {cc}: запись должна быть объектом")
        encoders[cc] = _parse_encoder(cc, entry)

    if not buttons and not encoders:
        raise ValueError("карта пуста: задайте [layer_a.button]/[layer_a.encoder]")

    sync = _parse_sync(data.get("sync", {}))
    log = _parse_log(data.get("log", {}))
    return Mapping(buttons=buttons, encoders=encoders, encoder_scheme=scheme, sync=sync, log=log)


def _parse_sync(raw: Any) -> SyncSpec:
    if not isinstance(raw, dict):
        raise ValueError("[sync] должен быть таблицей")
    try:
        interval_ms = int(raw.get("interval_ms", 1000))
        quiet_ms = int(raw.get("quiet_ms", 500))
    except (TypeError, ValueError) as exc:
        raise ValueError("[sync] interval_ms/quiet_ms должны быть целыми") from exc
    if interval_ms <= 0 or quiet_ms < 0:
        raise ValueError("[sync] interval_ms > 0, quiet_ms >= 0")
    return SyncSpec(
        enabled=bool(raw.get("enabled", True)),
        interval_ms=interval_ms,
        quiet_ms=quiet_ms,
    )


def _parse_log(raw: Any) -> LogSpec:
    if not isinstance(raw, dict):
        raise ValueError("[log] должен быть таблицей")
    path = raw.get("path", "photocraft-midi.log")
    if not isinstance(path, str) or not path:
        raise ValueError("[log] path должен быть непустой строкой")
    try:
        max_bytes = int(raw.get("max_bytes", 1_000_000))
        backups = int(raw.get("backups", 3))
    except (TypeError, ValueError) as exc:
        raise ValueError("[log] max_bytes/backups должны быть целыми") from exc
    if max_bytes <= 0 or backups < 0:
        raise ValueError("[log] max_bytes > 0, backups >= 0")
    return LogSpec(
        path=path,
        level=str(raw.get("level", "INFO")).upper(),
        max_bytes=max_bytes,
        backups=backups,
    )
