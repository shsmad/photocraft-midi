"""Общие фикстуры и подстановка `src` в sys.path (пакет не устанавливается, distribution=false)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from photocraftmidi.mapping import Action, ButtonSpec, EncoderSpec, LogSpec, Mapping, SyncSpec  # noqa: E402


class FakeMidi:
    """Заглушка XTouchMidi: пишет LED/энкодеры, умеет открываться/закрываться."""

    def __init__(self) -> None:
        self.led: list[tuple[int, bool]] = []
        self.encoder: list[tuple[int, int]] = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def is_open(self) -> bool:
        return self.opened

    def close(self) -> None:
        self.opened = False

    def set_led(self, cc: int, on: bool) -> None:
        self.led.append((cc, on))

    def set_encoder(self, cc: int, raw: int) -> None:
        self.encoder.append((cc, raw))


class FakeClient:
    """Заглушка MCPClient: записывает вызовы, отдаёт заданный ui_inspect."""

    def __init__(self, ui: dict | None = None, brush: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self._ui = ui or {}
        self._brush = brush

    @property
    def connected(self) -> bool:
        return True

    async def ui_set(self, fields: dict) -> None:
        self.calls.append(("ui_set", fields))

    async def command_run(self, command_id: str, params: dict | None = None) -> dict | None:
        self.calls.append(("command_run", command_id, params))
        if command_id == "brush.get":
            return self._brush
        return None

    async def command_batch(self, steps: list[dict]) -> dict:
        self.calls.append(("command_batch", steps))
        return {"completed": len(steps), "failed": 0, "results": []}

    async def ui_inspect(self) -> dict:
        return self._ui

    async def command_list(self, filter_text: str | None = None) -> list:
        return []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _make_button(cc: int, **kwargs: object) -> ButtonSpec:
    fields = {
        "kind": "momentary",
        "action": Action("command_run", {}),
        "led": False,
        "resets": {},
        "inject": None,
    }
    fields.update(kwargs)
    return ButtonSpec(cc=cc, **fields)  # type: ignore  # kwargs-сборка полей


def _make_encoder(cc: int, **kwargs: object) -> EncoderSpec:
    fields: dict = {"mode": "absolute"}
    fields.update(kwargs)
    return EncoderSpec(cc=cc, **fields)  # type: ignore[arg-type]


def _make_mapping(
    buttons: dict[int, ButtonSpec] | None = None,
    encoders: dict[int, EncoderSpec] | None = None,
) -> Mapping:
    return Mapping(
        buttons=buttons or {},
        encoders=encoders or {},
        encoder_scheme="relative2",
        sync=SyncSpec(),
        log=LogSpec(),
    )


@pytest.fixture
def fake_midi() -> FakeMidi:
    return FakeMidi()


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def make_button():
    return _make_button


@pytest.fixture
def make_encoder():
    return _make_encoder


@pytest.fixture
def make_mapping():
    return _make_mapping
