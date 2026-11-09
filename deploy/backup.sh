#!/usr/bin/env bash
# Бэкап SQLite-базы и загруженных файлов в ./backups (хранится KEEP_DAYS дней, по умолчанию 7).
# Если на диске остаётся меньше MIN_FREE_MB, самые старые архивы удаляются раньше (последний не трогается).
# Если в .env задан BACKUP_CHAT_ID, архив до 49 МБ ещё и отправляется туда ботом (копия вне сервера).
set -euo pipefail
cd "$(dirname "$0")/.."

KEEP_DAYS="${KEEP_DAYS:-7}"
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
STAMP="$(date -u +%Y-%m-%d_%H%M)"
OUT="backups/dsc-bot_$STAMP.tar.gz"
mkdir -p backups

# Согласованный снимок базы, пока бот работает (sqlite backup API), делается внутри контейнера
if docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
  docker compose exec -T bot python - <<'PY'
import sqlite3
src = sqlite3.connect("/app/data/bot.db")
dst = sqlite3.connect("/app/data/bot.db.snapshot")
src.backup(dst)
dst.close(); src.close()
PY
elif [[ -f data/bot.db ]]; then
  # Бот остановлен: можно просто скопировать файл
  cp data/bot.db data/bot.db.snapshot
else
  echo "Нет data/bot.db — нечего бэкапить." >&2
  exit 1
fi

# В архив идёт снимок вместо живой базы, плюс все файлы заданий и хакатонов
tar -czf "$OUT" -C data --exclude='./bot.db' --exclude='./bot.db-*' .
rm -f data/bot.db.snapshot
chmod 600 "$OUT"
echo "$(date -u '+%F %T') бэкап: $OUT ($(du -h "$OUT" | cut -f1))"

find backups -name 'dsc-bot_*.tar.gz' -mtime +"$KEEP_DAYS" -delete

free_mb() { df -Pm . | awk 'NR==2 {print $4}'; }
while (( $(free_mb) < MIN_FREE_MB )); do
  mapfile -t OLD < <(ls -1 backups/dsc-bot_*.tar.gz | sort)
  (( ${#OLD[@]} > 1 )) || { echo "Мало места на диске ($(free_mb) МБ свободно), а удалять больше нечего!" >&2; break; }
  echo "Мало места ($(free_mb) МБ), удаляю старый бэкап ${OLD[0]}"
  rm -f "${OLD[0]}"
done

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
CHAT_ID="$(env_get BACKUP_CHAT_ID || true)"
TOKEN="$(env_get BOT_TOKEN || true)"
if [[ -n "$CHAT_ID" && -n "$TOKEN" ]]; then
  SIZE=$(stat -c %s "$OUT")
  if (( SIZE < 49 * 1024 * 1024 )); then
    curl -fsS -o /dev/null -F chat_id="$CHAT_ID" -F document=@"$OUT" \
      -F caption="Бэкап бота $STAMP UTC" "https://api.telegram.org/bot$TOKEN/sendDocument" \
      && echo "Отправлен в Telegram." || echo "Не удалось отправить в Telegram." >&2
  else
    echo "Архив больше 49 МБ, в Telegram не отправляю (лимит Bot API)." >&2
  fi
fi
