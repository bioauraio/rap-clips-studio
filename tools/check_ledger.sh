#!/usr/bin/env bash
# Сторож «одной двери» к балансу очков.
#
# Контракт: user.gen_points имеет право менять ТОЛЬКО main._move_points, и она
# же пишет строку в journal (point_events). Пока контракт держался
# дисциплиной, пять мест меняли баланс напрямую и рядом вручную звали
# _log_points — следующая правка про это забыла бы, журнал перестал бы
# объяснять остаток, и обнаружилось бы это через квартал.
#
# Запуск: tools/check_ledger.sh   (ненулевой код возврата = сборка красная)
set -u
cd "$(dirname "$0")/.."

BAD=$(grep -rnE '\.gen_points[[:space:]]*(=|\+=|-=)' backend/*.py \
      | grep -v 'def _move_points' \
      | grep -v 'backend/db.py' \
      | grep -v '# ledger-ok')

if [ -n "$BAD" ]; then
  echo "Прямое изменение gen_points мимо _move_points:"
  echo "$BAD"
  echo
  echo "Двигай очки через main._move_points(...) — она же пишет строку журнала."
  echo "Если строка действительно исключение (создание админа), пометь её"
  echo "комментарием '# ledger-ok' в той же строке."
  exit 1
fi
echo "журнал очков: одна дверь на месте"
