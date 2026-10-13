"""Тесты обработки кнопок и сброса энкодеров (без железа и PhotoCraft)."""

from __future__ import annotations

from typing import Any, Literal

from photocraftmidi.main import BridgeApp
from photocraftmidi.mapping import Action, ButtonSpec, EncoderSpec, LogSpec, Mapping, SyncSpec


def _btn(cc: int, **kwargs: Any) -> ButtonSpec:
    fields: dict[str, Any] = {
        "kind": "momentary",
        "action": Action("command_run", {}),
        "led": False,
        "resets": {},
        "inject": None,
    }
    fields.update(kwargs)
    kind: Literal["momentary", "toggle"] = fields.pop("kind")
    return ButtonSpec(cc=cc, kind=kind, **fields)


def _mapping(buttons: dict[int, ButtonSpec], encoders: dict[int, EncoderSpec] | None = None) -> Mapping:
    return Mapping(
        buttons=buttons,
        encoders=encoders or {},
        encoder_scheme="relative2",
        sync=SyncSpec(),
        log=LogSpec(),
    )


def _app(fake_midi: Any, fake_client: Any, mapping: Mapping) -> BridgeApp:
    return BridgeApp(fake_midi, fake_client, mapping)


async def test_toggle_layer_visible(fake_midi: Any, fake_client: Any) -> None:
    mapping = _mapping(
        {
            23: _btn(
                23,
                kind="toggle",
                action=Action("command_run", {"id": "layer.setProps", "params": {}}),
                led="layer.visible",
                inject="visible",
            ),
        },
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(23, 127)
    assert ("command_run", "layer.setProps", {"visible": True}) in fake_client.calls
    fake_client.calls.clear()
    await app._handle_button(23, 0)
    assert ("command_run", "layer.setProps", {"visible": False}) in fake_client.calls


async def test_toggle_panel(fake_midi: Any, fake_client: Any) -> None:
    mapping = _mapping(
        {
            16: _btn(
                16,
                kind="toggle",
                action=Action("ui_set", {"fields": {"panels": {"options_bar": True}}}),
                led=True,
                inject="panels.options_bar",
            ),
        },
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(16, 0)
    assert ("ui_set", {"panels": {"options_bar": False}}) in fake_client.calls
    fake_client.calls.clear()
    await app._handle_button(16, 127)
    assert ("ui_set", {"panels": {"options_bar": True}}) in fake_client.calls


async def test_momentary_fires_on_both_values(fake_midi: Any, fake_client: Any) -> None:
    mapping = _mapping(
        {31: _btn(31, kind="momentary", action=Action("command_run", {"id": "tools.swapColors", "params": {}}))},
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(31, 0)
    await app._handle_button(31, 127)
    assert fake_client.calls.count(("command_run", "tools.swapColors", {})) == 2


async def test_button_resets_encoder_counter(fake_midi: Any, fake_client: Any) -> None:
    enc = EncoderSpec(
        cc=11,
        mode="absolute",
        command=Action("ui_set", {"fields": {"rotation": 0}}),
        key="rotation",
        min=-180.0,
        max=180.0,
    )
    mapping = _mapping(
        {
            3: _btn(
                3,
                kind="momentary",
                action=Action("ui_set", {"fields": {"rotation": 0}}),
                resets={"rotation": 0},
            ),
        },
        encoders={11: enc},
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(3, 127)
    assert fake_midi.encoder == [(11, 64)]  # 0° -> raw 64


async def test_absolute_encoder_tracks_deltas(fake_midi: Any, fake_client: Any) -> None:
    enc = EncoderSpec(
        cc=11,
        mode="absolute",
        command=Action("ui_set", {"fields": {"rotation": 0}}),
        key="rotation",
        min=-180.0,
        max=180.0,
    )
    mapping = _mapping({}, encoders={11: enc})
    app = _app(fake_midi, fake_client, mapping)
    # первый сэмпл: значение из позиции (raw 64 -> 1.42°)
    await app._handle_encoder(11, 64)
    first = fake_client.calls[-1]
    assert first[0] == "ui_set"
    assert round(first[1]["rotation"], 2) == 1.42
    # дельта +1: от 1.42° ровно на один шаг
    await app._handle_encoder(11, 65)
    second = fake_client.calls[-1]
    assert round(second[1]["rotation"] - first[1]["rotation"], 2) == round(360 / 127, 2)


async def test_toggle_hardness_states(fake_midi: Any, fake_client: Any) -> None:
    enc = EncoderSpec(
        cc=12,
        mode="absolute",
        command=Action("command_run", {"id": "tools.setBrush", "params": {}}),
        key="hardness",
        min=0.0,
        max=1.0,
    )
    mapping = _mapping(
        {
            4: _btn(
                4,
                kind="toggle",
                action=Action("command_run", {"id": "tools.setBrush", "params": {}}),
                states={127: 1.0, 0: 0.25},
                inject="hardness",
                resets={"hardness": 1.0},
            ),
        },
        encoders={12: enc},
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(4, 127)
    assert ("command_run", "tools.setBrush", {"hardness": 1.0}) in fake_client.calls
    assert (12, 127) in fake_midi.encoder  # 100% -> raw 127
    fake_client.calls.clear()
    fake_midi.encoder.clear()
    await app._handle_button(4, 0)
    assert ("command_run", "tools.setBrush", {"hardness": 0.25}) in fake_client.calls
    assert (12, 32) in fake_midi.encoder  # 25% -> raw 32 (как в эталоне)


async def test_toggle_transfer_opacity_states(fake_midi: Any, fake_client: Any) -> None:
    mapping = _mapping(
        {
            27: _btn(
                27,
                kind="toggle",
                action=Action("command_run", {"id": "tools.setBrush", "params": {"reset": False}}),
                states={127: "penPressure", 0: "off"},
                inject="transfer.opacity.control",
            ),
        },
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(27, 127)
    assert (
        "command_run",
        "tools.setBrush",
        {"reset": False, "transfer": {"opacity": {"control": "penPressure"}}},
    ) in fake_client.calls
    fake_client.calls.clear()
    await app._handle_button(27, 0)
    assert (
        "command_run",
        "tools.setBrush",
        {"reset": False, "transfer": {"opacity": {"control": "off"}}},
    ) in fake_client.calls


async def test_button_layer_opacity_states(fake_midi: Any, fake_client: Any) -> None:
    enc = EncoderSpec(
        cc=10,
        mode="absolute",
        command=Action("command_run", {"id": "layer.setProps", "params": {"opacity": 1.0}}),
        key="layer_opacity",
        min=0.0,
        max=1.0,
    )
    mapping = _mapping(
        {
            2: _btn(
                2,
                kind="toggle",
                action=Action("command_run", {"id": "layer.setProps", "params": {"opacity": 1.0}}),
                states={127: 1.0, 0: 0.25},
                inject="opacity",
                resets={"layer_opacity": 1.0},
            ),
        },
        encoders={10: enc},
    )
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_button(2, 0)
    assert ("command_run", "layer.setProps", {"opacity": 0.25}) in fake_client.calls
    assert (10, 32) in fake_midi.encoder  # 25% -> raw 32
    fake_client.calls.clear()
    fake_midi.encoder.clear()
    await app._handle_button(2, 127)
    assert ("command_run", "layer.setProps", {"opacity": 1.0}) in fake_client.calls
    assert (10, 127) in fake_midi.encoder  # 100% -> raw 127


async def test_absolute_encoder_injects_param_not_key(fake_midi: Any, fake_client: Any) -> None:
    enc = EncoderSpec(
        cc=10,
        mode="absolute",
        command=Action("command_run", {"id": "layer.setProps", "params": {"opacity": 1.0}}),
        key="layer_opacity",
        param="opacity",
        min=0.0,
        max=1.0,
    )
    mapping = _mapping({}, encoders={10: enc})
    app = _app(fake_midi, fake_client, mapping)
    await app._handle_encoder(10, 127)  # инициализация позиции -> opacity 1.0
    assert ("command_run", "layer.setProps", {"opacity": 1.0}) in fake_client.calls
    fake_client.calls.clear()
    await app._handle_encoder(10, 95)  # -32 -> ~0.748
    calls = [c for c in fake_client.calls if c[1] == "layer.setProps"]
    assert calls
    assert set(calls[-1][2].keys()) == {"opacity"}  # внутренний key не утекает в команду
    assert abs(calls[-1][2]["opacity"] - 0.748) < 0.01
