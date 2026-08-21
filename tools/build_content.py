#!/usr/bin/env python3
"""
tools/build_content.py — сборка SEO-контура lolq.ai.

Читает content/blog/{en,ru}/*.md и раскладывает готовые СТАТИЧЕСКИЕ страницы:

    content/blog/en/<slug>.md  ->  frontend/blog/<slug>/index.html
    content/blog/ru/<slug>.md  ->  frontend/ru/blog/<slug>/index.html
                                   frontend/blog/index.html      (лента EN)
                                   frontend/ru/blog/index.html   (лента RU)
                                   frontend/sitemap.xml

Почему статика, а не БД и не SPA:
  * StaticFiles(html=True) уже смонтирован на "/" (main.py) — каталог с
    index.html отдаётся по короткому адресу сам, без единого нового роута;
  * страницы статей НЕ грузят app.js (150 КБ) и i18n.js (140 КБ) — только
    blog.css. Лендинг рисуется джаваскриптом из словаря, и Bing с Яндексом
    его не дорендеривают; статьи обязаны быть текстом в разметке;
  * правка контента = коммит + деплой, версионирование и откат бесплатны.

Зависимостей нет: только стандартная библиотека, чтобы шаг вставал в
backend/Dockerfile без единой строки в requirements.txt.

    python3 tools/build_content.py            # собрать
    python3 tools/build_content.py --check    # только проверить цены и ссылки
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Пути переопределяются переменными окружения — тогда тот же скрипт можно
# запустить и внутри образа, где статика лежит в /app/static, а не в frontend/.
CONTENT_DIR = os.environ.get("CONTENT_DIR", os.path.join(ROOT, "content", "blog"))
FRONTEND_DIR = os.environ.get("CONTENT_OUT_DIR", os.path.join(ROOT, "frontend"))

# Канонический origin. На проде переопределяется переменной окружения —
# в sitemap и canonical обязаны попасть абсолютные адреса боевого домена.
ORIGIN = os.environ.get("CONTENT_ORIGIN", "https://qlolapp.art").rstrip("/")

SITE_NAME = "lolq.ai"
LANGS = ("en", "ru")

# Префикс адреса по языку. EN — основной, живёт в корне; RU — под /ru/.
PREFIX = {"en": "/blog", "ru": "/ru/blog"}
# Школа и каталог промтов живут по тем же правилам: EN в корне, RU под /ru/.
LEARN_PREFIX = {"en": "/learn", "ru": "/ru/learn"}
PROMPT_PREFIX = {"en": "/prompts", "ru": "/ru/prompts"}
# Каталог уроков и реестр промтов лежат вне content/: уроки в docs/learn,
# промты — в backend/prompts_catalog.py (публичная часть, без текстов).
LEARN_DIR = os.environ.get("LEARN_DIR", os.path.join(ROOT, "docs", "learn"))

UI = {
    "en": {
        "blog": "Journal",
        "home": "lolq.ai",
        "tagline": "Notes from the machine room of an AI music video studio: real model prices, working methods, honest limits.",
        "back": "← All articles",
        "cta": "Make a clip from your own track — the first one is free",
        "readmore": "Read →",
        "updated": "Updated",
        "other": "Читать по-русски",
        "learn": "School",
        "learnTag": "Everything we know about the pipeline: the free route to a first clip, styles, characters, engines, and what to do when a frame comes out wrong.",
        "prompts": "Prompts",
        "promptsTag": "A style answers how the frame looks. A story frame answers what is happening in it. Here is the whole catalogue, with what each one is for.",
        "minutes": "min read",
        "backLearn": "← All lessons",
        "backPrompts": "← All prompts",
        "usedIn": "used in {n} finished clips",
        "goodFor": "Works with",
        "mixWith": "Blends with",
        "engines": "Best engines",
        "signature": "Signature preset — the prompt text is not published",
        "openStudio": "Use this style",
    },
    "ru": {
        "blog": "Журнал",
        "home": "lolq.ai",
        "tagline": "Записки из машинного отделения студии ИИ-клипов: реальные цены моделей, рабочие приёмы, честные ограничения.",
        "back": "← Все статьи",
        "cta": "Соберите клип на свой трек — первый бесплатно",
        "readmore": "Читать →",
        "updated": "Обновлено",
        "other": "Read in English",
        "learn": "Школа",
        "learnTag": "Всё, что мы знаем про конвейер: бесплатный маршрут до первого клипа, стили, персонажи, движки и что делать, когда кадр вышел не тот.",
        "prompts": "Промты",
        "promptsTag": "Стиль отвечает, как выглядит кадр. Каркас — что в нём происходит. Здесь весь каталог и то, подо что каждый пресет сделан.",
        "minutes": "мин чтения",
        "backLearn": "← Все уроки",
        "backPrompts": "← Все промты",
        "usedIn": "применён в {n} собранных клипах",
        "goodFor": "Подходит под",
        "mixWith": "Миксуется с",
        "engines": "Лучшие движки",
        "signature": "Фирменный пресет — текст промпта не публикуется",
        "openStudio": "Снять в этом стиле",
    },
}

# ─────────────────── сторож цен: статьи против кода ───────────────────
# Цены в статьях — главный актив этих текстов и первое, что протухает.
# Витрина уже один раз разъехалась с прайсом (LD_PLANS_FALLBACK в app.js
# показывал PRO 700 очков при живых 660). Здесь тот же класс ошибки ловится
# на сборке: если backend/main.py разошёлся с этой таблицей, билд падает и
# называет, какие файлы переписать.
PRICE_FACTS = {
    "free": {"points": 120, "usd": 0},
    "pro": {"points": 660, "usd": 20},
    "pro_max": {"points": 3400, "usd": 100},
    "studio": {"points": 10500, "usd": 299},
}
# Ступени ULTRA: их цифры вбиты руками в JSON-LD лендинга и в тексты, поэтому
# сторож обязан следить и за ними. Первая ступень по определению совпадает с
# PLANS["studio"], иначе витрина и касса разойдутся на пустом месте.
TIER_FACTS = {
    "u1": {"points": 10500, "cents": 29900},
    "u2": {"points": 26000, "cents": 65900},
    "u3": {"points": 52000, "cents": 119900},
    "u4": {"points": 104000, "cents": 214900},
}
# Себестоимость сцены 6 сек в долларах — mediagen.VIDEO_ENGINES[*]["usd_6s"].
ENGINE_FACTS = {
    "grok": 0.0,
    "seedance-2-mini": 0.246,
    "seedance-2-0": 1.23,
    "seedance-2-5": 1.89,
    "seedance-2-5-480": 0.84,
    "kling-3.0": 0.42,
    "kling-3.0-pro": 0.54,
    "minimax-h3": 0.48,
}


def check_prices() -> list[str]:
    """Сверяет таблицы выше с backend/main.py и backend/mediagen.py.

    Читает файлы регулярками, а не импортом: импорт main.py поднимает всё
    приложение, включая коннект к базе и фоновые треды.
    """
    problems: list[str] = []
    try:
        main_src = open(os.path.join(ROOT, "backend", "main.py"), encoding="utf-8").read()
        media_src = open(os.path.join(ROOT, "backend", "mediagen.py"), encoding="utf-8").read()
    except OSError as e:
        return [f"не смог прочитать backend/: {e}"]

    plans_block = main_src[main_src.find("PLANS = {"):main_src.find("# Легаси-поле старого кода")]
    for pid, want in PRICE_FACTS.items():
        chunk = plans_block[plans_block.find(f'"{pid}": {{'):]
        chunk = chunk[:chunk.find('"features"')] or chunk[:2000]
        m = re.search(r'"points":\s*(\d+)', chunk)
        if not m:
            problems.append(f"PLANS[{pid}]: не нашёл points в main.py")
        elif int(m.group(1)) != want["points"]:
            problems.append(
                f"PLANS[{pid}].points = {m.group(1)}, в статьях {want['points']}")
        if want["usd"]:
            m = re.search(r'PRICE_[A-Z_]+_USD",\s*"(\d+)"', chunk)
            if m and int(m.group(1)) != want["usd"]:
                problems.append(
                    f"PLANS[{pid}] цена = ${m.group(1)}, в статьях ${want['usd']}")

    # Границу ищем ОТ начала блока: слово ULTRA_TOP_TIERS встречается и в
    # комментарии выше, и поиск с нуля давал пустой срез.
    _t0 = main_src.find("PLAN_TIERS = {")
    tiers_block = main_src[_t0:main_src.find("ULTRA_TOP_TIERS =", _t0)] if _t0 >= 0 else ""
    if not tiers_block:
        problems.append("PLAN_TIERS: не нашёл блок ступеней в main.py")
    for tid, want in TIER_FACTS.items():
        m = re.search(rf'"id":\s*"{tid}",\s*"points":\s*(\d+),\s*"usd_cents":\s*(\d+)',
                      tiers_block)
        if not m:
            problems.append(f"PLAN_TIERS[{tid}]: не нашёл ступень в main.py")
            continue
        if int(m.group(1)) != want["points"]:
            problems.append(
                f"PLAN_TIERS[{tid}].points = {m.group(1)}, здесь {want['points']}")
        if int(m.group(2)) != want["cents"]:
            problems.append(
                f"PLAN_TIERS[{tid}].usd_cents = {m.group(2)}, здесь {want['cents']}")
    if TIER_FACTS["u1"]["points"] != PRICE_FACTS["studio"]["points"]:
        problems.append("первая ступень ULTRA разошлась с PLANS['studio'].points")

    # Искать только внутри реестра ВИДЕО: "grok" есть и в IMAGE_ENGINES, и
    # в VIDEO_ENGINES, и поиск по всему файлу находит картиночный (у него
    # поля usd_6s нет вовсе).
    video_block = media_src[media_src.find("VIDEO_ENGINES: dict"):]
    for eid, want_usd in ENGINE_FACTS.items():
        chunk = video_block[video_block.find(f'"{eid}": {{'):]
        chunk = chunk[:400]
        m = re.search(r'"usd_6s":\s*([\d.]+)', chunk)
        if not m:
            problems.append(f"VIDEO_ENGINES[{eid}]: не нашёл usd_6s")
        elif abs(float(m.group(1)) - want_usd) > 1e-9:
            problems.append(
                f"VIDEO_ENGINES[{eid}].usd_6s = {m.group(1)}, в статьях {want_usd}")
    return problems


# ─────────────────────────── разбор md ───────────────────────────

def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Фронтматтер — подмножество YAML: key: "value" и key: ["a", "b"]."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    head, body = raw[3:end], raw[end + 4:]
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key.strip()] = [v.strip().strip('"\'')
                                 for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = val.strip('"\'')
    return meta, body.lstrip("\n")


_CODE_STASH: list[str] = []


def _stash_code(m: re.Match) -> str:
    _CODE_STASH.append(f"<code>{html.escape(m.group(1))}</code>")
    return f"\x00{len(_CODE_STASH) - 1}\x00"


def inline(text: str) -> str:
    """Инлайн-разметка. Порядок важен: сначала прячем код, потом всё
    остальное — иначе звёздочки внутри `code` съест жирный шрифт."""
    global _CODE_STASH
    _CODE_STASH = []
    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
                  text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    for i, code in enumerate(_CODE_STASH):
        text = text.replace(f"\x00{i}\x00", code)
    return text


def render_markdown(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Возвращает (html, оглавление). Поддержано ровно то, что используют
    статьи: h1–h3, абзацы, ul/ol, таблицы, hr. Не парсер общего назначения."""
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    lines = md.splitlines()
    i = 0
    used_ids: set[str] = set()

    def slugify(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        base = re.sub(r"[^a-z0-9а-яё]+", "-", s.lower()).strip("-") or "section"
        cand, n = base, 2
        while cand in used_ids:
            cand, n = f"{base}-{n}", n + 1
        used_ids.add(cand)
        return cand

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            level, text = len(m.group(1)), inline(m.group(2))
            hid = slugify(m.group(2))
            if level >= 2:
                toc.append((level, hid, m.group(2)))
            out.append(f'<h{level} id="{hid}">{text}</h{level}>')
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            out.append("<hr />")
            i += 1
            continue

        # таблица: строка с | , следующая — разделитель |---|
        if stripped.startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(stripped)
            i += 2
            body_rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body_rows.append(cells(lines[i].strip()))
                i += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in head)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
                for row in body_rows)
            # Обёртка обязательна: широкая таблица должна скроллиться внутри
            # себя, а не растягивать страницу вбок на телефоне.
            out.append(f'<div class="tw"><table><thead><tr>{thead}</tr>'
                       f"</thead><tbody>{tbody}</tbody></table></div>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(inline(re.sub(r"^[-*]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(inline(re.sub(r"^\d+\.\s+", "", lines[i].strip())))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        para = []
        while i < len(lines) and lines[i].strip() and \
                not re.match(r"^(#{1,3}\s|[-*]\s|\d+\.\s|\|)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    return "\n".join(out), toc


# ─────────────────────────── шаблон страницы ───────────────────────────

def page_shell(*, lang: str, title: str, description: str, canonical: str,
               alternates: dict, body: str, image: str = "",
               jsonld: list | None = None, extra_head: str = "") -> str:
    """Единая оболочка всех статических страниц.

    Здесь собрано ровно то, чего сейчас нет ни на одной странице сервиса:
    canonical, hreflang, Open Graph, Twitter Card и JSON-LD.
    """
    t = UI[lang]
    img = image or "/img/shots/step-clip.jpg"
    og_locale = {"en": "en_US", "ru": "ru_RU"}[lang]
    alt_locale = {"en": "ru_RU", "ru": "en_US"}[lang]

    links = "\n".join(
        f'<link rel="alternate" hreflang="{hl}" href="{ORIGIN}{href}" />'
        for hl, href in alternates.items())

    ld = ""
    if jsonld:
        ld = "\n".join(
            '<script type="application/ld+json">'
            + json.dumps(block, ensure_ascii=False, separators=(",", ":"))
            + "</script>" for block in jsonld)

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description, quote=True)}" />
<link rel="canonical" href="{ORIGIN}{canonical}" />
{links}
<meta property="og:type" content="article" />
<meta property="og:site_name" content="{SITE_NAME}" />
<meta property="og:title" content="{html.escape(title, quote=True)}" />
<meta property="og:description" content="{html.escape(description, quote=True)}" />
<meta property="og:url" content="{ORIGIN}{canonical}" />
<meta property="og:image" content="{ORIGIN}{img}" />
<meta property="og:locale" content="{og_locale}" />
<meta property="og:locale:alternate" content="{alt_locale}" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{html.escape(title, quote=True)}" />
<meta name="twitter:description" content="{html.escape(description, quote=True)}" />
<meta name="twitter:image" content="{ORIGIN}{img}" />
<link rel="icon" type="image/svg+xml" href="/img/logo-mark.svg" />
<link rel="stylesheet" href="/blog.css?v=1" />
{extra_head}
{ld}
</head>
<body>
<header class="bh">
  <a class="bh-brand" href="/"><img src="/img/logo-mark.svg" width="26" height="26" alt="" /><span>lolq<span class="dot">.</span>ai</span></a>
  <nav class="bh-nav">
    <a href="{PREFIX[lang]}/">{t['blog']}</a>
    <a href="/#ld-how">{'How it works' if lang == 'en' else 'Как это работает'}</a>
    <a href="/#ld-pricing">{'Pricing' if lang == 'en' else 'Тарифы'}</a>
    <a class="bh-cta" href="/">{'Start free' if lang == 'en' else 'Начать бесплатно'}</a>
  </nav>
</header>
<main class="bw">
{body}
</main>
<footer class="bf">
  <a href="/">{SITE_NAME}</a> · <a href="{PREFIX[lang]}/">{t['blog']}</a> · <a href="/music.html">qlolmusic</a>
</footer>
</body>
</html>
"""


def article_page(art: dict, sibling: dict | None) -> str:
    t = UI[art["lang"]]
    alternates = {art["lang"]: art["url"]}
    if sibling:
        alternates[sibling["lang"]] = sibling["url"]
    alternates["x-default"] = alternates.get("en", art["url"])

    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME,
             "item": f"{ORIGIN}/"},
            {"@type": "ListItem", "position": 2, "name": t["blog"],
             "item": f"{ORIGIN}{PREFIX[art['lang']]}/"},
            {"@type": "ListItem", "position": 3, "name": art["title"]},
        ],
    }
    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": art["title"][:110],
        "description": art["description"],
        "inLanguage": art["lang"],
        "datePublished": art["date"],
        "dateModified": art.get("updated") or art["date"],
        "image": f"{ORIGIN}{art.get('cover') or '/img/shots/step-clip.jpg'}",
        "mainEntityOfPage": {"@type": "WebPage", "@id": f"{ORIGIN}{art['url']}"},
        "author": {"@type": "Organization", "name": SITE_NAME,
                   "url": f"{ORIGIN}/"},
        "publisher": {"@type": "Organization", "name": SITE_NAME,
                      "url": f"{ORIGIN}/",
                      "logo": {"@type": "ImageObject",
                               "url": f"{ORIGIN}/img/logo-mark.svg"}},
    }

    toc = ""
    if len(art["toc"]) > 2:
        items = "".join(f'<li><a href="#{hid}">{html.escape(txt)}</a></li>'
                        for lvl, hid, txt in art["toc"] if lvl == 2)
        toc = f'<nav class="btoc"><ul>{items}</ul></nav>'

    switch = ""
    if sibling:
        switch = (f'<a class="blang" href="{sibling["url"]}" '
                  f'hreflang="{sibling["lang"]}">{t["other"]}</a>')

    meta_line = (f'<time datetime="{art["date"]}">{art["date"]}</time>'
                 + (f' · {t["updated"]} {art["updated"]}'
                    if art.get("updated") and art["updated"] != art["date"] else ""))

    # Оглавление встаёт ПОСЛЕ заголовка статьи, а не перед ним: h1 обязан
    # быть первым содержательным элементом страницы.
    doc = art["html"]
    cut = doc.find("</h1>")
    doc = (doc[:cut + 5] + "\n" + toc + doc[cut + 5:]) if cut >= 0 else (toc + doc)

    body = f"""<article class="bpost">
<p class="bmeta"><a href="{PREFIX[art['lang']]}/">{t['back']}</a> · {meta_line} {switch}</p>
{doc}
<aside class="bcta">
  <p>{t['cta']}</p>
  <a class="bbtn" href="/">{'Open the studio' if art['lang'] == 'en' else 'Открыть студию'}</a>
</aside>
</article>"""

    return page_shell(lang=art["lang"], title=art["title"],
                      description=art["description"], canonical=art["url"],
                      alternates=alternates, body=body, image=art.get("cover", ""),
                      jsonld=[article_ld, breadcrumb])


def index_page(lang: str, arts: list[dict]) -> str:
    t = UI[lang]
    cards = "".join(
        f'<li class="bcard"><a href="{a["url"]}"><h2>{html.escape(a["title"])}</h2>'
        f'<p>{html.escape(a["description"])}</p>'
        f'<span class="bcard-more">{t["readmore"]}</span></a></li>'
        for a in arts)
    other = "en" if lang == "ru" else "ru"
    alternates = {lang: f"{PREFIX[lang]}/", other: f"{PREFIX[other]}/",
                  "x-default": f"{PREFIX['en']}/"}
    ld = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": f"{SITE_NAME} — {t['blog']}",
        "description": t["tagline"],
        "inLanguage": lang,
        "url": f"{ORIGIN}{PREFIX[lang]}/",
    }
    body = (f'<header class="bhero"><h1>{t["blog"]}</h1>'
            f'<p>{t["tagline"]}</p></header>'
            f'<ul class="bgrid">{cards}</ul>')
    return page_shell(lang=lang, title=f"{t['blog']} — {SITE_NAME}",
                      description=t["tagline"],
                      canonical=f"{PREFIX[lang]}/", alternates=alternates,
                      body=body, jsonld=[ld])


# ═══════════════════ ШКОЛА: статические страницы уроков ═══════════════════
# Тот же шаблон и тот же рендерер, что у статей. Уровни 0–2 открыты без
# регистрации — они и есть длинный хвост органики; закрытые уровни (access
# в фронтматтере) в индекс не идут вовсе, иначе поисковик приведёт человека
# на замок.

def load_lessons() -> list[dict]:
    out: list[dict] = []
    for lang in LANGS:
        d = os.path.join(LEARN_DIR, lang)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md") or name == "index.md":
                continue
            meta, body = parse_front_matter(
                open(os.path.join(d, name), encoding="utf-8").read())
            if str(meta.get("access") or "free") != "free":
                continue      # закрытый урок в индекс не отдаём
            slug = meta.get("slug") or name[:-3]
            rendered, toc = render_markdown(body)
            out.append({
                **meta, "lang": lang, "slug": slug, "html": rendered, "toc": toc,
                "url": f"{LEARN_PREFIX[lang]}/{slug}/",
                "title": meta.get("title", slug),
                "description": meta.get("description", ""),
                "date": meta.get("date", date.today().isoformat()),
                "translationKey": meta.get("translationKey", slug),
                "level": int(meta.get("level") or 0),
                "minutes": int(meta.get("minutes") or 5),
            })
    return out


def lesson_page(item: dict, sibling: dict | None) -> str:
    t = UI[item["lang"]]
    alternates = {item["lang"]: item["url"]}
    if sibling:
        alternates[sibling["lang"]] = sibling["url"]
    alternates["x-default"] = alternates.get("en", item["url"])
    ld = {
        "@context": "https://schema.org", "@type": "LearningResource",
        "name": item["title"], "description": item["description"],
        "inLanguage": item["lang"], "datePublished": item["date"],
        "dateModified": item.get("updated") or item["date"],
        "timeRequired": f"PT{item['minutes']}M",
        "isAccessibleForFree": True,
        "learningResourceType": "tutorial",
        "url": f"{ORIGIN}{item['url']}",
        "provider": {"@type": "Organization", "name": SITE_NAME, "url": f"{ORIGIN}/"},
    }
    crumbs = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": f"{ORIGIN}/"},
            {"@type": "ListItem", "position": 2, "name": t["learn"],
             "item": f"{ORIGIN}{LEARN_PREFIX[item['lang']]}/"},
            {"@type": "ListItem", "position": 3, "name": item["title"]},
        ],
    }
    switch = ""
    if sibling:
        switch = (f'<a class="blang" href="{sibling["url"]}" '
                  f'hreflang="{sibling["lang"]}">{t["other"]}</a>')
    body = f"""<article class="bpost">
<p class="bmeta"><a href="{LEARN_PREFIX[item['lang']]}/">{t['backLearn']}</a>
 · {item['minutes']} {t['minutes']} {switch}</p>
{item['html']}
<aside class="bcta">
  <p>{t['cta']}</p>
  <a class="bbtn" href="/">{'Open the studio' if item['lang'] == 'en' else 'Открыть студию'}</a>
</aside>
</article>"""
    return page_shell(lang=item["lang"], title=f"{item['title']} — {t['learn']}",
                      description=item["description"], canonical=item["url"],
                      alternates=alternates, body=body, image=item.get("cover", ""),
                      jsonld=[ld, crumbs])


def learn_index_page(lang: str, items: list[dict]) -> str:
    t = UI[lang]
    other = "en" if lang == "ru" else "ru"
    by_level: dict = {}
    for it in items:
        by_level.setdefault(it["level"], []).append(it)
    blocks = []
    for level in sorted(by_level):
        rows = "".join(
            f'<li class="bcard"><a href="{it["url"]}"><h2>{html.escape(it["title"])}</h2>'
            f'<p>{html.escape(it["description"])}</p>'
            f'<span class="bcard-more">{it["minutes"]} {t["minutes"]} →</span></a></li>'
            for it in by_level[level])
        blocks.append(f'<ul class="bgrid">{rows}</ul>')
    alternates = {lang: f"{LEARN_PREFIX[lang]}/", other: f"{LEARN_PREFIX[other]}/",
                  "x-default": f"{LEARN_PREFIX['en']}/"}
    body = (f'<header class="bhero"><h1>{t["learn"]}</h1><p>{t["learnTag"]}</p></header>'
            + "".join(blocks))
    ld = {"@context": "https://schema.org", "@type": "Course",
          "name": f"{SITE_NAME} — {t['learn']}", "description": t["learnTag"],
          "inLanguage": lang, "url": f"{ORIGIN}{LEARN_PREFIX[lang]}/",
          "isAccessibleForFree": True,
          "provider": {"@type": "Organization", "name": SITE_NAME, "url": f"{ORIGIN}/"},
          "hasCourseInstance": {"@type": "CourseInstance",
                                "courseMode": "online",
                                "courseWorkload": f"PT{sum(i['minutes'] for i in items)}M"}}
    return page_shell(lang=lang, title=f"{t['learn']} — {SITE_NAME}",
                      description=t["learnTag"], canonical=f"{LEARN_PREFIX[lang]}/",
                      alternates=alternates, body=body, jsonld=[ld])


# ═══════════════════ ПРОМТЫ: страница-ферма на каждый стиль ═══════════════════
# 15 стилей × 2 языка = 30 индексируемых страниц из контента, который УЖЕ
# написан. Берём ровно публичную часть карточки (prompts_catalog.public_style):
# ни одного символа промпта в эти файлы попасть не может — реестр отдаёт
# по белому списку полей.

def load_prompts() -> list[dict]:
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        import prompts_catalog as pc          # noqa: PLC0415
    except Exception as e:                    # noqa: BLE001
        print(f"  ! каталог промтов не импортировался: {e}", file=sys.stderr)
        return []
    out = []
    for lang in LANGS:
        for key in pc.STYLE_KEYS:
            card = pc.public_style(key, lang=lang)
            if not card:
                continue
            out.append({
                **card, "lang": lang, "slug": key, "translationKey": f"style-{key}",
                "url": f"{PROMPT_PREFIX[lang]}/{key}/",
                "title": card["label"],
                "description": card.get("gain") or card.get("desc") or "",
                "date": date.today().isoformat(),
            })
    return out


def prompt_page(item: dict, sibling: dict | None) -> str:
    t = UI[item["lang"]]
    alternates = {item["lang"]: item["url"]}
    if sibling:
        alternates[sibling["lang"]] = sibling["url"]
    alternates["x-default"] = alternates.get("en", item["url"])
    media = item.get("media") or {}
    music = item.get("music") or {}
    rows = []
    if isinstance(music, dict) and music.get("text"):
        rows.append(f'<p><b>{t["goodFor"]}:</b> {html.escape(music["text"])}</p>')
    if item.get("mix_with"):
        rows.append(f'<p><b>{t["mixWith"]}:</b> '
                    + html.escape(", ".join(item["mix_with"])) + "</p>")
    if item.get("engines"):
        rows.append(f'<p><b>{t["engines"]}:</b> '
                    + html.escape(", ".join(item["engines"])) + "</p>")
    if item.get("prompt_class") == "closed":
        rows.append(f'<p class="bnote">{t["signature"]}</p>')
    if item.get("research"):
        rows.append(f'<p class="bnote">{html.escape(item["research"])}</p>')
    shots = "".join(
        f'<img src="{html.escape(u)}" alt="" loading="lazy" width="360" height="640" />'
        for u in (media.get("shots") or [])[:3])
    ld = {
        "@context": "https://schema.org", "@type": "CreativeWork",
        "name": item["title"], "description": item["description"],
        "inLanguage": item["lang"], "url": f"{ORIGIN}{item['url']}",
        "genre": item.get("group", ""),
        "keywords": ", ".join(item.get("tags") or []),
        "creator": {"@type": "Organization", "name": SITE_NAME, "url": f"{ORIGIN}/"},
    }
    crumbs = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": f"{ORIGIN}/"},
            {"@type": "ListItem", "position": 2, "name": t["prompts"],
             "item": f"{ORIGIN}{PROMPT_PREFIX[item['lang']]}/"},
            {"@type": "ListItem", "position": 3, "name": item["title"]},
        ],
    }
    switch = ""
    if sibling:
        switch = (f'<a class="blang" href="{sibling["url"]}" '
                  f'hreflang="{sibling["lang"]}">{t["other"]}</a>')
    body = f"""<article class="bpost">
<p class="bmeta"><a href="{PROMPT_PREFIX[item['lang']]}/">{t['backPrompts']}</a> {switch}</p>
<h1>{html.escape(item['title'])}</h1>
<p>{html.escape(item.get('desc') or '')}</p>
<p>{html.escape(item.get('gain') or '')}</p>
{''.join(rows)}
{f'<div class="bshots">{shots}</div>' if shots else ''}
<aside class="bcta">
  <p>{t['cta']}</p>
  <a class="bbtn" href="/#ld-prompts">{t['openStudio']}</a>
</aside>
</article>"""
    return page_shell(lang=item["lang"], title=f"{item['title']} — {t['prompts']}",
                      description=item["description"], canonical=item["url"],
                      alternates=alternates, body=body,
                      image=media.get("poster", ""), jsonld=[ld, crumbs])


def prompts_index_page(lang: str, items: list[dict]) -> str:
    t = UI[lang]
    other = "en" if lang == "ru" else "ru"
    cards = "".join(
        f'<li class="bcard"><a href="{it["url"]}"><h2>{html.escape(it["title"])}</h2>'
        f'<p>{html.escape(it["description"])}</p>'
        f'<span class="bcard-more">{t["readmore"]}</span></a></li>' for it in items)
    alternates = {lang: f"{PROMPT_PREFIX[lang]}/", other: f"{PROMPT_PREFIX[other]}/",
                  "x-default": f"{PROMPT_PREFIX['en']}/"}
    ld = {"@context": "https://schema.org", "@type": "CollectionPage",
          "name": f"{SITE_NAME} — {t['prompts']}", "description": t["promptsTag"],
          "inLanguage": lang, "url": f"{ORIGIN}{PROMPT_PREFIX[lang]}/"}
    body = (f'<header class="bhero"><h1>{t["prompts"]}</h1><p>{t["promptsTag"]}</p></header>'
            f'<ul class="bgrid">{cards}</ul>')
    return page_shell(lang=lang, title=f"{t['prompts']} — {SITE_NAME}",
                      description=t["promptsTag"], canonical=f"{PROMPT_PREFIX[lang]}/",
                      alternates=alternates, body=body, jsonld=[ld])


def build_sitemap(arts: list[dict], by_key: dict,
                  extra: list[dict] | None = None) -> str:
    """sitemap с двуязычными alternate-ссылками на каждом URL.

    extra — страницы школы и каталога промтов: у них тот же двуязычный
    контракт, что и у статей, поэтому и sitemap у них ОДИН. Второй sitemap
    означал бы второй набор правил и рассинхрон при первой же правке."""
    today = date.today().isoformat()
    entries: list[tuple[str, str, dict]] = []

    # Лендинг и витрина лейбла: один язык, переключается на клиенте.
    entries.append(("/", today, {}))
    entries.append(("/music.html", today, {}))
    for prefixes in (PREFIX, LEARN_PREFIX, PROMPT_PREFIX):
        for lang in LANGS:
            other = "en" if lang == "ru" else "ru"
            entries.append((f"{prefixes[lang]}/", today, {
                lang: f"{prefixes[lang]}/", other: f"{prefixes[other]}/",
                "x-default": f"{prefixes['en']}/"}))
    index = dict(by_key)
    for a in (extra or []):
        index[(a["translationKey"], a["lang"])] = a
    for a in list(arts) + list(extra or []):
        sib = index.get((a["translationKey"],
                         "en" if a["lang"] == "ru" else "ru"))
        alt = {a["lang"]: a["url"]}
        if sib:
            alt[sib["lang"]] = sib["url"]
        alt["x-default"] = alt.get("en", a["url"])
        entries.append((a["url"], a.get("updated") or a["date"], alt))

    rows = []
    for loc, lastmod, alt in entries:
        links = "".join(
            f'<xhtml:link rel="alternate" hreflang="{hl}" href="{ORIGIN}{href}"/>'
            for hl, href in alt.items())
        rows.append(f"<url><loc>{ORIGIN}{loc}</loc>"
                    f"<lastmod>{lastmod}</lastmod>{links}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    check_only = "--check" in sys.argv

    problems = check_prices()
    if problems:
        print("ЦЕНЫ В СТАТЬЯХ РАЗОШЛИСЬ С КОДОМ:", file=sys.stderr)
        for p in problems:
            print(f"  · {p}", file=sys.stderr)
        print("\nПочини содержимое статей в content/blog/ (или таблицы "
              "PRICE_FACTS/ENGINE_FACTS здесь, если прайс менялся осознанно) "
              "и собери заново.", file=sys.stderr)
        return 1

    arts: list[dict] = []
    for lang in LANGS:
        d = os.path.join(CONTENT_DIR, lang)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            meta, body = parse_front_matter(
                open(os.path.join(d, name), encoding="utf-8").read())
            slug = meta.get("slug") or name[:-3]
            rendered, toc = render_markdown(body)
            arts.append({
                **meta, "lang": lang, "slug": slug, "html": rendered, "toc": toc,
                "url": f"{PREFIX[lang]}/{slug}/",
                "title": meta.get("title", slug),
                "description": meta.get("description", ""),
                "date": meta.get("date", date.today().isoformat()),
                "translationKey": meta.get("translationKey", slug),
            })

    by_key = {(a["translationKey"], a["lang"]): a for a in arts}

    # Битые внутренние ссылки — самая частая поломка при перекладке статей.
    known = {a["url"] for a in arts} | {"/", "/music.html"} | \
            {f"{PREFIX[l]}/" for l in LANGS}
    for a in arts:
        for href in re.findall(r'href="(/[^"#]*)"', a["html"]):
            if href.startswith(("/blog/", "/ru/blog/")) and href not in known:
                print(f"  ! битая внутренняя ссылка {href} в {a['url']}",
                      file=sys.stderr)

    lessons = load_lessons()
    prompts = load_prompts()
    lessons_by = {(x["translationKey"], x["lang"]): x for x in lessons}
    prompts_by = {(x["translationKey"], x["lang"]): x for x in prompts}

    if check_only:
        print(f"проверено: {len(arts)} статей, {len(lessons)} уроков, "
              f"{len(prompts)} карточек промтов; цены сходятся с кодом")
        return 0

    for a in arts:
        sib = by_key.get((a["translationKey"],
                          "en" if a["lang"] == "ru" else "ru"))
        write(os.path.join(FRONTEND_DIR, a["url"].strip("/"), "index.html"),
              article_page(a, sib))

    for lang in LANGS:
        items = sorted([a for a in arts if a["lang"] == lang],
                       key=lambda x: x["date"], reverse=True)
        if items:
            write(os.path.join(FRONTEND_DIR, PREFIX[lang].strip("/"), "index.html"),
                  index_page(lang, items))

    for it in lessons:
        sib = lessons_by.get((it["translationKey"],
                              "en" if it["lang"] == "ru" else "ru"))
        write(os.path.join(FRONTEND_DIR, it["url"].strip("/"), "index.html"),
              lesson_page(it, sib))
    for lang in LANGS:
        items = sorted([x for x in lessons if x["lang"] == lang],
                       key=lambda x: (x["level"], x["slug"]))
        if items:
            write(os.path.join(FRONTEND_DIR, LEARN_PREFIX[lang].strip("/"), "index.html"),
                  learn_index_page(lang, items))

    for it in prompts:
        sib = prompts_by.get((it["translationKey"],
                              "en" if it["lang"] == "ru" else "ru"))
        write(os.path.join(FRONTEND_DIR, it["url"].strip("/"), "index.html"),
              prompt_page(it, sib))
    for lang in LANGS:
        items = [x for x in prompts if x["lang"] == lang]
        if items:
            write(os.path.join(FRONTEND_DIR, PROMPT_PREFIX[lang].strip("/"), "index.html"),
                  prompts_index_page(lang, items))

    write(os.path.join(FRONTEND_DIR, "sitemap.xml"),
          build_sitemap(arts, by_key, lessons + prompts))

    print(f"собрано: {len(arts)} статей, {len(lessons)} уроков, "
          f"{len(prompts)} карточек промтов, {3 * len(LANGS)} лент, "
          f"sitemap.xml → frontend/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
