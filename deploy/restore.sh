#!/usr/bin/env bash
# Восстановить данные из бэкапа:  ./deploy/restore.sh backups/dsc-bot_2026-11-20_0100.tar.gz
# Текущая папка data/ не удаляется, а переименовывается в data.before-restore-<время>.
set -euo pipefail
cd "$(dirname "$0")/.."

ARCHIVE="${1:?Укажи файл бэкапа, например: ./deploy/restore.sh backups/dsc-bot_....tar.gz}"
[[ -f "$ARCHIVE" ]] || { echo "Нет файла $ARCHIVE" >&2; exit 1; }

docker compose stop bot
if [[ -d data ]]; then
  mv data "data.before-restore-$(date -u +%Y%m%d%H%M%S)"
fi
mkdir data
tar -xzf "$ARCHIVE" -C data
mv data/bot.db.snapshot data/bot.db
chown -R 1000:1000 data
docker compose start bot
echo "Восстановлено из $ARCHIVE. Логи: docker compose logs -f bot"
