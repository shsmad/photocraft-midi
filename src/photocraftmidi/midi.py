"""Rtmidi-обвязка X-Touch Mini: поиск портов, приём CC, LED.

Слой «тупой»: форвардит сырые CC (номер, значение) в очередь; разбор
(абсолютный/относительный энкодер, кнопка) делает main по конфигу.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time

import rtmidi

logger = logging.getLogger("photocraftmidi.midi")

PORT_SUBSTR = "X-TOUCH MINI"
# Control Change на MIDI-канале 11 (1-based): 0xB0 + (11-1) = 0xBA = 186.
CC_STATUS_CH11 = 0xBA


def find_port_by_name(ports: list[str], name: str) -> int:
    """Индекс первого порта, чьё имя содержит подстроку; -1 если нет."""
    return next((idx for idx, port in enumerate(ports) if name in port), -1)


def run_monitor(client_name: str = "photocraft-midi-monitor") -> None:
    """Диагностика: печатать все входящие MIDI-сообщения как есть (без MCP)."""
    midiin = rtmidi.MidiIn()  # type: ignore  # нет стабов у python-rtmidi
    idx = find_port_by_name(midiin.get_ports(), PORT_SUBSTR)
    if idx == -1:
        raise RuntimeError(f"MIDI-вход '{PORT_SUBSTR}' не найден: {midiin.get_ports()}")
    midiin.open_port(idx, client_name)

    def _callback(event: tuple[list[int], float], _data: object = None) -> None:
        message, _delta = event
        sys.stdout.write(f"raw {message}\n")
        sys.stdout.flush()

    midiin.set_callback(_callback)
    sys.stdout.write("monitor: крутите контролы (Ctrl+C для выхода)\n")
    sys.stdout.flush()
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            midiin.close_port()


class XTouchMidi:
    """Вход (события) и выход (LED) X-Touch Mini; события в очередь."""

    def __init__(
        self,
        events: asyncio.Queue[tuple[str, int, int]],
        loop: asyncio.AbstractEventLoop,
        *,
        client_name: str = "photocraft-midi",
    ) -> None:
        self._events = events
        self._loop = loop
        self._name = client_name
        self._opened = False
        self._midiin = rtmidi.MidiIn()  # type: ignore  # нет стабов у python-rtmidi
        self._midiout = rtmidi.MidiOut()  # type: ignore  # нет стабов у python-rtmidi

    def open(self) -> None:
        """Найти порты X-TOUCH MINI, открыть, навесить callback."""
        in_idx = find_port_by_name(self._midiin.get_ports(), PORT_SUBSTR)
        if in_idx == -1:
            raise RuntimeError(f"MIDI-вход '{PORT_SUBSTR}' не найден: {self._midiin.get_ports()}")
        out_idx = find_port_by_name(self._midiout.get_ports(), PORT_SUBSTR)
        if out_idx == -1:
            raise RuntimeError(f"MIDI-выход '{PORT_SUBSTR}' не найден: {self._midiout.get_ports()}")

        self._midiin.open_port(in_idx, self._name)
        self._midiout.open_port(out_idx, self._name)
        self._midiin.set_callback(self._on_event)
        self._opened = True

    def is_open(self) -> bool:
        """Открыт ли порт и присутствует ли ещё устройство X-TOUCH MINI."""
        return self._opened and find_port_by_name(self._midiin.get_ports(), PORT_SUBSTR) != -1

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._midiin.cancel_callback()
        with contextlib.suppress(Exception):
            self._midiin.close_port()
        with contextlib.suppress(Exception):
            self._midiout.close_port()
        self._opened = False

    def set_led(self, cc: int, on: bool) -> None:
        """Светодиод кнопки: CC value 127 вкл / 0 выкл."""
        if not self._opened:
            return
        self._midiout.send_message([CC_STATUS_CH11, cc, 127 if on else 0])

    def set_encoder(self, cc: int, raw: int) -> None:
        """Задать внутреннее значение энкодера (счётчик 0..127) — проверено на 0.8.2."""
        if not self._opened:
            return
        self._midiout.send_message([CC_STATUS_CH11, cc, max(0, min(127, raw))])

    def _on_event(self, event: tuple[list[int], float], data: object = None) -> None:
        message, _delta = event
        logger.debug("MIDI raw %s", message)
        if len(message) < 3 or message[0] != CC_STATUS_CH11:
            return
        cc, value = message[1], message[2]
        # callback rtmidi идёт из своего потока -> в очередь event loop потокобезопасно
        self._loop.call_soon_threadsafe(self._events.put_nowait, ("cc", cc, value))
