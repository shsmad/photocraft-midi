"""Тесты зеркалирования состояния PhotoCraft -> X-Touch (фейковый ui_inspect/midi)."""

from __future__ import annotations

from typing import Any

from photocraftmidi.main import BridgeApp
from photocraftmidi.mapping import Action, ButtonSpec, EncoderSpec, LogSpec, Mapping, SyncSpec

UI: dict[str, Any] = {
    "tool": "Smudge",
    "views": [{"rotation": 10.0, "zoom": 2.0}],
    "brush": {"hardness": 0.5, "opacity": 0.25},
    "panels": {"options_bar": False, "brush_settings": True},
    "document": {
        "activeLayer": 2,
        "canUndo": True,
        "canRedo": False,
        "layers": [{"id": 2, "visible": False}],
    },
}


def _btn(cc: int, led: Any = False, inject: str | None = None) -> ButtonSpec:
    action = Action("ui_set", {"fields": {}}) if inject else Action("command_run", {"id": "edit.undo", "params": {}})
    return ButtonSpec(cc=cc, kind="toggle", action=action, led=led, resets={}, inject=inject)


def _enc(cc: int, key: str, min_v: float, max_v: float, scale: str = "linear") -> EncoderSpec:
    return EncoderSpec(
        cc=cc,
        mode="absolute",
        command=Action("command_run", {"id": "tools.setBrush", "params": {}}),
        key=key,
        min=min_v,
        max=max_v,
        scale=scale,
    )


def _app(fake_midi: Any, fake_client: Any) -> BridgeApp:
    mapping = Mapping(
        buttons={
            16: _btn(16, led=True, inject="panels.options_bar"),
            17: _btn(17, led="undo"),
            18: _btn(18, led="redo"),
            19: _btn(19, led=True, inject="panels.brush_settings"),
            21: _btn(21, led="tool:brush"),
            22: _btn(22, led="tool:smudge"),
            23: _btn(23, led="layer.visible", inject="visible"),
            24: _btn(24, led=True, inject="panels.brush_settings"),
        },
        encoders={
            11: _enc(11, "rotation", -180, 180),
            12: _enc(12, "hardness", 0, 1),
            13: _enc(13, "opacity", 0, 1),
            14: _enc(14, "zoom", 0.1, 16, "log"),
        },
        encoder_scheme="relative2",
        sync=SyncSpec(),
        log=LogSpec(),
    )
    return BridgeApp(fake_midi, fake_client, mapping)


def test_sync_encoders(fake_midi: Any, fake_client: Any) -> None:
    app = _app(fake_midi, fake_client)
    app._sync_encoders(UI)
    ccs = {cc for cc, _ in fake_midi.encoder}
    assert ccs == {11, 12, 13, 14}
    assert (12, 64) in fake_midi.encoder  # hardness 0.5 -> 64
    assert (13, 32) in fake_midi.encoder  # opacity 0.25 -> 32


def test_sync_layer_opacity_encoder(fake_midi: Any, fake_client: Any) -> None:
    mapping = Mapping(
        buttons={},
        encoders={10: _enc(10, "layer_opacity", 0, 1)},
        encoder_scheme="relative2",
        sync=SyncSpec(),
        log=LogSpec(),
    )
    app = BridgeApp(fake_midi, fake_client, mapping)
    ui = {"document": {"activeLayer": 2, "layers": [{"id": 2, "opacity": 0.5, "fill": 0.8}]}}
    app._sync_encoders(ui)
    assert (10, 64) in fake_midi.encoder  # opacity 0.5 -> raw 64


def test_sync_leds(fake_midi: Any, fake_client: Any) -> None:
    app = _app(fake_midi, fake_client)
    app._apply_tool_group(UI)
    for cc in app._mapping.buttons:
        app._apply_led(cc, UI)
    on = {cc for cc, state in fake_midi.led if state}
    off = {cc for cc, state in fake_midi.led if not state}
    assert on == {17, 19, 22, 24}  # undo, brush_settings (19/24), smudge
    assert {16, 18, 21, 23} <= off


def test_sync_skips_document_when_absent(fake_midi: Any, fake_client: Any) -> None:
    app = _app(fake_midi, fake_client)
    ui = {"tool": "Brush", "panels": {}, "document": None}
    app._apply_led(23, ui)  # слой нет документа -> LED не трогаем
    assert fake_midi.led == []


def _app_with_brush_led(fake_midi: Any, fake_client: Any) -> BridgeApp:
    mapping = Mapping(
        buttons={
            27: ButtonSpec(
                cc=27,
                kind="toggle",
                action=Action("command_run", {"id": "tools.setBrush", "params": {}}),
                led="brush.transfer.opacity",
                resets={},
                inject="transfer.opacity.control",
            ),
        },
        encoders={},
        encoder_scheme="relative2",
        sync=SyncSpec(),
        log=LogSpec(),
    )
    return BridgeApp(fake_midi, fake_client, mapping)


async def test_sync_once_led_brush_transfer_opacity(fake_midi: Any, fake_client: Any) -> None:
    fake_client._brush = {"transfer": {"opacity": {"control": "penPressure"}}}
    app = _app_with_brush_led(fake_midi, fake_client)
    await app._sync_once()
    assert (27, True) in fake_midi.led
    fake_client._brush = {"transfer": {"opacity": {"control": "off"}}}
    fake_midi.led.clear()
    await app._sync_once()
    assert (27, False) in fake_midi.led


async def test_refresh_led_brush_transfer_opacity(fake_midi: Any, fake_client: Any) -> None:
    fake_client._brush = {"transfer": {"opacity": {"control": "penPressure"}}}
    app = _app_with_brush_led(fake_midi, fake_client)
    await app._handle_button(27, 0)
    assert (27, True) in fake_midi.led  # LED по реальному состоянию, а не по нажатию
