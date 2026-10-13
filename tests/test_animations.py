"""Тесты LED-анимаций (фейковый set_led)."""

from __future__ import annotations

import asyncio
import contextlib

from photocraftmidi import animations


class _Led:
    def __init__(self) -> None:
        self.events: list[tuple[int, bool]] = []

    def set_led(self, cc: int, on: bool) -> None:
        self.events.append((cc, on))


async def test_blink_all_turns_on_then_off() -> None:
    led = _Led()
    await animations.blink_all(led, ccs=(16, 17), times=1, on_s=0, off_s=0)
    assert led.events == [(16, True), (17, True), (16, False), (17, False)]


async def test_snake_direction_top_then_bottom() -> None:
    led = _Led()
    await animations.snake(led, top=(16, 17, 18), bottom=(20, 19), delay=0)
    # предыдущий LED гасится при загорании следующего
    assert led.events.index((16, True)) < led.events.index((17, True))
    assert led.events.index((16, False)) > led.events.index((17, True))
    # в конце всё погашено
    assert led.events[-1] == (19, False)
    assert (20, True) in led.events


async def test_bounce_pingpong_and_cancel() -> None:
    led = _Led()
    task = asyncio.create_task(animations.bounce(led, ccs=(16, 17, 18), delay=0.001))
    await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert (16, True) in led.events
    assert (18, True) in led.events  # докатились до края (туда-обратно)
    assert len(led.events) > 5
