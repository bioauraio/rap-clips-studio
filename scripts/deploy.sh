#!/usr/bin/env bash
# Деплой lolq.ai из локальной копии репозитория. Работает у любого члена команды.
#
# Правило параллельной работы: канон — GitHub (bioauraio/rap-clips-studio).
# Скрипт НЕ выкатит код, пока локальная копия не синхронизирована с origin/main:
# это единственная защита от того, что rsync --delete молча затрёт чужие правки.
#
# Использование:  ./scripts/deploy.sh [путь_к_ssh_ключу]
# Ключ по умолчанию: ~/.ssh/bioaura_organism (у Ани — ~/.ssh/anya_lolq_key).
set -euo pipefail
cd "$(dirname "$0")/.."

KEY="${1:-$HOME/.ssh/bioaura_organism}"
[ -f "$KEY" ] || KEY="$HOME/.ssh/anya_lolq_key"
[ -f "$KEY" ] || { echo "нет ssh-ключа: укажи путь аргументом"; exit 1; }
MSK=root@201.51.8.78
SSH="ssh -o ConnectTimeout=25 -i $KEY"

echo "== git-синхронизация =="
if [ -n "$(git status --porcelain)" ]; then
  echo "!! есть незакоммиченные правки — сначала git add/commit (или stash)"; exit 1
fi
git pull --rebase origin main
git push origin main

echo "== выкатка на msk (rsync ТОЛЬКО подпапками) =="
rsync -az --delete -e "$SSH" backend/  $MSK:/opt/rapclips/backend/
rsync -az --delete -e "$SSH" frontend/ $MSK:/opt/rapclips/frontend/
$SSH $MSK 'cd /opt/rapclips && ./deploy.sh'

echo "== синк msk -> lolq (5.42.120.67) =="
$SSH $MSK 'rsync -az --delete /opt/rapclips/backend/  root@5.42.120.67:/opt/qlolvideo/backend/ &&
           rsync -az --delete /opt/rapclips/frontend/ root@5.42.120.67:/opt/qlolvideo/frontend/ &&
           ssh root@5.42.120.67 "cd /opt/qlolvideo/infra && docker compose up -d --build qlolvideo"'

echo "== проверка =="
curl -fsS -m 15 https://lolq.ai/ | grep -oE 'app\.js\?v=[0-9]+' | head -1
echo "деплой завершён"
