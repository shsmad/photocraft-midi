"""Тесты разбора и валидации config.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from photocraftmidi.mapping import load_config

MINIMAL = """
[xtouch]
encoder_scheme = "relative2"

[layer_a.button]
17 = { kind = "momentary", mcp = "command_run", args = { id = "edit.undo", params = {} }, led = "undo" }

[layer_a.encoder]
11 = { command = { mcp = "ui_set", args = { fields = { rotation = 0 } } }, key = "rotation", min = -180, max = 180 }

[sync]
enabled = true
interval_ms = 1000
quiet_ms = 500

[log]
path = "test.log"
level = "INFO"
max_bytes = 1000
backups = 2
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_minimal(tmp_path: Path) -> None:
    mapping = load_config(_write(tmp_path, MINIMAL))
    assert set(mapping.buttons) == {17}
    assert mapping.buttons[17].kind == "momentary"
    assert mapping.buttons[17].led == "undo"
    assert mapping.encoders[11].mode == "absolute"
    assert mapping.encoders[11].key == "rotation"
    assert mapping.encoder_scheme == "relative2"
    assert mapping.sync.enabled is True
    assert mapping.log.path == "test.log"
    assert mapping.log.backups == 2


def test_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.toml")


@pytest.mark.parametrize(
    "bad",
    [
        MINIMAL.replace('"momentary"', '"bogus"'),  # неизвестный kind
        MINIMAL.replace('mcp = "command_run"', 'mcp = "nope"'),  # неизвестный mcp
        MINIMAL.replace('min = -180', 'min = "x"'),  # min не число
        MINIMAL.replace('encoder_scheme = "relative2"', 'encoder_scheme = "nope"'),
        MINIMAL.replace("interval_ms = 1000", "interval_ms = 0"),
        MINIMAL.replace('path = "test.log"', "path = 5"),
    ],
)
def test_invalid_configs(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, bad))


def test_encoder_requires_key(tmp_path: Path) -> None:
    bad = MINIMAL.replace(', key = "rotation"', "")
    with pytest.raises(ValueError, match="key"):
        load_config(_write(tmp_path, bad))


def test_empty_mapping(tmp_path: Path) -> None:
    empty = '[xtouch]\nencoder_scheme = "relative2"\n'
    with pytest.raises(ValueError, match="пуста"):
        load_config(_write(tmp_path, empty))
