#!/usr/bin/env bash
# Лаунчер: PhotoCraft GUI (control-порт) + прослойка photocraft-midi.
# По образцу ~/gimp3. Если PhotoCraft уже слушает control-порт — переиспользуем
# запущенный экземпляр, GUI повторно не стартуем.
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

if [[ -f "$REPO_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_DIR/.env"
  set +a
fi

CONTROL_PORT="${PHOTOCRAFT_CONTROL_PORT:-7878}"
TOKEN_FILE="${PHOTOCRAFT_CONTROL_TOKEN_FILE:-$HOME/.config/photocraft/control.token}"
APPIMAGE="${PHOTOCRAFT_APPIMAGE:-$HOME/soft/artcraft/photocraft-0.8.2-linux-x86_64.AppImage}"

port_open() {
  (exec 3<>"/dev/tcp/127.0.0.1/$CONTROL_PORT") 2>/dev/null
}

if port_open; then
  echo "PhotoCraft уже слушает 127.0.0.1:$CONTROL_PORT — переиспользуем запущенный экземпляр"
else
  if pgrep -f "/usr/bin/photocraft" >/dev/null 2>&1; then
    echo "ВНИМАНИЕ: процесс PhotoCraft есть, но порт $CONTROL_PORT закрыт — вероятно, он запущен без --control." >&2
    echo "Закройте его и запустите лаунчер снова, либо запустится второй экземпляр." >&2
  fi
  echo "стартуем PhotoCraft (control-порт $CONTROL_PORT) ..."
  "$APPIMAGE" --control "$CONTROL_PORT" --control-token-file "$TOKEN_FILE" &
  for _ in $(seq 1 60); do
    if port_open; then
      echo "порт готов"
      break
    fi
    sleep 1
  done
  if ! port_open; then
    echo "control-порт $CONTROL_PORT так и не поднялся за 60 c" >&2
    exit 1
  fi
fi

# Перезапускаем только прослойку (PhotoCraft не трогаем)
pkill -f "photocraftmidi" 2>/dev/null || true

cd "$REPO_DIR"
export PHOTOCRAFT_CONTROL_PORT PHOTOCRAFT_CONTROL_TOKEN_FILE
export PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$REPO_DIR/.venv/bin/python" -m photocraftmidi