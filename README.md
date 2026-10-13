# photocraft-midi

Прослойка на Python 3.14: слушает MIDI-контроллер **Behringer X-Touch Mini** и управляет
**PhotoCraft 0.8.2** через его **MCP-сервер**, дублируя состояние приложения на LED контроллера.

Аналог `gimp-nanok2` (управление GIMP с X-Touch Mini), но вместо нативного MIDI — MCP-мост к
PhotoCraft (Rust-редактор, https://github.com/storytold/photocraft).

## Как это работает

```
X-Touch Mini ──(MIDI CC)──▶ photocraftmidi ──(MCP, stdio)──▶ photocraft-cli mcp --bridge ──▶ PhotoCraft GUI
       ▲                                                                                          │
       └────────────────────── LED / счётчики энкодеров (MIDI CC) ◀── ui_inspect (поллинг) ◀──────┘
```

- `photocraftmidi` запускает `photocraft-cli mcp --bridge 127.0.0.1:<port>` (подпроцесс) и говорит с ним
  по stdio (JSON-RPC, инструменты `command_run`/`ui_set`/`ui_inspect`).
- Кнопки/энкодеры транслируются в команды PhotoCraft; LED и счётчики энкодеров обновляются по
  `ui_inspect` (фоновый поллинг ~1 Гц — push-уведомлений в канале нет).
- При недоступности MIDI или bridge — ожидание и авто-reconnect (с LED-анимацией ожидания).

## Требования

- Linux (ALSA), Python 3.14 (`/usr/bin/python3.14`), [pdm](https://pdm-project.org/).
- Сборка `python-rtmidi`: `libasound2-dev libjack-jackd2-dev build-essential python3-dev`.
- Установленный **PhotoCraft 0.8.2** (AppImage) и **X-Touch Mini**.

## Установка

```bash
pdm install -G dev          # venv в .venv, зависимости + dev (ruff/ty/pytest)
cp .env.example .env        # заполнить пути/порт
```

## Настройка `.env`

```
PHOTOCRAFT_CONTROL_PORT=7878
PHOTOCRAFT_CONTROL_TOKEN_FILE=/home/<user>/.config/photocraft/control.token
# PHOTOCRAFT_CLI=/path/to/photocraft-cli     # авто-поиск: $PATH или /tmp/.mount_*/usr/bin
# PHOTOCRAFT_APPIMAGE=/home/<user>/soft/artcraft/photocraft-0.8.2-linux-x86_64.AppImage
# PHOTOCRAFT_MIDI_CONFIG=/path/to/config.toml
# PHOTOCRAFT_MIDI_LOG=DEBUG                  # оверрайд уровня логирования
```

Токен-файл и контрольный порт создаёт сам PhotoCraft при запуске с `--control`.

## Запуск

```bash
./launch-photocraft-midi.sh
```

Скрипт: `source .env` → если control-порт уже слушается — переиспользует запущенный PhotoCraft,
иначе стартует его и ждёт порт → перезапускает мост (`.venv/bin/python -m photocraftmidi`, `PYTHONPATH=src`).

Напрямую:

```bash
PYTHONPATH=src .venv/bin/python -m photocraftmidi
PYTHONPATH=src .venv/bin/python -m photocraftmidi --monitor   # сырой дамп входящих MIDI
```

## `config.toml`

Карта «MIDI CC → действие» и настройки. Оверрайд пути — `PHOTOCRAFT_MIDI_CONFIG`.

- `[xtouch]` — `encoder_scheme` (`relative1|relative2|relative3`).
- `[layer_a.button]` — кнопки: `{ kind = momentary|toggle, mcp, args, led, resets, inject }`.
  - срабатывают на **любое** сообщение CC (X-Touch в toggle-режиме шлёт одно сообщение на нажатие);
  - `momentary` — одноразовое действие; `toggle` — значение CC = целевое (`127`=вкл, `0`=выкл);
  - `led`: `false` | `true` | `undo`/`redo` | `tool:<имя>` | `layer.visible`;
  - `resets = { <encoder-key> = <value> }` — выставить счётчик энкодера на устройстве.
- `[layer_a.encoder]` — энкодеры: `absolute { command, key, min, max, scale? }` либо `steps { incr, decr }`
  (`buffer = true` — коалесценция 40 мс, у BrushSize).
- `[sync]` — `enabled`, `interval_ms`, `quiet_ms`.
- `[log]` — `path` (по умолчанию в текущем каталоге), `level`, `max_bytes`, `backups`.

## Раскладка X-Touch Mini (Layer A)

- CC 0–7 — кнопки энкодеров; 8–15 — энкодеры; 16–23 и 24–31 — две строки кнопок; 32 — фейдер.
- Энкодеры CC 8–14 — абсолютные (позиция 0–127), CC 15 — относительный.
- LED — кнопки 16–31; LED-кольца энкодеров показывают их счётчик.

## Разработка

```bash
pdm run lint        # ruff check src tests
pdm run typecheck   # ty check
pdm run test        # pytest (интеграционные помечены `integration`, пропуск без env)
```

Оформление — как в соседних проектах; тайпчекер — `ty`, линтер — `ruff` (line-length 120).

## Troubleshooting

- **`photocraft-cli не найден`** — задайте `PHOTOCRAFT_CLI` или запустите PhotoCraft AppImage
  (CLI лежит внутри `/tmp/.mount_*/usr/bin/photocraft-cli`).
- **Bridge недоступен** — PhotoCraft должен быть запущен с `--control <port> --control-token-file <path>`
  (лаунчер делает это сам).
- **Не видно MIDI-порта** — проверьте `X-TOUCH MINI` в `aconnect -l`; мост ждёт появления порта и
  переподключится.
- **Логи** — stderr и файл из `[log].path` (по умолчанию `photocraft-midi.log`).

## Лицензия

MIT (см. `LICENSE`).
