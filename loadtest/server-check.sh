#!/usr/bin/env bash
# Безопасная проверка нагрузки на боевом сервере. Запуск из консоли сервера:
#   cd /opt/dsc-bot && ./loadtest/server-check.sh
#
# Что делает:
#   1. Берёт согласованную копию базы (как backup.sh) во временную папку.
#   2. Запускает ОТДЕЛЬНЫЙ экземпляр бота из того же образа на этой копии, в контейнере без сети
#      (--network none): он не может написать ни в Telegram, ни в OpenRouter, реальные участники его не видят.
#      Вместо модели — фейковая LLM внутри того же контейнера.
#   3. Гоняет по нему USERS выдуманных участников DURATION секунд и печатает отчёт.
#   4. Параллельно раз в 5 с замеряет, как отвечает настоящий сайт (одним лёгким запросом /health).
#   5. Удаляет копию базы и контейнер.
# Настоящая база, файлы и боевой контейнер не меняются. Процессор на 2–3 минуты делится с боевым ботом,
# поэтому запускай, когда участников мало.
#
# Параметры (переменные окружения): USERS=100 DURATION=90 CPUS=1 MEMORY=2g
set -euo pipefail
cd "$(dirname "$0")/.."

USERS="${USERS:-100}"
DURATION="${DURATION:-90}"
CPUS="${CPUS:-1}"
MEMORY="${MEMORY:-2g}"
IMAGE="${IMAGE:-dsc-bot:latest}"
NAME="dsc-loadtest"
WORK="$(mktemp -d /tmp/dsc-loadtest.XXXXXX)"
PROBE_PID=""

cleanup() {
  [[ -n "$PROBE_PID" ]] && kill "$PROBE_PID" 2>/dev/null || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"  # в копии базы личные данные участников — не оставляем её на диске
}
trap cleanup EXIT

docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "Нет образа $IMAGE. Сначала: docker compose build" >&2; exit 1; }
FREE_MB=$(df -Pm /tmp | awk 'NR==2 {print $4}')
(( FREE_MB > 1024 )) || { echo "Мало места в /tmp (${FREE_MB} МБ), нужно хотя бы 1 ГБ" >&2; exit 1; }

echo "==> Копия базы во временную папку"
mkdir -p "$WORK/data"
if docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
  docker compose exec -T bot python - <<'PY'
import sqlite3
src = sqlite3.connect("/app/data/bot.db")
dst = sqlite3.connect("/app/data/loadtest.db.copy")
src.backup(dst)
dst.close(); src.close()
PY
  mv data/loadtest.db.copy "$WORK/data/bot.db"
elif [[ -f data/bot.db ]]; then
  cp data/bot.db "$WORK/data/bot.db"
else
  echo "Базы ещё нет — тест пойдёт на пустой базе."
fi
chown -R 1000:1000 "$WORK/data"

DOMAIN="$(grep -E '^DOMAIN=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
if [[ -n "$DOMAIN" ]]; then
  echo "==> Замер настоящего сайта https://$DOMAIN/health раз в 5 с (пишется в $WORK/probe.txt)"
  (
    while true; do
      t=$(curl -s -o /dev/null -m 10 -w '%{http_code} %{time_total}' "https://$DOMAIN/health" || echo "ERR 10")
      echo "$(date +%T) $t" >> "$WORK/probe.txt"
      sleep 5
    done
  ) &
  PROBE_PID=$!
fi

echo "==> Тест: $USERS участников, $DURATION с, лимиты контейнера: $CPUS CPU / $MEMORY"
docker run --rm --name "$NAME" --network none --cpus "$CPUS" --memory "$MEMORY" \
  -v "$WORK/data:/app/data" -v "$PWD/loadtest:/loadtest:ro" \
  -e BOT_TOKEN= -e ADMIN_IDS=900000001 -e DATA_DIR=/app/data -e HOST=127.0.0.1 -e PORT=18080 \
  -e LLM_BASE_URL=http://127.0.0.1:9999/v1 -e LLM_MODEL=loadtest-fake -e LLM_API_KEY=x -e CHECK_CONCURRENCY=4 \
  -e USERS="$USERS" -e DURATION="$DURATION" \
  "$IMAGE" sh -c '
    python /loadtest/fake_llm.py --port 9999 --latency 2-5 --rpm 0 &
    python -m app.main > /app/data/app.log 2>&1 &
    APP=$!
    for i in $(seq 60); do
      python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:18080/health\", timeout=2)" 2>/dev/null && break
      sleep 1
    done
    python /loadtest/loadtest.py --url http://127.0.0.1:18080 --users "$USERS" --duration "$DURATION" \
      --ramp 20 --drain 90 --pid "$APP"
    code=$?
    if grep -qiE "database is locked|Traceback" /app/data/app.log; then
      echo "! В логе тестового бота есть ошибки:"; grep -iE -A3 "database is locked|Traceback" /app/data/app.log | head -40
    fi
    exit $code
  ' && RESULT=0 || RESULT=$?

if [[ -s "$WORK/probe.txt" ]]; then
  echo
  echo "==> Настоящий сайт во время теста (код, секунды):"
  awk '{print "  " $0} {n++; if ($2 != "200") bad++; s+=$3; if ($3 > m) m=$3}
       END {if (n) printf "  Итого: %d замеров, не 200: %d, среднее %.2f с, максимум %.2f с\n", n, bad+0, s/n, m}' "$WORK/probe.txt"
fi
exit "$RESULT"
