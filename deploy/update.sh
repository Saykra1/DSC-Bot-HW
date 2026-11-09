#!/usr/bin/env bash
# Обновить бота до свежего кода из GitHub и перезапустить. Запуск: /opt/dsc-bot/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Бэкап перед обновлением"
./deploy/backup.sh || echo "Бэкап не удался (бот, возможно, ещё не запускался), продолжаю."

echo "==> Забираю код"
git pull --ff-only

echo "==> Пересобираю и перезапускаю"
mkdir -p data && chown -R 1000:1000 data 2>/dev/null || true
docker compose up -d --build
docker image prune -f >/dev/null

echo "==> Статус"
docker compose ps
echo "Логи: docker compose logs -f bot"
