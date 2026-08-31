"""Импорт библиотеки камера-движений aicameramovements.com в слой «Камера».

Одноразовый скрипт: добавляет НОВЫЕ пресеты (те, чьих аналогов нет ни в
CAMERAS, ни в MOTIONS group=camera) в наложение каталога `prompts_overlay`
(app_settings) — тем же путём, каким пишет админка /api/admin/prompts:
_overlay_setting_save + reload_prompts_overlay. Заводской файл не трогаем.

Запуск на проде (внутри контейнера qlolvideo-api):
    docker exec -e PYTHONPATH=/app qlolvideo-api \
        python3 /app/tools/import_aicameramovements.py

Идемпотентен: уже существующий ключ в наложении не перезаписывает.

Источник промптов — aicameramovements.com (46 пресетов, формула
Camera/Movement/Speed/Framing/End). Тексты адаптированы под наш стандарт:
запреты no morphing / no warping и финал «settles and holds», где уместно.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

BAN = " No morphing, no warping, no frame distortion."

# key -> (label_en, label_ru, desc_en, desc_ru, camera, bracket, text, solo,
#         physics_en, physics_ru, traits)
NEW: dict[str, tuple] = {
    "slow_zoom_in": (
        "Slow zoom in", "Медленный зум",
        "A gradual lens zoom toward a tighter frame — the camera itself never moves.",
        "Плавный наезд объективом к более тесному кадру — сама камера не двигается.",
        "slow even lens zoom in, camera position fixed",
        "[Zoom in]",
        "A slow, even lens zoom toward the main subject: the camera position stays fixed while the focal "
        "length gradually increases, the frame tightening around the subject. No dolly travel, no pan, no "
        "handheld shake — only the lens breathes in. The zoom eases out onto a stable tighter composition "
        "that settles and holds." + BAN,
        "Slow even lens zoom in on the subject from a fixed camera position, no dolly or pan, easing onto a "
        "tighter composition that settles and holds." + BAN,
        "A zoom compresses perspective: unlike a dolly, the spatial relationships flatten as the frame tightens.",
        "Зум сжимает перспективу: в отличие от долли, пространственные соотношения сплющиваются по мере сужения кадра.",
        ["slow"],
    ),
    "slow_zoom_out": (
        "Slow zoom out", "Медленное отдаление",
        "A gradual lens zoom out — the world opens around the subject while the camera stands still.",
        "Плавное отдаление объективом — мир раскрывается вокруг героя, камера стоит на месте.",
        "slow even lens zoom out, camera position fixed",
        "[Zoom out]",
        "A slow, even lens zoom out from the main subject: the camera position stays fixed while the focal "
        "length gradually decreases and surrounding space enters the frame. No dolly travel, no pan, no "
        "handheld shake. The zoom eases out onto a stable wider composition that settles and holds." + BAN,
        "Slow even lens zoom out from the subject with a fixed camera position, surrounding space entering "
        "the frame, easing onto a wider composition that settles and holds." + BAN,
        "The reveal is the story: what enters the edges of the frame recontextualizes the subject.",
        "История — в раскрытии: то, что входит в края кадра, меняет смысл героя.",
        ["slow", "wide_frame"],
    ),
    "crash_zoom_in": (
        "Crash zoom in", "Резкий зум",
        "A violent snap of the lens onto the subject — comedic or dramatic punch-in.",
        "Резкий рывок объектива на героя — комедийный или драматический панч.",
        "very fast crash zoom in, camera position fixed",
        "[Zoom in]",
        "A very fast, punchy crash zoom: the lens snaps rapidly toward the main subject in one aggressive "
        "move, the sudden scale change landing like an accent. The camera position stays fixed — no dolly, "
        "no pan. The subject stays readable through the snap, and the frame lands hard on a bold tight "
        "composition that settles and holds." + BAN,
        "Aggressive crash zoom snapping onto the subject from a fixed camera position, a bold tight "
        "composition landing hard, then settling and holding." + BAN,
        "The impact lives in the deceleration: the snap must land dead, not float past the target.",
        "Удар — в торможении: рывок должен встать намертво, а не проплыть мимо цели.",
        ["fast"],
    ),
    "crash_zoom_out": (
        "Crash zoom out", "Резкое отдаление",
        "The lens snaps violently away from the subject — an instant reveal of the surroundings.",
        "Объектив резко отскакивает от героя — мгновенное раскрытие окружения.",
        "very fast crash zoom out, camera position fixed",
        "[Zoom out]",
        "A very fast, punchy crash zoom out: the lens snaps rapidly away from the main subject, the "
        "surrounding space slamming into frame in one move. The camera position stays fixed — no dolly, no "
        "pan. The subject stays readable at the center as the world opens, and the frame lands on a bold "
        "wide composition that settles and holds." + BAN,
        "Aggressive crash zoom out from the subject, the surroundings slamming into frame, landing on a "
        "bold wide composition that settles and holds." + BAN,
        "The joke or the dread is in what was just off frame: the reveal must be instant and total.",
        "Шутка или ужас — в том, что было прямо за кадром: раскрытие должно быть мгновенным и полным.",
        ["fast", "wide_frame"],
    ),
    "pedestal_up": (
        "Pedestal up", "Подъём камеры",
        "The whole camera lifts straight up, lens level — the framing rises without tilting.",
        "Вся камера поднимается строго вертикально, объектив горизонтален — кадр растёт без наклона.",
        "pedestal up, lens level, no tilt",
        "[Pedestal up]",
        "A smooth vertical pedestal move: the entire camera lifts straight upward at constant speed while "
        "the lens stays level and pointed in the same direction — strictly no tilt, no pan, no zoom. The "
        "framing rises past the subject, revealing what sits above the original composition. The lift "
        "decelerates and the higher framing settles and holds." + BAN,
        "Smooth vertical pedestal lift at constant speed, lens level with no tilt or pan, the higher "
        "framing decelerating to settle and hold." + BAN,
        "A pedestal differs from a tilt: the horizon stays put while the camera itself gains height.",
        "Педестал отличается от тилта: горизонт стоит на месте, высоту набирает сама камера.",
        ["moving_camera", "slow"],
    ),
    "orbit_clockwise": (
        "Orbit", "Орбита вокруг героя",
        "A ground-level circle around the subject at constant radius — the world rotates behind them.",
        "Круг вокруг героя на уровне земли с постоянным радиусом — мир вращается за спиной.",
        "smooth clockwise orbit around the subject, constant radius",
        "",
        "A smooth clockwise orbit around the main subject at a consistent radius and height: the subject "
        "stays centered and stable in frame while the entire background rotates around them. Constant "
        "controlled speed — no zoom, no radius drift, no handheld shake. The orbit completes its arc, "
        "decelerates, and the framing settles and holds on the strongest angle." + BAN,
        "Smooth clockwise orbit around the subject at constant radius, subject centered while the "
        "background rotates, the arc decelerating to settle and hold." + BAN,
        "The subject is the axis: any drift in radius or height reads instantly as an error.",
        "Герой — ось вращения: любой дрейф радиуса или высоты мгновенно читается как ошибка.",
        ["moving_camera"],
    ),
    "side_tracking": (
        "Side tracking", "Боковой тревеллинг",
        "The camera travels parallel beside the moving subject, holding a profile at stable distance.",
        "Камера едет параллельно движущемуся герою, держа профиль на постоянной дистанции.",
        "side tracking parallel to the subject at matched speed",
        "[Tracking shot]",
        "A side tracking shot: the camera moves parallel beside the subject along their direction of "
        "travel, speed locked to theirs, holding them in side or three-quarter profile at a stable "
        "distance. The environment streams past in layers of parallax — no pan, no zoom, no drifting "
        "closer. At the end the pace steadies and the moving composition settles into a clean parallel "
        "glide that holds." + BAN,
        "Side tracking shot parallel to the moving subject at matched speed, stable profile framing, "
        "layered parallax streaming past, settling into a clean glide that holds." + BAN,
        "Matched speed sells it: the subject is pinned in frame while three layers of world slide past.",
        "Продаёт синхронная скорость: герой приколот в кадре, а три слоя мира скользят мимо.",
        ["moving_camera"],
    ),
    "reverse_tracking": (
        "Reverse tracking", "Обратный тревеллинг",
        "The camera retreats in front of the walking subject — the walk-and-talk frame.",
        "Камера отступает перед идущим героем — кадр «walk-and-talk».",
        "reverse tracking, camera leading the walking subject",
        "[Tracking shot]",
        "A reverse tracking shot: the camera moves backward directly in front of the walking subject, "
        "matching their forward pace so the front-facing framing of face and body stays stable while the "
        "background recedes behind them. Smooth and level — no pan, no zoom, no bobbing. The walk eases to "
        "a stop and the front-facing composition settles and holds." + BAN,
        "Reverse tracking shot leading the walking subject, stable front-facing framing as the background "
        "recedes, easing to a stop where the composition settles and holds." + BAN,
        "The face is the anchor: it must stay pinned while everything behind it flows away.",
        "Якорь — лицо: оно пришпилено в кадре, пока всё за спиной утекает назад.",
        ["moving_camera"],
    ),
    "low_tracking": (
        "Low tracking", "Низкий тревеллинг",
        "A ground-level tracking move alongside footsteps or wheels — the floor becomes the stage.",
        "Тревеллинг на уровне земли рядом с шагами или колёсами — сцена становится полом.",
        "low tracking at ground height, matched to the subject",
        "[Tracking shot]",
        "A low tracking shot at ground or below-waist height, moving alongside the subject's path with "
        "speed matched to their footsteps or wheels: the low detail stays sharp and readable while the "
        "ground plane rushes through the frame. Level and steady — no tilt up, no zoom. The move "
        "decelerates and the low perspective settles and holds." + BAN,
        "Low tracking shot at ground height matched to the subject's pace, the ground plane rushing "
        "through frame, decelerating until the low perspective settles and holds." + BAN,
        "Proximity to the ground multiplies perceived speed: the closer the floor, the faster the shot feels.",
        "Близость к земле умножает ощущение скорости: чем ближе пол, тем быстрее кажется кадр.",
        ["moving_camera", "fast"],
    ),
    "chase_shot": (
        "Chase shot", "Погоня",
        "A fast, reactive pursuit of the moving subject — close, energetic, slightly imperfect.",
        "Быстрое реактивное преследование героя — близко, энергично, чуть несовершенно.",
        "fast reactive chase behind the moving subject",
        "[Tracking shot]",
        "A chase shot: the camera pursues the fast-moving subject along their route, physically close and "
        "reactive, allowing small energetic reframings as the action turns — but always keeping the "
        "subject visible and readable. Real pursuit inertia, slight lag on the corners, no zoom, no cuts. "
        "At the end the chase catches up, the pace bleeds off, and the framing settles and holds." + BAN,
        "Fast reactive chase shot close behind the moving subject, energetic reframing with real pursuit "
        "inertia, the chase catching up until the framing settles and holds." + BAN,
        "The lag is the life: a chase that corners perfectly reads as a rail, not a pursuit.",
        "Жизнь — в отставании: погоня, идеально входящая в повороты, читается как рельсы, а не преследование.",
        ["moving_camera", "fast", "handheld"],
    ),
    "push_past": (
        "Push past", "Проход сквозь передний план",
        "The camera glides forward past a foreground edge or opening into the space beyond.",
        "Камера скользит вперёд мимо переднего плана — края, проёма — в пространство за ним.",
        "forward glide past a foreground object into the space beyond",
        "[Push in]",
        "A push-past move: the camera glides smoothly forward past a visible foreground object, edge or "
        "opening — the foreground sweeping close by the lens, soft and large — while the space beyond "
        "resolves into clarity. One continuous forward path, no pan, no zoom. The camera arrives inside "
        "the revealed space, decelerates, and the new framing settles and holds." + BAN,
        "Smooth forward glide past a close foreground edge into the space beyond, the foreground sweeping "
        "by the lens, arriving in the revealed space where the framing settles and holds." + BAN,
        "The foreground brushing the lens is the doorway: its blur and speed sell the depth of the move.",
        "Дверь — это передний план у самой линзы: его смаз и скорость продают глубину прохода.",
        ["moving_camera"],
    ),
    "snorricam": (
        "Snorricam", "Снорикам",
        "A body-mounted camera locked to the subject: they stay pinned while the world lurches around them.",
        "Камера на теле героя: он приколот в кадре, а мир шатается вокруг.",
        "body-mounted snorricam locked to the subject's torso",
        "",
        "A body-mounted Snorricam shot: the camera is rigidly fixed relative to the subject's torso and "
        "face, so the subject stays close, centered and locked in frame while the entire background sways, "
        "lurches and rotates with their every step. The disorientation belongs to the world, not the "
        "subject — their framing never drifts. At the end the subject stops moving and the locked framing "
        "settles and holds." + BAN,
        "Snorricam shot with the subject locked centered in frame while the background lurches and rotates "
        "with their movement, ending as they stop and the framing settles and holds." + BAN,
        "The inversion is the effect: the subject is the still point and the world does the staggering.",
        "Эффект — в инверсии: герой — неподвижная точка, а шатается сам мир.",
        ["moving_camera", "handheld"],
    ),
    "drone_pull_back": (
        "Drone pull back", "Дрон-отлёт",
        "A smooth aerial retreat: the subject shrinks as the landscape opens wide around them.",
        "Плавный отлёт дрона: герой уменьшается, вокруг раскрывается ландшафт.",
        "smooth backward drone flight away from the subject",
        "[Pull out]",
        "A smooth aerial drone pull-back: the camera flies backward and slightly upward away from the "
        "subject in one controlled retreat, the subject staying readable at the center while more and more "
        "landscape opens around them. Real flight inertia, gentle acceleration — no zoom, no cuts. The "
        "retreat eases off and the wide aerial composition settles and holds." + BAN,
        "Controlled backward drone flight away from the subject, the landscape opening wide around them, "
        "easing into an aerial composition that settles and holds." + BAN,
        "Scale is the payoff: the subject must shrink smoothly, never popping between sizes.",
        "Награда — масштаб: герой обязан уменьшаться плавно, без скачков размера.",
        ["moving_camera", "wide_frame"],
    ),
    "fpv_first_person": (
        "First-person view", "От первого лица",
        "The camera is the character's eyes: hands and body edges anchor the viewer inside the scene.",
        "Камера — глаза героя: руки и края тела прописывают зрителя внутрь сцены.",
        "first-person view at eye height, hands visible as anchor",
        "",
        "A first-person point-of-view shot: the camera moves forward at human eye height from the "
        "character's own perspective, at a natural walking or reaching pace, with visible hands, arms or "
        "body edges anchoring the viewer physically in the scene. Natural head-motion sway, no cuts, no "
        "zoom. The move arrives at the next point of action from the same perspective and the view settles "
        "and holds." + BAN,
        "First-person POV moving forward at eye height with visible hands as the anchor, natural sway, "
        "arriving at the point of action where the view settles and holds." + BAN,
        "The hands make it first-person: without a body edge in frame it reads as just a dolly.",
        "От первого лица делают руки: без края тела в кадре это читается как обычный долли.",
        ["moving_camera", "handheld"],
    ),
    "tilt_shift_miniature": (
        "Tilt-shift", "Тилт-шифт миниатюра",
        "A high view with a narrow band of focus: the real world turns into a toy diorama.",
        "Высокая точка с узкой полосой резкости: реальный мир превращается в игрушечную диораму.",
        "high angled tilt-shift view, narrow focal band",
        "",
        "A tilt-shift miniature shot from a high angled vantage over the scene: a narrow horizontal band "
        "of sharp focus crosses the key subject area while everything above and below falls into soft "
        "creamy blur, making the world read as a hand-built miniature diorama. The camera holds or glides "
        "minutely — small precise movement only, no zoom. The miniature-scale view settles and holds." + BAN,
        "High tilt-shift view with a narrow band of sharp focus across the subject and soft blur above and "
        "below, the scene reading as a miniature diorama, settling and holding." + BAN,
        "The blur gradient does the trick: our eyes read shallow focus at scale as tiny physical objects.",
        "Фокус-градиент и есть фокус: глаз читает малую глубину резкости на масштабе как крошечные предметы.",
        ["slow", "wide_frame"],
    ),
    "infinite_zoom": (
        "Infinite zoom", "Бесконечный зум",
        "A continuous accelerating zoom into a centered target that opens into the next world.",
        "Непрерывный ускоряющийся зум в центр кадра, раскрывающийся в следующий мир.",
        "continuous accelerating zoom into the exact center",
        "",
        "An infinite zoom: the lens zooms continuously inward toward the exact center of the frame, "
        "smoothly accelerating, the centered target expanding until a next visual world opens up and fills "
        "the frame from inside it. The target stays locked dead center throughout — no drift, no pan, no "
        "cuts. The zoom eases off once the new world fills the frame, and the view settles and holds." + BAN,
        "Continuous accelerating zoom into the locked center of frame until the next visual world opens "
        "and fills it, then easing off to settle and hold." + BAN,
        "Center lock is everything: a millimeter of drift breaks the illusion of falling inward.",
        "Всё решает центровка: миллиметр дрейфа ломает иллюзию падения внутрь кадра.",
        ["fast"],
    ),
    "earth_zoom_out": (
        "Earth zoom out", "Отлёт до планеты",
        "A rapid pull from street level through city and landscape up to planetary scale.",
        "Стремительный отлёт от уличного плана через город и ландшафт до масштаба планеты.",
        "rapid vertical zoom out from street to planet scale",
        "",
        "An earth zoom out: the view pulls rapidly upward and away from the starting point, expanding "
        "through street scale, city scale, landscape scale and finally planetary scale, the original "
        "location staying implied at the exact center of the frame throughout the entire climb. One "
        "continuous accelerating pull, no cuts, no lateral drift. The pull eases off on the planet-scale "
        "view, which settles and holds." + BAN,
        "Rapid continuous zoom out from street level to planet scale with the starting point locked at "
        "center, easing off on the planetary view that settles and holds." + BAN,
        "Each scale must hand off believably to the next: streets into blocks, blocks into coastline, "
        "coastline into the curve of the planet.",
        "Каждый масштаб обязан правдоподобно передать эстафету следующему: улицы — кварталам, кварталы — "
        "побережью, побережье — изгибу планеты.",
        ["fast", "wide_frame"],
    ),
    "timelapse_locked": (
        "Time-lapse", "Таймлапс",
        "A locked camera while time races: light, crowds and weather pour through a fixed frame.",
        "Камера заперта, время несётся: свет, толпы и погода текут сквозь неподвижный кадр.",
        "locked-off camera, rapid time-lapse",
        "[Static shot]",
        "A locked-camera time-lapse: the camera holds one fixed position, angle and composition for the "
        "entire clip while time moves rapidly forward through the frame — light sweeping, shadows "
        "rotating, clouds streaming, crowds flowing as motion trails. The horizon and framing never move. "
        "The time compression eases at the end and the final state of the scene settles and holds in the "
        "same composition." + BAN,
        "Locked-off time-lapse with light sweeping and motion streaming through a fixed composition, time "
        "easing off at the end as the final state settles and holds." + BAN,
        "The stillness of the frame is what makes the speed of time legible.",
        "Скорость времени читается только благодаря абсолютной неподвижности кадра.",
        ["slow", "wide_frame"],
    ),
    "pass_through": (
        "Pass-through", "Пролёт сквозь предмет",
        "The camera glides into a surface, object or opening and emerges in the space beyond.",
        "Камера влетает в поверхность, предмет или проём и выныривает в пространстве за ним.",
        "smooth centered glide forward through an object into the space beyond",
        "[Push in]",
        "A pass-through move: the camera glides smoothly forward toward a visible object, surface or "
        "opening — a keyhole, a window, a screen, a crack — keeping the transition point locked at center, "
        "then continues straight through it into the space beyond. One continuous centered glide, no pan, "
        "no cuts; the pass must read as travel through, not a dissolve. Beyond the threshold the camera "
        "decelerates and the revealed space settles and holds." + BAN,
        "Smooth centered glide forward through an opening or surface into the space beyond, the pass "
        "reading as continuous travel, decelerating until the revealed space settles and holds." + BAN,
        "The threshold must scale believably as it approaches: the pass sells depth, not a cut.",
        "Порог обязан правдоподобно расти при приближении: пролёт продаёт глубину, а не склейку.",
        ["moving_camera"],
    ),
}


def build_rows() -> dict[str, dict]:
    rows = {}
    for key, (len_, lru, den, dru, camera, bracket, text, solo, phen, phru, traits) in NEW.items():
        rows[key] = {
            "label": {"en": len_, "ru": lru},
            "desc": {"en": den, "ru": dru},
            "tier": "free", "group": "presets",
            "camera": camera, "bracket": bracket,
            "physics": {"en": phen, "ru": phru},
            "text": text, "solo": solo,
        }
    return rows


def main() -> None:
    import main as core  # backend/main.py внутри контейнера (PYTHONPATH=/app/backend)
    import prompts_library as pl
    from db import SessionLocal

    db = SessionLocal()
    try:
        data = core._overlay_setting(db, core.PROMPTS_OVERLAY_KEY)
        data.setdefault("cameras", {})
        existing = set(pl._BUILTIN_BY_KEY_LAYER["cameras"]) | set(data["cameras"])
        added, skipped = [], []
        for key, row in build_rows().items():
            if key in existing:
                skipped.append(key)
                continue
            data["cameras"][key] = row
            added.append(key)
        if added:
            core._overlay_setting_save(db, core.PROMPTS_OVERLAY_KEY, data)
            core.reload_prompts_overlay(db)
        print(f"добавлено {len(added)}: {', '.join(added) or '—'}")
        print(f"пропущено (уже есть) {len(skipped)}: {', '.join(skipped) or '—'}")
        print(f"итого в слое «Камера»: {len(pl.CAMERAS)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
