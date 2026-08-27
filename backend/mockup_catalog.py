# ─────────────── Каталог шаблонов мокапов (маркетинг-студия) ───────────────
# Готовые предметные сцены: человек загружает фото товара, выбирает шаблон —
# и получает кадр по промпту сцены. Продукт в каждом промпте описан как
# «the exact product from the reference photo» — этикетка и пропорции обязаны
# совпасть с фото, сцена меняется, товар нет.
#
# Поля шаблона:
#   id        — стабильный ключ (пишется в счёт и в промпт сцены);
#   ru / en   — название для карточки;
#   category  — фильтр витрины: product | ugc | ads | poster | shelf | motion;
#   tara      — какой таре сцена идёт лучше всего (подпись на карточке):
#               bottle | jar | tube | box | pouch | cup | any;
#   emoji     — заглушка карточки, пока превью не сгенерировано;
#   prompt    — полный английский промпт сцены (постановка, свет, ракурс);
#   motion    — сцена явно просится в анимацию (кнопка «оживить» сразу);
#   showcase  — превью этого шаблона генерируется первым (витрина из 6).

PRODUCT_CLAUSE = (
    "The product is the exact product from the reference photo: keep its "
    "shape, proportions, cap, material and every label letter, logo and "
    "color exactly as in the reference, no redesign, no invented text. "
)

# Нейтральная бутылка для превью каталога — превью показывают СЦЕНУ,
# а не чей-то товар.
PREVIEW_PRODUCT = (
    "A generic unbranded amber glass bottle with a plain matte black cap "
    "and a blank cream label with no text. "
)

TEMPLATES: list[dict] = [
    # ── product shots ──
    {"id": "studio_podium", "ru": "Студийный подиум", "en": "Studio podium",
     "category": "product", "tara": "any", "emoji": "🏛️", "showcase": True,
     "prompt": "Professional studio product photography on a round matte beige podium, seamless warm off-white background, soft diffused key light from the upper left, gentle contact shadow under the product, subtle gradient falloff behind, eye-level camera, 85mm lens look, centered composition with generous negative space, no props."},
    {"id": "marble_sprig", "ru": "Мрамор и веточка", "en": "Marble & sprig",
     "category": "product", "tara": "jar", "emoji": "🌿",
     "prompt": "Elegant product photography on a white Carrara marble surface, a single fresh eucalyptus sprig lying beside the product, soft window daylight from the right casting long soft shadows, light airy background slightly out of focus, camera at 30 degrees above eye level, shallow depth of field, clean minimal spa aesthetic."},
    {"id": "water_splash", "ru": "Всплеск воды", "en": "Water splash",
     "category": "product", "tara": "bottle", "emoji": "💦", "motion": True,
     "prompt": "High-speed splash photography: the product standing in shallow crystal-clear water with a dynamic crown-shaped splash frozen around its base, fine droplets suspended in the air, cool fresh blue-white gradient background, hard backlight rimming the droplets, low camera angle at product level, crisp macro detail."},
    {"id": "ice_hover", "ru": "Лёд и ягоды", "en": "Ice cube hover",
     "category": "product", "tara": "jar", "emoji": "🧊", "showcase": True,
     "prompt": "The product hovering a few centimeters above a large crystal-clear ice cube with fresh red berries frozen inside it, soft pastel pink seamless background, cool studio light with a warm accent, tiny frost particles in the air, slight low camera angle, glossy reflections on the ice, premium cosmetic ad style."},
    {"id": "silk_wave", "ru": "Шёлковая ткань", "en": "Silk drape",
     "category": "product", "tara": "tube", "emoji": "🎀",
     "prompt": "Luxury product photography: the product resting on flowing champagne-colored silk fabric with soft sculpted folds, warm golden side light grazing the fabric texture, dark warm vignette background, camera slightly above eye level, rich highlights on the silk, intimate premium mood."},
    {"id": "sand_sun", "ru": "Песок и солнце", "en": "Desert sun",
     "category": "product", "tara": "bottle", "emoji": "🏜️",
     "prompt": "The product standing upright on rippled warm sand dunes, hard midday sunlight from high right creating a crisp dark shadow, clear gradient sky from deep blue to pale horizon, heat-haze shimmer far away, low wide-angle camera close to the sand, summer editorial energy."},
    {"id": "mirror_floor", "ru": "Зеркальная гладь", "en": "Mirror floor",
     "category": "product", "tara": "any", "emoji": "🪞",
     "prompt": "The product standing on a perfectly reflective black mirror surface, its symmetric reflection below, dark charcoal gradient background with a soft halo of light behind the product, two narrow strip lights carving glossy vertical highlights on its sides, exact frontal eye-level camera, dramatic premium tech-ad look."},
    {"id": "flatlay_top", "ru": "Флэтлей сверху", "en": "Top flat lay",
     "category": "product", "tara": "any", "emoji": "🔝",
     "prompt": "Top-down flat lay photography: the product lying centered on a warm textured linen cloth, arranged around it a few minimal props — a wooden spoon, dried flowers, scattered oats — soft even daylight, gentle shadows, camera exactly overhead at 90 degrees, balanced airy composition, editorial lifestyle magazine style."},
    {"id": "olive_drop", "ru": "Капля в масле", "en": "Silky drop",
     "category": "product", "tara": "bottle", "emoji": "🫒", "motion": True,
     "prompt": "Macro liquid photography: a single drop falling into silky golden-olive liquid next to the product, perfect circular ripples, the product standing tall behind the ripple slightly out of focus, warm amber backlight glowing through the liquid, extreme close-up low angle, ultra sharp droplet frozen mid-air."},
    {"id": "cream_open", "ru": "Открытая банка", "en": "Open jar luxe",
     "category": "product", "tara": "jar", "emoji": "🤍",
     "prompt": "Luxury beauty still life: the product jar open with a perfect whipped-cream swirl texture inside, the lid leaning beside it, both standing on a closed hardcover book in warm tones, two thin gold rings placed nearby, soft warm side light, creamy beige background, 45-degree camera angle, rich tactile detail."},
    {"id": "frost_stock", "ru": "Морозный сток", "en": "Frost stock",
     "category": "product", "tara": "jar", "emoji": "❄️",
     "prompt": "Several units of the product buried up to their base in crushed ice inside a metal tub, cold vapor drifting over the ice, tiny frost crystals on the packaging, steely blue light with a soft top key, tight camera at ice level looking slightly down, fresh chilled beverage-counter mood."},
    # ── ugc ──
    {"id": "hands_hold", "ru": "Товар в руках", "en": "In hands",
     "category": "ugc", "tara": "any", "emoji": "🤲", "showcase": True,
     "prompt": "Lifestyle UGC shot: two well-groomed hands gently holding the product toward the camera, softly blurred cozy living room in warm daylight behind, natural skin tones, shallow depth of field focused on the label, slightly above eye-level phone-camera framing, authentic casual feel."},
    {"id": "freckles_face", "ru": "Девушка с веснушками", "en": "Freckles smile",
     "category": "ugc", "tara": "any", "emoji": "😊",
     "prompt": "UGC beauty portrait: a happy young woman with natural freckles and light makeup holding the product next to her cheek, warm yellow seamless background, bright natural daylight from the front, genuine smile looking into the camera, chest-up framing, phone-photo realism, the label facing the camera."},
    {"id": "pool_hand", "ru": "Рука у бассейна", "en": "Poolside hand",
     "category": "ugc", "tara": "pouch", "emoji": "🏊",
     "prompt": "Summer UGC shot: a hand holding the product up in the foreground, behind it a sunlit swimming pool with colorful inflatable toys floating, sparkling water reflections, hard summer sun, saturated cheerful colors, slightly tilted candid phone framing, vacation vibe."},
    {"id": "wet_glow", "ru": "Мокрые волосы", "en": "Wet glow",
     "category": "ugc", "tara": "tube", "emoji": "💧",
     "prompt": "Glossy beauty UGC: a model with slicked wet hair pressing the product against her cheek, dewy glowing skin with water droplets, warm yellow studio background, punchy direct flash-like light, tight head-and-shoulders crop, editorial-meets-selfie energy, label readable."},
    {"id": "sun_splash", "ru": "Солнце и брызги", "en": "Sun & splash",
     "category": "ugc", "tara": "jar", "emoji": "☀️",
     "prompt": "Hard-light summer UGC: a young woman by the water in bright direct sunlight holding the product can next to her face, water splashes frozen around, deep shadows and bright highlights on the skin, squinting joyful expression, blue water background, close phone-camera crop."},
    {"id": "streetwear_jacket", "ru": "Стритвир-дроп", "en": "Streetwear drop",
     "category": "ugc", "tara": "jar", "emoji": "🧢",
     "prompt": "Streetwear UGC: a girl in a baseball cap and white sunglasses pulling the product out from inside her open puffer jacket like a secret drop, clear deep-blue sky background, hard sunlight, low slightly tilted camera angle, bold fashion-editorial attitude, label toward the camera."},
    {"id": "denim_sky", "ru": "Взгляд снизу", "en": "Denim sky",
     "category": "ugc", "tara": "jar", "emoji": "🕶️",
     "prompt": "UGC portrait shot from above: a young man in a denim jacket looking up into the camera, holding the product can up beside his face, open sky with light clouds behind him, natural daylight, wide phone lens perspective, confident relaxed expression."},
    {"id": "bathroom_selfie", "ru": "Селфи в ванной", "en": "Bathroom selfie",
     "category": "ugc", "tara": "any", "emoji": "🪞",
     "prompt": "Mirror selfie UGC: a girl in a bathrobe taking a mirror selfie in a stylish bathroom, holding the product up next to her face, soft pink LED light strip glow, marble sink with skincare bottles slightly blurred, phone visible in the mirror, cozy evening routine mood."},
    # ── ads ──
    {"id": "gold_luxe", "ru": "Золотой люкс", "en": "Golden luxe",
     "category": "ads", "tara": "box", "emoji": "✨", "showcase": True,
     "prompt": "High-end advertising shot: the product on a polished black stone pedestal surrounded by floating thin golden ribbons and fine gold particles, deep warm charcoal background, dramatic golden rim light from behind, soft key from the front, slight low camera angle to make the product heroic, cinematic luxury commercial style."},
    {"id": "pillow_hug", "ru": "Пижамный уют", "en": "Pillow hug",
     "category": "ads", "tara": "any", "emoji": "🛏️",
     "prompt": "Cozy studio ad: a girl in soft pastel pajamas hugging an oversized fluffy pillow, the product held in her hands on top of the pillow, seamless warm cream background, soft wrapping light, gentle sleepy smile, centered symmetrical composition, sleep-and-care campaign mood."},
    {"id": "neon_cyber", "ru": "Неон-кибер", "en": "Neon cyber",
     "category": "ads", "tara": "bottle", "emoji": "🌃",
     "prompt": "Cyberpunk advertising scene: the product on a wet reflective street surface at night, magenta and cyan neon signs glowing out of focus behind, thin neon light strips reflecting on the packaging, light haze in the air, low dramatic camera angle, high contrast futuristic mood."},
    # ── posters ──
    {"id": "paper_wave", "ru": "Бумажная волна", "en": "Paper wave",
     "category": "poster", "tara": "any", "emoji": "🌊", "showcase": True,
     "prompt": "Paper-art poster: the product carried on the crest of a stylized ocean wave built entirely from layered cut paper in teal and cream tones, origami foam curls, paper clouds and a paper sun in the background, soft even studio light with crisp layered shadows, frontal camera, playful premium craft aesthetic."},
    {"id": "levitation", "ru": "Левитация", "en": "Levitation",
     "category": "poster", "tara": "any", "emoji": "🪐",
     "prompt": "The product levitating in mid-air surrounded by slowly orbiting ingredient particles — petals, herbs, droplets — soft beige-to-cream gradient background, gentle studio light with a faint glow behind the product, perfectly centered frontal composition, calm weightless minimal poster look."},
    {"id": "zero_gravity", "ru": "Невесомость", "en": "Zero gravity",
     "category": "poster", "tara": "any", "emoji": "🎈",
     "prompt": "Minimalist poster: the product floating slightly tilted in an empty warm beige space, one soft diffused shadow cast on the back wall, no props at all, generous negative space around, soft top light, frontal camera, quiet gallery-print minimalism."},
    {"id": "pastel_room", "ru": "Пастельная 3D-комната", "en": "Pastel 3D room",
     "category": "poster", "tara": "any", "emoji": "🧸",
     "prompt": "Cute 3D-render style poster: the product standing on a podium inside a pastel pink-and-mint toy room with rounded geometric shapes, arches and soft stairs, matte clay-like materials everywhere except the product itself which stays photoreal, soft ambient light, centered wide shot, playful designer render mood."},
    {"id": "neon_room", "ru": "Фиолетовая 3D-комната", "en": "Violet 3D room",
     "category": "poster", "tara": "any", "emoji": "🔮",
     "prompt": "Neon-violet 3D room poster: several units of the product arranged on glossy purple podiums among lush green tropical plants, saturated purple ambient light with pink neon accents, reflective floor, symmetrical wide composition, futuristic showroom render style, products stay photoreal."},
    {"id": "moss_forest", "ru": "Мох и лес", "en": "Forest moss",
     "category": "poster", "tara": "jar", "emoji": "🌲",
     "prompt": "Nature poster: the product standing on a mound of vivid green moss with small ferns and mushrooms around, dark misty forest background with sun rays breaking through, dew drops on the moss, soft cool light with a warm sun accent, close low camera angle, organic cosmetics mood."},
    {"id": "grunge_concrete", "ru": "Гранж-бетон", "en": "Raw concrete",
     "category": "poster", "tara": "tube", "emoji": "🧱",
     "prompt": "Brutalist poster: the product on a raw cracked concrete block, textured grey concrete wall behind, one hard directional light from the side throwing a long sharp shadow, faint dust in the light beam, slightly low camera angle, monochrome palette that makes the packaging colors pop."},
    # ── shelf / marketplace ──
    {"id": "store_shelf", "ru": "Полка магазина", "en": "Store shelf",
     "category": "shelf", "tara": "box", "emoji": "🛒", "showcase": True,
     "prompt": "Retail shelf shot: rows of the product neatly fronted on a bright supermarket shelf, price-tag rails visible but blank, clean even retail lighting, shallow depth of field with neighboring shelves blurred, eye-level camera as a shopper would see it, the front row perfectly sharp."},
    {"id": "fridge_rows", "ru": "Полка холодильника", "en": "Fridge rows",
     "category": "shelf", "tara": "jar", "emoji": "🧃",
     "prompt": "Open fridge shelf packed with rows of the product cans, fresh fruit and greens tucked between them, cool white fridge light with slight condensation on the packaging, tight frontal camera framing the loaded shelf, crisp appetizing freshness."},
    {"id": "shelf_reach", "ru": "Рука к полке", "en": "Shelf reach",
     "category": "shelf", "tara": "jar", "emoji": "🫳",
     "prompt": "A shopper's hand reaching toward a row of the product on a store shelf, the chosen unit slightly pulled forward, warm store lighting, blurred aisle background with soft bokeh, camera just behind the shoulder at shelf height, natural documentary retail moment."},
    # ── motion ──
    {"id": "turntable_spin", "ru": "Вращение 360°", "en": "Turntable spin",
     "category": "motion", "tara": "any", "emoji": "🔄", "motion": True,
     "prompt": "Studio product hero frame made to be animated: the product perfectly centered on a glossy rotating turntable disc, seamless graphite background, two symmetric strip lights carving clean highlights, faint motion streaks of light hinting at rotation, exact eye-level frontal camera, premium launch-video look."},
    {"id": "petal_storm", "ru": "Вихрь лепестков", "en": "Petal storm",
     "category": "motion", "tara": "bottle", "emoji": "🌸", "motion": True,
     "prompt": "Cinematic frame made to be animated: the product standing firm while a swirling vortex of pink flower petals spins around it, petals frozen mid-flight at different distances, warm cream background, soft key light plus backlight catching petal edges, slight low angle, dynamic beauty-commercial energy."},
]

_BY_ID = {t["id"]: t for t in TEMPLATES}

CATEGORIES = ("product", "ugc", "ads", "poster", "shelf", "motion")


def get(template_id: str) -> dict | None:
    return _BY_ID.get((template_id or "").strip())


def scene_prompt(tpl: dict) -> str:
    """Промпт кадра по шаблону: сцена + жёсткая охрана этикетки."""
    return PRODUCT_CLAUSE + tpl["prompt"]


def preview_prompt(tpl: dict) -> str:
    """Промпт превью каталога: та же сцена, но с нейтральной бутылкой."""
    return PREVIEW_PRODUCT + tpl["prompt"]


# ═════════════════════════════════════════════════════════════════════════════
# НАЛОЖЕНИЕ ВЛАДЕЛЬЦА (админка → «Промты» → вкладка «Шаблоны мокапов»)
#
# Тот же приём, что у стилей и у слоёв промтов: файл остаётся источником и
# живёт в git-истории, правки владельца лежат отдельным слоем в app_settings
# (ключ `mockup_overlay`) и накладываются сверху. Своя карточка (ключа нет в
# файле) собирается из скелета — тогда в TEMPLATES она просто добавляется.
# ═════════════════════════════════════════════════════════════════════════════

#: Что вообще можно перекрыть. `id` не редактируется: он пишется в счёт и в
#: превью, и переименование ключа означало бы потерю и того и другого.
EDITABLE = ("ru", "en", "category", "tara", "emoji", "prompt", "motion", "showcase")

_BUILTIN: list[dict] = [dict(t) for t in TEMPLATES]
_BUILTIN_BY_ID = {t["id"]: t for t in _BUILTIN}
_OVERLAY: dict[str, dict] = {}


def _skeleton(tid: str) -> dict:
    return {"id": tid, "ru": tid, "en": tid, "category": "product",
            "tara": "any", "emoji": "🖼️", "prompt": "", "motion": False,
            "showcase": False}


def _rebuild() -> None:
    global TEMPLATES, _BY_ID
    out: list[dict] = []
    for base in _BUILTIN:
        ov = _OVERLAY.get(base["id"]) or {}
        if ov.get("enabled") is False:
            continue
        if not ov:
            out.append(base)
            continue
        row = dict(base)
        for f in EDITABLE:
            if f in ov and ov[f] not in (None, ""):
                row[f] = ov[f]
        out.append(row)
    for tid, ov in _OVERLAY.items():
        if tid in _BUILTIN_BY_ID or ov.get("enabled") is False:
            continue
        row = _skeleton(tid)
        for f in EDITABLE:
            if f in ov and ov[f] not in (None, ""):
                row[f] = ov[f]
        out.append(row)
    TEMPLATES = out
    _BY_ID = {t["id"]: t for t in out}


def set_overlay(data: dict | None) -> None:
    global _OVERLAY
    _OVERLAY = {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}
    _rebuild()


def overlay() -> dict:
    return dict(_OVERLAY)


def is_builtin(tid: str) -> bool:
    return tid in _BUILTIN_BY_ID


def builtin(tid: str) -> dict | None:
    return _BUILTIN_BY_ID.get(tid)


def admin_list() -> list[dict]:
    """Каталог для админки: эффективные поля плюс метки правки и скрытия."""
    out = []
    for t in TEMPLATES:
        ov = _OVERLAY.get(t["id"]) or {}
        row = {f: t.get(f) for f in EDITABLE}
        row.update({"id": t["id"], "builtin": t["id"] in _BUILTIN_BY_ID,
                    "overridden": bool({f for f in EDITABLE if f in ov}),
                    "hidden": False})
        out.append(row)
    for tid, ov in _OVERLAY.items():
        if ov.get("enabled") is not False or tid not in _BUILTIN_BY_ID:
            continue
        base = _BUILTIN_BY_ID[tid]
        row = {f: base.get(f) for f in EDITABLE}
        row.update({"id": tid, "builtin": True, "overridden": True, "hidden": True})
        out.append(row)
    return out
