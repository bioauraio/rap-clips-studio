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

# ЗАМОК НА ВРЕМЯ ВЫКАТКИ. Владелец и Аня катят с разных машин;два одновременных
# rsync'а в одну папку дают смесь файлов, а «кто последний — тот и прав»
# выглядит как откат чужой работы. Второй деплой ждёт, а не лезет параллельно.
WHO="$(git config user.name 2>/dev/null || whoami)@$(hostname -s)"
COMMIT="$(git rev-parse --short HEAD)"
VER="$(grep -oE 'app\.js\?v=[0-9]+' frontend/index.html | head -1)"

echo "== жду свободный деплой-замок =="
for i in $(seq 1 60); do
  if $SSH $MSK "mkdir /tmp/rapclips-deploy.lock 2>/dev/null && echo '$WHO $COMMIT' > /tmp/rapclips-deploy.lock/owner"; then break; fi
  # Кто держит замок — видно сразу: иначе ожидание выглядит как зависание.
  HOLDER=$($SSH $MSK 'cat /tmp/rapclips-deploy.lock/owner 2>/dev/null' || true)
  # Замок старше 20 минут — след упавшего деплоя, снимаем.
  $SSH $MSK 'find /tmp -maxdepth 1 -name rapclips-deploy.lock -mmin +20 -exec rm -rf {} \; 2>/dev/null' || true
  echo "  катит ${HOLDER:-кто-то ещё} — жду 15с ($i/60)"; sleep 15
done
trap '$SSH $MSK "rm -rf /tmp/rapclips-deploy.lock 2>/dev/null" || true' EXIT

# ЖУРНАЛ ВЫКАТОК. Двое катят с разных машин: без записи «кто/что/когда»
# разбор «почему на проде не моё» превращается в гадание.
$SSH $MSK "echo \"\$(date '+%F %T') $WHO commit=$COMMIT $VER старт\" >> /opt/rapclips/deploy-journal.log" || true

echo "== выкатка на msk (rsync ТОЛЬКО подпапками) =="
rsync -az --delete -e "$SSH" backend/  $MSK:/opt/rapclips/backend/
rsync -az --delete -e "$SSH" frontend/ $MSK:/opt/rapclips/frontend/
# Админка едет ТРЕТЬЕЙ папкой. До 27.08 её тут не было: Dockerfile её копирует
# (COPY admin/ /app/admin/), а деплой не обновлял — правки админки молча не
# доезжали, и это выглядело как «кнопка не появилась».
rsync -az --delete -e "$SSH" admin/    $MSK:/opt/rapclips/admin/
$SSH $MSK 'cd /opt/rapclips && ./deploy.sh'

echo "== ждём тишины: рестарт посреди генерации убивает оплаченную работу =="
for i in $(seq 1 30); do
  BUSY=$($SSH $MSK "ssh root@5.42.120.67 'docker exec -e PYTHONPATH=/app qlolvideo-api python3 -c \"
from db import SessionLocal, Scene, Track
s=SessionLocal()
n=s.query(Scene).filter(Scene.video_status.in_((\\\"queued\\\",\\\"running\\\"))).count()
n+=s.query(Scene).filter(Scene.image_status.in_((\\\"queued\\\",\\\"running\\\"))).count()
print(n)\"'" 2>/dev/null | tail -1)
  [ "${BUSY:-0}" = "0" ] && break
  echo "  активных генераций: $BUSY — жду 20с ($i/30)"
  sleep 20
done

echo "== синк msk -> lolq (5.42.120.67) =="
$SSH $MSK 'rsync -az --delete /opt/rapclips/backend/  root@5.42.120.67:/opt/qlolvideo/backend/ &&
           rsync -az --delete /opt/rapclips/frontend/ root@5.42.120.67:/opt/qlolvideo/frontend/ &&
           rsync -az --delete /opt/rapclips/admin/    root@5.42.120.67:/opt/qlolvideo/admin/ &&
           ssh root@5.42.120.67 "cd /opt/qlolvideo/infra && docker compose up -d --build qlolvideo"'

echo "== проверка: прод обязан отдавать ИМЕННО нашу версию =="
# 26.08 прод откатился на v126 при свежих файлах на диске: docker собрал образ
# из КЕША слоя со статикой, и в контейнер уехал старый index.html. Снаружи это
# выглядит как «мои правки пропали». Поэтому версия сверяется, а расхождение
# лечится пересборкой без кеша — молча старую версию больше не отдаём.
WANT=$(grep -oE 'app\.js\?v=[0-9]+' frontend/index.html | head -1)
for attempt in 1 2; do
  sleep 12
  GOT=$(curl -fsS -m 20 https://lolq.ai/ | grep -oE 'app\.js\?v=[0-9]+' | head -1 || true)
  [ "$GOT" = "$WANT" ] && {
    $SSH $MSK "echo \"\$(date '+%F %T') $WHO commit=$COMMIT $GOT готово\" >> /opt/rapclips/deploy-journal.log" || true
    echo "прод: $GOT — совпадает"; echo "деплой завершён"; exit 0; }
  echo "!! прод отдаёт $GOT вместо $WANT — пересобираю образ без кеша ($attempt/2)"
  $SSH $MSK 'ssh root@5.42.120.67 "cd /opt/qlolvideo/infra &&
             docker compose build --no-cache qlolvideo >/dev/null 2>&1 &&
             docker compose up -d qlolvideo"'
done
echo "!! версия так и не сошлась: ждали $WANT, прод отдаёт $GOT"; exit 1
