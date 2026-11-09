#!/usr/bin/env bash
# Первичная настройка чистого сервера Ubuntu 24.04 под бота. Запускать от root после git clone:
#   bash /opt/dsc-bot/deploy/setup-server.sh <ветка>
# Пошагово: docs/DEPLOY.md
# Скрипт можно запускать повторно: уже сделанные шаги он пропускает.
set -euo pipefail

BRANCH="${1:-main}"
REPO_URL="${REPO_URL:-https://github.com/Saykra1/DSC-Bot-HW.git}"
APP_DIR="${APP_DIR:-/opt/dsc-bot}"

if [[ $EUID -ne 0 ]]; then
  echo "Запусти от root (или через sudo)." >&2
  exit 1
fi

step() { echo; echo "==> $*"; }

step "Обновляю систему и ставлю нужные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq git curl ca-certificates ufw fail2ban unattended-upgrades
# Автоматические обновления безопасности
dpkg-reconfigure -f noninteractive unattended-upgrades

step "Ставлю Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

step "Файл подкачки 2 ГБ (запас на случай пиков памяти)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  echo "Подкачка уже есть, пропускаю."
fi

step "Файрвол: открыты только SSH, HTTP и HTTPS"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

step "Защита SSH"
systemctl enable --now fail2ban
if [[ -s /root/.ssh/authorized_keys ]]; then
  # 00- раньше 50-cloud-init.conf, поэтому эта настройка главнее
  cat > /etc/ssh/sshd_config.d/00-dsc-hardening.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
  systemctl restart ssh
  echo "Вход по паролю отключён, работает только SSH-ключ."
else
  echo "В /root/.ssh/authorized_keys нет ключа: вход по паролю оставляю включённым."
  echo "Добавь ключ (см. docs/DEPLOY.md) и запусти скрипт ещё раз."
fi

step "Код бота в $APP_DIR (ветка $BRANCH)"
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  echo "Репозиторий уже есть, обновлять его будет deploy/update.sh."
fi
cd "$APP_DIR"

mkdir -p data backups
chown -R 1000:1000 data   # в контейнере бот работает от пользователя с uid 1000

step "Файл настроек .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  IP="$(curl -fsS4 https://api.ipify.org || true)"
  if [[ -n "$IP" ]]; then
    DOMAIN="${IP//./-}.sslip.io"
    sed -i "s|^DOMAIN=.*|DOMAIN=$DOMAIN|" .env
    sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://$DOMAIN|" .env
    echo "Адрес мини аппа: https://$DOMAIN (можно заменить на свой домен в .env)"
  fi
  echo "Создан .env. Заполни BOT_TOKEN, ADMIN_IDS, LLM_API_KEY, LLM_MODEL:  nano $APP_DIR/.env"
else
  echo ".env уже есть, не трогаю."
fi

step "Ежедневный бэкап в 04:00 МСК"
chmod +x deploy/*.sh
cat > /etc/cron.d/dsc-bot-backup <<CRON
# Бэкап базы и файлов бота (время сервера — UTC, 01:00 UTC = 04:00 МСК)
0 1 * * * root $APP_DIR/deploy/backup.sh >> /var/log/dsc-bot-backup.log 2>&1
CRON

echo
echo "Готово. Дальше:"
echo "  1) nano $APP_DIR/.env    — впиши токены"
echo "  2) cd $APP_DIR && docker compose up -d --build"
echo "  3) docker compose logs -f bot   — проверь, что бот подключился"
