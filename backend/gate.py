"""ОЧЕРЕДЬ ГЕНЕРАЦИЙ: сколько задач человек держит в работе одновременно.

ЗАЧЕМ. До этого модуля восемнадцать мест в main.py поднимали голый
`Thread(..., daemon=True)` без единого ограничителя. Косвенное доказательство,
что очередь нужна, лежало прямо в коде: `_reset_orphan_jobs()` на старте
разгребает задачи, чьи потоки не пережили рестарт, — мы платили цену
отсутствия очереди, не получая от неё ничего.

ЧТО ИМЕННО ОГРАНИЧИВАЕМ И ЧТО НЕТ.
  * Маржу параллельность НЕ защищает. Её защищают токены: потолок
    `points × POINT_USD` абсолютен и не зависит от того, за сколько потоков
    человек их потратит. Параллельность управляет только СКОРОСТЬЮ траты.
  * Поэтому персональный лимит — ПРОДУКТОВЫЙ: он продаёт время сборки клипа
    («клип за ~11 минут»), а не бережёт кассу. Отсюда и щедрость сетки.
  * Глобальный потолок (GEN_SLOTS) — предохранитель на весь сервис, чтобы
    сотня одновременных задач не положила контейнер. По умолчанию он выше
    любого разумного одновременного спроса и в норме не бьёт никого.

ОЧЕРЕДЬ, А НЕ ОТКАЗ. Упёршись в лимит, задача ВСТАЁТ В ОЧЕРЕДЬ и ждёт молча —
ровно как слот ffmpeg (mediagen._Slot: «очередь честнее отказа — работа уже
оплачена токенами»). Отказать после списания токенов значило бы взять деньги
и не сделать работу; вернуть их отдельной веткой — завести ещё одно место,
где баланс расходится с журналом. Конкурент в этом месте отвечает 429
(«Слишком много генераций одновременно») — то есть отказом.

ПОЧЕМУ СПИСАНИЕ ОСТАЁТСЯ НА ВХОДЕ РОУТА. Соблазн «списывать в момент старта
задачи» здесь неверен: инвариант сервиса — «списано → задача выполнится либо
токены вернутся» (_charge/_refund в main.py). Очередь этот инвариант держит:
задача из очереди не может исчезнуть, она либо отработает, либо упадёт с
возвратом. Перенос списания внутрь потока сломал бы проверку баланса ДО
запуска и открыл бы гонку «два роута списали один и тот же остаток».

FIFO. Порядок допуска строго по времени постановки: без него длинная пачка
могла бы бесконечно обгонять одиночную сцену того же человека.

ПОТОКИ, А НЕ ТАБЛИЦА. Состояние очереди живёт в памяти процесса — там же, где
живут сами потоки. Класть его в базу имело бы смысл только при нескольких
воркерах; у нас один процесс, и вторая копия правды в базе после рестарта
врала бы ровно так же, как врали 'running'-строки до _reset_orphan_jobs.
"""
from __future__ import annotations

import itertools
import logging
import os
import threading

log = logging.getLogger("rapclips.gate")

# ─────────────────────────── сетка параллельности ───────────────────────────
#
# Клип = 30 сцен, сцена ~90 секунд машинного времени. Отсюда прямая продающая
# таблица (её же показывает витрина, беря число ОТСЮДА, а не из вёрстки):
#
#   FREE     1 задача  → клип ~45 мин
#   PRO      2         → ~23 мин
#   PRO MAX  4         → ~11 мин
#   ULTRA u1 6         → ~8 мин      u3 12 → ~4 мин
#         u2 8         → ~6 мин      u4 16 → ~3 мин
#
# Один семафор на человека поверх ВСЕХ генераций (кадры, промежуточные, видео,
# моделька персонажа, раскадровка): отдельное число «сколько клипов сразу»
# заводить не нужно — три запущенных трека просто делят те же слоты.
PLAN_PARALLEL = {"free": 1, "pro": 2, "pro_max": 4, "studio": 6}

#: Ступени объёма ULTRA (PLAN_TIERS в main.py). Пустая ступень = первая.
TIER_PARALLEL = {"u1": 6, "u2": 8, "u3": 12, "u4": 16}

#: Владелец. Не «безлимит»: бесконечность здесь означала бы, что админским
#: аккаунтом можно случайно положить сервис одной кнопкой «собрать всё».
ADMIN_PARALLEL = int(os.environ.get("GEN_PARALLEL_ADMIN", "32"))

#: Глобальный потолок на весь сервис. 0 — выключен.
GEN_SLOTS = max(0, int(os.environ.get("GEN_SLOTS", "24")))

#: Сколько секунд задача имеет право ЖДАТЬ очереди. Дальше — запускаем всё
#: равно: лучше перегрузить сервис, чем держать оплаченную работу вечно.
#: Практически недостижимо (полтора часа), это страховка от логической ошибки.
MAX_WAIT_SEC = int(os.environ.get("GEN_QUEUE_MAX_WAIT", "5400"))

_cv = threading.Condition()
_run: dict[int, int] = {}          # user_id → сколько задач в работе
_queue: dict[int, list[int]] = {}  # user_id → билеты в очереди, по порядку
_kinds: dict[int, list[str]] = {}  # user_id → что именно работает (для витрины)
_seq = itertools.count(1)
_global = threading.Semaphore(GEN_SLOTS) if GEN_SLOTS else None


def limit_for(plan_id: str, tier: str = "", *, is_admin: bool = False) -> int:
    """Сколько задач одновременно даёт тариф. Неизвестный тариф считаем
    бесплатным — ошибка в данных не должна раздавать чужую скорость."""
    if is_admin:
        return ADMIN_PARALLEL
    pid = str(plan_id or "free")
    base = PLAN_PARALLEL.get(pid, PLAN_PARALLEL["free"])
    if pid == "studio":
        return TIER_PARALLEL.get(str(tier or "u1"), base)
    return base


def status(user_id: int, limit: int = 0) -> dict:
    """Что происходит у человека прямо сейчас. `limit` вызывающий знает сам
    (он знает тариф), gate его не хранит: тариф меняется оплатой, и копия
    здесь протухала бы до следующей задачи."""
    uid = int(user_id or 0)
    with _cv:
        running = int(_run.get(uid, 0))
        queued = len(_queue.get(uid, ()))
        kinds = list(_kinds.get(uid, ()))
        busy_total = sum(_run.values())
    return {
        "limit": int(limit or 0),
        "running": running,
        "queued": queued,
        "kinds": kinds,
        # Свободные слоты — то, что рисует витрина «2 из 4 заняты».
        "free": max(0, int(limit or 0) - running),
        # Загрузка всего сервиса: нужна честной строке «в час пик подождёшь».
        "service_running": busy_total,
        "service_limit": GEN_SLOTS,
    }


def snapshot() -> dict:
    """Общая картина для админки: кто и сколько занимает."""
    with _cv:
        return {
            "running": {uid: n for uid, n in _run.items() if n},
            "queued": {uid: len(q) for uid, q in _queue.items() if q},
            "total_running": sum(_run.values()),
            "total_queued": sum(len(q) for q in _queue.values()),
            "slots": GEN_SLOTS,
        }


def _admit(uid: int, ticket: int, limit: int) -> None:
    """Дождаться своей очереди и занять слот."""
    with _cv:
        _queue.setdefault(uid, []).append(ticket)
        waited = 0.0
        while True:
            q = _queue.get(uid) or []
            if _run.get(uid, 0) < max(1, limit) and q and q[0] == ticket:
                break
            if waited >= MAX_WAIT_SEC:
                log.warning("очередь: задача %s человека %s ждала %.0f с — пускаю",
                            ticket, uid, waited)
                break
            _cv.wait(1.0)
            waited += 1.0
        q = _queue.get(uid) or []
        if ticket in q:
            q.remove(ticket)
        if not q:
            _queue.pop(uid, None)
        _run[uid] = _run.get(uid, 0) + 1
        # Разбудить следующего: при лимите больше единицы за одним
        # освободившимся слотом может войти сразу несколько задач.
        _cv.notify_all()


def _leave(uid: int, kind: str) -> None:
    with _cv:
        left = _run.get(uid, 0) - 1
        if left > 0:
            _run[uid] = left
        else:
            _run.pop(uid, None)
        lst = _kinds.get(uid)
        if lst and kind in lst:
            lst.remove(kind)
        if not lst:
            _kinds.pop(uid, None)
        _cv.notify_all()


def _body(uid: int, limit: int, fn, args: tuple, kind: str) -> None:
    ticket = next(_seq)
    _admit(uid, ticket, limit)
    with _cv:
        _kinds.setdefault(uid, []).append(kind)
    try:
        if _global is not None:
            _global.acquire()
        try:
            fn(*args)
        finally:
            if _global is not None:
                _global.release()
    except Exception:  # noqa: BLE001 — поток задачи не имеет права уронить сервис
        log.exception("задача %s (%s) человека %s упала", ticket, kind, uid)
    finally:
        _leave(uid, kind)


def spawn(user_id: int, limit: int, fn, args: tuple = (), *, kind: str = "") -> None:
    """Запустить генерацию под лимитом тарифа.

    Замена `Thread(target=fn, args=args, daemon=True).start()` — один в один,
    но с очередью. Поток стартует СРАЗУ (иначе роут пришлось бы держать), а
    ждёт уже внутри себя: снаружи поведение не меняется вообще."""
    threading.Thread(target=_body,
                     args=(int(user_id or 0), int(limit or 1), fn, tuple(args),
                           str(kind or "gen")),
                     daemon=True).start()


def reset() -> None:
    """Забыть всё. Нужен тестам: состояние живёт в модуле, а не в объекте."""
    with _cv:
        _run.clear()
        _queue.clear()
        _kinds.clear()
        _cv.notify_all()
