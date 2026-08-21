#!/usr/bin/env python3
"""Проверка связки «урок → артефакт».

ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ. learn.py разбирает маркдаун и НЕ ИМЕЕТ ПРАВА зависеть
от каталога промтов: школа должна открываться, даже если каталог сломан, и
наоборот. Но ссылка между ними всё равно существует — во фронтматтере урока
лежат ключи `pack`, `preset`, `styles`, `mode`. Ключ с опечаткой не уронит ни
один из двух модулей: урок откроется, кнопка «Открыть в студии» молча ничего
не применит, и заметит это пользователь, а не мы.

Поэтому проверка живёт третьим файлом и знает про оба. Гоняется руками и в CI:

    python3 tools/check_learn_artifacts.py

Что проверяет:
  * ключи pack/preset/styles/mode из фронтматтера существуют в каталогах;
  * набор не дороже урока (бесплатный урок не обещает платную кнопку);
  * оба языка описывают один и тот же артефакт — расхождение перевода здесь
    означает, что англичанин и русский нажмут разные кнопки;
  * у каждого набора есть хотя бы один урок, и наоборот — набор без урока
    некому применить.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import learn                      # noqa: E402
import prompts_catalog            # noqa: E402
import prompts_library            # noqa: E402

#: Режимы студии — реестр MODES в frontend/nav.js. Второй список здесь, а не
#: импорт из JS: тащить парсер бандла ради пяти строк дороже, чем сверять их
#: глазами раз в полгода.
MODES = ("clip", "ugc", "series", "mockup", "chat")


def main() -> int:
    err: list[str] = []

    # Сначала сами каталоги: битый каталог даст лавину ложных ошибок ниже.
    for name, problems in (("prompts_catalog", prompts_catalog.validate()),
                           ("prompts_library", prompts_library.validate())):
        err += [f"{name}: {p}" for p in problems]
    if err:
        print("\n".join(err))
        return 1

    by_key: dict[str, dict[str, dict]] = {}
    for lang in learn.LANGS:
        lessons = learn._load(lang)
        if not lessons:
            err.append(f"{lang}: уроков не найдено — проверь LEARN_DIR")
        for x in lessons:
            by_key.setdefault(x["key"], {})[lang] = x

            if x["mode"] not in MODES:
                err.append(f"{lang}/{x['file']}: режим {x['mode']!r} вне MODES")
            if x["preset"] and x["preset"] not in prompts_catalog.PRESET_KEYS:
                err.append(f"{lang}/{x['file']}: каркас {x['preset']!r} не существует")
            for st in x["styles"]:
                if st not in prompts_catalog.STYLE_KEYS:
                    err.append(f"{lang}/{x['file']}: стиль {st!r} не существует")
            if not x["pack"]:
                continue
            pack = prompts_library._PACK_BY_KEY.get(x["pack"])
            if not pack:
                err.append(f"{lang}/{x['file']}: набор {x['pack']!r} не существует")
                continue
            if pack["lesson"] != x["key"]:
                err.append(f"{lang}/{x['file']}: набор {x['pack']} привязан к уроку "
                           f"{pack['lesson']!r}, а лежит в {x['key']!r}")
            # Урок бесплатнее своего набора — законно и осознанно: текст открыт,
            # применимость по тарифу. А вот обратное — ошибка: платный урок с
            # бесплатным набором значит, что мы заперли то, что и так отдаём.
            if learn.PLAN_ORDER.index(x["access"]) > learn.PLAN_ORDER.index(pack["tier"]):
                err.append(f"{lang}/{x['file']}: урок {x['access']}, а набор "
                           f"{pack['tier']} — урок дороже своего артефакта")

    # Оба языка обязаны вести в один артефакт.
    for key, langs in by_key.items():
        if len(langs) < len(learn.LANGS):
            err.append(f"урок {key}: есть не на всех языках ({', '.join(langs)})")
            continue
        for field in ("pack", "preset", "mode", "level", "access"):
            vals = {lg: x[field] for lg, x in langs.items()}
            if len(set(map(str, vals.values()))) > 1:
                err.append(f"урок {key}: поле {field} разошлось по языкам: {vals}")

    # Набор без урока применять некому — он не появится ни на одной странице.
    linked = {x["pack"] for langs in by_key.values() for x in langs.values() if x["pack"]}
    for p in prompts_library.PACKS:
        if p["key"] not in linked:
            err.append(f"набор {p['key']}: ни один урок на него не ссылается")

    if err:
        print("\n".join(err))
        return 1

    lessons = learn._load("en")
    with_pack = sum(1 for x in lessons if x["pack"])
    print(f"связка цела: {len(lessons)} уроков × {len(learn.LANGS)} языка, "
          f"{with_pack} с артефактом, {len(prompts_library.PACKS)} наборов, "
          f"{len(prompts_library.SHOTS)} приёмов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
