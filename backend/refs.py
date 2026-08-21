"""Виды референса: ЧТО именно берут с приложенной картинки.

Модуль существует ровно по одной причине — тексты правил нужны В ДВУХ местах
сразу: студии (референсы кадра, main.py) и мастерской (вложения строки
промпта, chat.py). Копировать их нельзя: `vibe` и `style` ПРОТИВОПОЛОЖНЫ по
отношению к грейду («не копируй свет» против «скопируй свет точно»), и стоит
двум копиям разъехаться — генератор начнёт получать взаимоисключающие
инструкции, а виноватой будет выглядеть модель.

chat.py не может импортировать main.py (тот импортирует chat), поэтому общее
место у них может быть только третьим модулем. Он намеренно пустой от
зависимостей: ни FastAPI, ни базы, ни настроек — только текст.
"""
from __future__ import annotations

# Порядок = порядок в интерфейсе: от самого частого к самому редкому.
REF_KINDS = ("vibe", "style", "place", "copy")
REF_DEFAULT = "vibe"

# Инструкция генератору по каждому виду. Тексты разные не для красоты: vibe и
# style противоположны по отношению к грейду, и смешивать их нельзя.
REF_RULES = {
    "vibe": (
        "COMPOSITION REFERENCE: take framing, camera angle, distance and the energy "
        "of the shot. Do NOT copy its colours, lighting or background."
    ),
    "style": (
        "STYLE REFERENCE: copy exactly this look — colour grade, lighting, contrast, "
        "texture and grain. Ignore its subject and composition."
    ),
    "place": (
        "LOCATION REFERENCE: this is where the shot happens. Reproduce the environment, "
        "architecture, objects and time of day. People in it are irrelevant — our own "
        "characters act in this place."
    ),
    "copy": (
        "SHOT TO REPRODUCE: recreate this frame as closely as possible — same composition, "
        "same environment, same light — replacing only the person with OUR character from "
        "the character references."
    ),
}


def ref_norm_kind(value: str | None) -> str:
    """Любой мусор снаружи → допустимый вид. Пустой вид тоже мусор: без вида
    промпт трактует картинку как композицию, и человек об этом не знает."""
    kind = str(value or "").strip().lower()
    return kind if kind in REF_KINDS else REF_DEFAULT


def ref_legend(kinds: list[str]) -> str:
    """Легенда для промпта: какая по счёту приложенная картинка чем является.

    Без нумерации модель получала пачку правил и пачку картинок, никак не
    связанных между собой, — и приклеивала стиль к локации. Порядок строк
    ОБЯЗАН совпадать с порядком картинок в запросе."""
    lines: list[str] = []
    for i, raw in enumerate(kinds, start=1):
        kind = ref_norm_kind(raw)
        lines.append(f"Reference image #{i} — {REF_RULES[kind]}")
    return "\n".join(lines)
