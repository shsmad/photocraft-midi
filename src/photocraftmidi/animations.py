"""LED-анимации X-Touch Mini.

Принимают любой объект с методом `set_led(cc, on)` (см. `XTouchMidi`); про MCP не знают.
"""

from __future__ import annotations

import asyncio

from typing import Protocol

BUTTON_CCS: tuple[int, ...] = tuple(range(16, 32))  # кнопки строк 1-2: 16..31
TOP_ROW: tuple[int, ...] = tuple(range(16, 24))  # 16..23
BOTTOM_ROW: tuple[int, ...] = tuple(range(24, 32))  # 24..31


class LedSink(Protocol):
    def set_led(self, cc: int, on: bool) -> None: ...


async def blink_all(
    led: LedSink,
    ccs: tuple[int, ...] = BUTTON_CCS,
    *,
    times: int = 2,
    on_s: float = 0.12,
    off_s: float = 0.12,
) -> None:
    """Мигнуть всеми указанными LED `times` раз."""
    for _ in range(times):
        for cc in ccs:
            led.set_led(cc, True)
        await asyncio.sleep(on_s)
        for cc in ccs:
            led.set_led(cc, False)
        await asyncio.sleep(off_s)


async def snake(
    led: LedSink,
    *,
    top: tuple[int, ...] = TOP_ROW,
    bottom: tuple[int, ...] = BOTTOM_ROW,
    delay: float = 0.06,
) -> None:
    """Змейка: вправо по верхнему ряду (16→23), затем влево по нижнему (31→24)."""
    for row in (top, bottom):
        previous: int | None = None
        for cc in row:
            led.set_led(cc, True)
            if previous is not None:
                led.set_led(previous, False)
            previous = cc
            await asyncio.sleep(delay)
        if previous is not None:
            led.set_led(previous, False)


async def bounce(
    led: LedSink,
    ccs: tuple[int, ...] = TOP_ROW,
    *,
    delay: float = 0.08,
) -> None:
    """Бегущая точка «туда-обратно» по ряду; крутится до отмены задачи."""
    row = list(ccs) or [TOP_ROW[0]]
    ping = row + row[-2:0:-1] if len(row) >= 2 else row
    previous: int | None = None
    while True:
        for cc in ping:
            led.set_led(cc, True)
            if previous is not None:
                led.set_led(previous, False)
            previous = cc
            await asyncio.sleep(delay)
