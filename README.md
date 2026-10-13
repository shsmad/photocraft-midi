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

Ключи таблиц `[layer_a.button]` и `[layer_a.encoder]` — номера CC. Значения — inline-таблицы; ниже —
полное описание аргументов.

### `[layer_a.button]` — кнопки (CC 0–7 и 16–31)

Срабатывают на **любое** сообщение CC (X-Touch в toggle-режиме шлёт одно сообщение на нажатие,
поочерёдно `127` и `0`). Поля записи:

| Поле | Обязательно | Значения | Назначение |
| --- | --- | --- | --- |
| `kind` | да | `"momentary"` \| `"toggle"` | `momentary` — одноразовое действие, значение CC игнорируется; `toggle` — значение CC задаёт целевое состояние (см. `states`). На поведение срабатывания не влияет (обе реагируют на любое сообщение) — это маркер семантики. |
| `mcp` | да | `"command_run"` \| `"ui_set"` \| `"ui_inspect"` \| `"command_list"` | Вызываемый MCP-инструмент. |
| `args` | да | объект | Аргументы инструмента. `command_run` → `{ id = "<команда>", params = { … } }`; `ui_set` → `{ fields = { … } }`; `ui_inspect`/`command_list` → `{}`. У `command_run` строгие `params`: лишний ключ — ошибка `-32602`. |
| `led` | нет (`false`) | `false` \| `true` \| `"undo"` \| `"redo"` \| `"tool:<имя>"` \| `"layer.visible"` \| `"brush.transfer.opacity"` | Обратная связь на LED кнопки: `false` — не управлять; `true` — LED = состояние тумбла из `inject` (работает для `panels.*` и `visible`); `"undo"`/`"redo"` — активность `edit.undo`/`redo`; `"tool:<имя>"` — активен инструмент (`brush`, `smudge`, `cloneStamp`, `healing`); `"layer.visible"` — видимость активного слоя; `"brush.transfer.opacity"` — включена ли у кисти «opacity from pressure» (`transfer.opacity.control`). Неизвестная строка — LED не трогается. |
| `inject` | нет | строка | Имя параметра, куда перед вызовом подставляется целевое значение. Поддерживает вложенность через точку (`panels.options_bar`, `transfer.opacity.control`). Для `command_run` пишет в `params`, для `ui_set` — в `fields`. Без `inject` действие выполняется «как есть». |
| `states` | нет | таблица `{ <CC-значение> = <целевое> }` | Карта «значение CC → что подставить в `inject`». Без неё: `127`/любое ненулевое → `true`, `0` → `false`. Для небулевых целей: `{ 127 = 1.0, 0 = 0.25 }` (hardness), `{ 127 = "penPressure", 0 = "off" }` (opacity-from-pressure). Ключи — числа. |
| `resets` | нет (`{}`) | таблица `{ <encoder-key> = <число> }` | Переставить счётчик соответствующего **абсолютного** энкодера (по его `key`): на устройство шлётся CC с `inverse_map(value)`, а внутреннее значение сбрасывается ровно к `value` — дальнейшее вращение продолжается от него без скачка. Если задан `states`, вместо статического `value` берётся целевое значение из карты. |

### `[layer_a.encoder]` — энкодеры (CC 8–15)

Два взаимоисключающих вида записи: `absolute` (по `command`) либо `steps` (по `incr`/`decr`).
У любого вида есть опциональный `buffer`.

**absolute** — значение ведётся в приложении по дельтам от позиции устройства:

| Поле | Обязательно | Значения | Назначение |
| --- | --- | --- | --- |
| `command` | да | `{ mcp, args }` | Команда, вызываемая при изменении значения. |
| `key` | да | строка | Внутреннее имя значения (используется в `resets` кнопок и в синке). |
| `param` | нет (= `key`) | строка | Имя параметра команды, в который подставляется значение (может отличаться от `key`, напр. `key="layer_opacity"`, `param="opacity"`). Поддерживает вложенность через точку. |
| `min` | нет (`0`) | число | Нижняя граница (устройство 0 → `min`). |
| `max` | нет (`1`) | число | Верхняя граница (устройство 127 → `max`). |
| `scale` | нет (`"linear"`) | `"linear"` \| `"log"` | Шкала отображения позиции в значение (`log` — для зумов и т.п.). |

Позиция 0–127 отображается в `min..max`; значение хранится в приложении, а `set_encoder`
переставляет счётчик устройства (запас хода + LED-кольцо). Синк подтягивает значение из `ui_inspect`.

**steps** — относительный энкодер:

| Поле | Обязательно | Значения | Назначение |
| --- | --- | --- | --- |
| `incr` | да | `{ mcp, args }` | Команда при повороте вправо (вызывается N раз, где N — число шагов). |
| `decr` | да | `{ mcp, args }` | Команда при повороте влево. |

**Общее:**

| Поле | Обязательно | Значения | Назначение |
| --- | --- | --- | --- |
| `buffer` | нет (`false`) | `true` \| `false` | Коалесценция событий в окне 40 мс с накоплением дельт (сейчас не используется). |

Схема знака относительного энкодера задаётся в `[xtouch].encoder_scheme`:
`relative1` (decr=127, incr=1), `relative2` (decr=63, incr=65), `relative3` (decr=65, incr=1).

### Примеры записей

```toml
[layer_a.button]
# toggle с картой значений: 127 -> 100%, 0 -> 25%, с перестановкой счётчика hardness-энкодера
4 = { kind = "toggle", mcp = "command_run", args = { id = "tools.setBrush", params = { hardness = 1.0 } }, led = false, resets = { hardness = 1.0 }, states = { 127 = 1.0, 0 = 0.25 }, inject = "hardness" }
# переключение панели с LED
16 = { kind = "toggle", mcp = "ui_set", args = { fields = { panels = { options_bar = true } } }, led = true, inject = "panels.options_bar" }
# выбор инструмента, LED горит при активном инструменте
29 = { kind = "toggle", mcp = "ui_set", args = { fields = { tool = "cloneStamp" } }, led = "tool:cloneStamp" }

[layer_a.encoder]
# абсолютный: zoom 0.1..16 по логарифмической шкале
14 = { command = { mcp = "ui_set", args = { fields = { zoom = 0 } } }, key = "zoom", min = 0.1, max = 16, scale = "log" }
# шаговый: размер кисти
15 = { incr = { mcp = "command_run", args = { id = "tools.increaseBrushSize", params = {} } }, decr = { mcp = "command_run", args = { id = "tools.decreaseBrushSize", params = {} } } }
```

### Остальные секции

- `[xtouch]` — `encoder_scheme` (`relative1|relative2|relative3`).
- `[sync]` — фоновая синхронизация: `enabled` (bool), `interval_ms` (по умолчанию `1000`),
  `quiet_ms` (по умолчанию `500` — контрол, тронутый за это время, синк не трогает).
- `[log]` — `path` (по умолчанию `photocraft-midi.log` в текущем каталоге), `level`
  (`DEBUG|INFO|WARNING|ERROR`; env `PHOTOCRAFT_MIDI_LOG` переопределяет), `max_bytes`
  (по умолчанию `1_000_000`), `backups` (по умолчанию `3`).

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
