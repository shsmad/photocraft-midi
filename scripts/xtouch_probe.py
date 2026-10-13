#!/usr/bin/env python3
"""Проверка: можно ли задать внутреннее значение энкодера X-Touch Mini через MIDI.

Фаза 1: ничего не отправляем — базовая (что шлёт энкодер при повороте).
Фаза 2: отправляем CC11=64 (попытка задать значение 64), затем крутите.
Фаза 3: отправляем CC11=0, затем крутите.
Фаза 4: отправляем CC11=127, затем крутите.

Если задание работает — после отправки энкодер начнёт от этого значения.
Иначе всегда шлёт физическую позицию.
"""
import time

import rtmidi

PORT = "X-TOUCH MINI"
CC_CH11 = 0xBA


def find(ports: list[str]) -> int:
    return next((i for i, p in enumerate(ports) if PORT in p), -1)


midiin = rtmidi.MidiIn()  # type: ignore  # нет стабов у python-rtmidi
midiout = rtmidi.MidiOut()  # type: ignore  # нет стабов у python-rtmidi
ii = find(midiin.get_ports())
oo = find(midiout.get_ports())
if ii == -1 or oo == -1:
    raise SystemExit(f"порт '{PORT}' не найден: in={midiin.get_ports()} out={midiout.get_ports()}")
midiin.open_port(ii, "probe-in")
midiout.open_port(oo, "probe-out")

events: list[list[int]] = []


def callback(event: tuple[list[int], float], _data: object = None) -> None:
    msg, _ = event
    events.append(msg)


midiin.set_callback(callback)


def collect(seconds: float, label: str) -> None:
    events.clear()
    print(f"\n--- {label}: КРУТИТЕ энкодер 11 ({seconds:.0f} c) ---", flush=True)
    t = time.time()
    while time.time() - t < seconds:
        time.sleep(0.05)
    cc11 = [m[2] for m in events if len(m) >= 3 and m[0] == CC_CH11 and m[1] == 11]
    print(f"  всего сообщений: {len(events)}; CC11: {len(cc11)}", flush=True)
    if cc11:
        print(f"  CC11 values: {cc11[:50]}", flush=True)
    else:
        print("  CC11 не приходило", flush=True)


collect(6, "ФАЗА 1 — базовая (ничего не отправляю)")

for value in (64, 0, 127):
    print(f"\n>>> отправляю CC11 = {value} (попытка задать значение)", flush=True)
    midiout.send_message([CC_CH11, 11, value])
    time.sleep(0.3)
    collect(6, f"ФАЗА после отправки CC11={value}")

print("\nготово")
