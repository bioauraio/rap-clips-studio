# Правки фронта под новые движки и новую экономику

Бэкенд уже отдаёт всё нужное. Фронт (`frontend/index.html`, `app.js`,
`style.css`, `i18n.js`) в этой задаче НЕ трогался — ниже ровно то, что в нём
надо поменять, фрагментами.

Пока правки не внесены, сервис работает: старые поля контракта сохранены, и
фронт продолжает видеть привычные `video: ["grok","seedance","kling"]`,
`points`, `usd`, `movies_estimate`. Но три вещи он показывает НЕВЕРНО — они
помечены ниже как «врёт».

---

## 1. `app.js:2691` — LD_SCENE_COST **врёт** (цифры устарели вчетверо)

```js
// БЫЛО
const LD_SCENE_COST = { grok: 4, seedance: 10, top: 16 };
```

Это зеркало backend `SCENE_COST`, и после пересчёта цен оно разошлось с
реальностью: лендинг считает «сколько это клипов» по 16 очков за сцену, а
Seedance 2.5 теперь стоит 167.

```js
// СТАЛО — зеркало backend/main.py (кадры на шлюзе + видео движком).
// Полный прайс приезжает в /api/billing/plans → costs.scene и costs.video;
// этот словарь нужен только запасной витрине для гостя без ответа сервера.
const LD_SCENE_COST = {
  grok: 4,                  // наша подписка: два кадра + анимация первого
  seedance: 22,             // Seedance 2 Mini — рабочая лошадка платных тарифов
  top: 154,                 // Seedance 2.5 720p
};
```

Лучше не хардкодить вовсе: в живом ответе `/api/billing/plans` теперь есть
`costs.scene` (движок → полная цена сцены), `costs.video`, `costs.frames`,
`costs.point_usd`. Запасные числа оставить только на случай пустого ответа.

## 2. `app.js:2698-2704` — LD_PLANS_FALLBACK **врёт** (старые нормы очков)

```js
// БЫЛО                        // СТАЛО
{ id: "free",    points: 120,  usd: 0 },     // 120     — не менялось
{ id: "pro",     points: 700,  usd: 20 },    // 660
{ id: "pro_max", points: 2400, usd: 100 },   // 3400
{ id: "studio",  points: 6000, usd: 299 },   // 10500
```

Там же рядом лежит `LD_PACKS_FALLBACK` — новые цены пакетов:

```js
const LD_PACKS_FALLBACK = [
  { id: "p400",   points: 400,   usd: 15 },
  { id: "p1000",  points: 1000,  usd: 36 },
  { id: "p2500",  points: 2500,  usd: 87 },
  { id: "p6000",  points: 6000,  usd: 199 },
  { id: "p15000", points: 15000, usd: 479 },
];
```

## 3. `app.js:1806, 1826-1827` — чип «kling» подписан как **Grok**

```js
// БЫЛО: всё, что не seedance, подписывается Грок'ом
opt.textContent = t(p === "seedance" ? "scene.providerSeedance" : "scene.providerGrok");
```

У PRO MAX и STUDIO в списке три семейства, и Kling показывается словом «Grok».

```js
// СТАЛО
const PROVIDER_KEY = {
  grok: "scene.providerGrok",
  seedance: "scene.providerSeedance",
  kling: "scene.providerKling",
};
opt.textContent = t(PROVIDER_KEY[p] || "scene.providerGrok");
```

Нужны новые строки словаря в `i18n.js`: `scene.providerKling`,
`scene.providerKlingShort`.

---

## 4. Новое: выбор КОНКРЕТНОЙ модели, а не только семейства

`/api/providers` теперь отдаёт `video_engines` — список моделей, открытых
тарифу, с ценой и реальной доступностью:

```json
{
  "video": ["grok", "seedance", "kling"],
  "video_engines": [
    { "id": "seedance-2-5", "title": "Seedance 2.5 · 720p", "family": "seedance",
      "default": true, "live": true, "first_last": true, "paid": true,
      "scene_cost": 167, "video_cost": 152, "usd_per_scene": 1.89,
      "note": "Самая дорогая позиция прайса. Витринный движок, не поточный." },
    { "id": "seedance-2-mini", "title": "Seedance 2 Mini · 720p", "default": false,
      "scene_cost": 35, "usd_per_scene": 0.246, "first_last": true, "live": true }
  ]
}
```

`POST /api/scenes/{id}/generate-video` принимает `engine` в теле рядом с
`provider`; `POST /api/tracks/{id}/generate-all-videos` — query-параметром
`engine`. Чужой тарифу движок молча опускается до дефолтного (FREE не получит
Seedance 2.5, попросив его строкой).

Что показать в карточке сцены: селектор из `video_engines` с ценой в очках
(`scene_cost`) и пометкой «первый+последний кадр» (`first_last: false` у Grok —
он оживляет только первый, монтаж на нём деградирует, и человек должен это
видеть до нажатия кнопки).

## 5. Новое: движок КАДРОВ и его честное состояние

```json
{
  "image_engine": "chatgpt",
  "image_engine_planned": "nano-banana-pro",
  "image_engine_downgraded": true,
  "frames_cost": 2,
  "image_engines": [
    { "id": "nano-banana-pro", "title": "Nano Banana Pro", "live": false,
      "max_refs": 8, "native_4k": true, "frames_cost": 15, "usd_per_image": 0.09,
      "current": false }
  ],
  "keys": { "kie": false, "seevio": false, "kling_official": false }
}
```

`image_engine_downgraded: true` означает: тариф обещает Nano Banana Pro, но
`KIE_API_KEY` не задан, и кадры рисует шлюз. Это НАДО показывать — иначе
человек платит за строчку в описании тарифа, которая не работает. Текст вроде
«Nano Banana Pro временно недоступен — кадры рисует ChatGPT» рядом с кнопкой
генерации кадров.

## 6. Новое: пакеты очков только при живой подписке

`/api/billing/plans` и `/api/billing/packs` отдают:

```json
{ "topup_requires_plan": true, "topup_allowed": false }
```

`POST /api/billing/create` с `kind: "topup"` от пользователя на FREE отвечает
**403** с кодом `subscription_required` и текстом
«Points packs top up an active plan. Subscribe first, then add points any time.»

Витрине: при `topup_allowed: false` кнопки пакетов гасить и подписывать
«доступно с платным тарифом», а не отправлять человека в 403.

## 7. Новое: две оценки «сколько клипов»

Карточка тарифа теперь отдаёт обе:

| поле | что значит |
|---|---|
| `movies_estimate` | по рабочей лошадке тарифа (самый дешёвый ПЛАТНЫЙ движок) |
| `movies_estimate_top` | по самому дорогому движку тарифа |
| `movies_estimate_grok` | по бесплатному Grok |
| `scene_cost` / `scene_cost_top` | цена сцены на этих двух движках |
| `usd_per_point` | сколько человек платит за одно очко |
| `image_engine_title` / `frames_cost` | чем и почём тариф рисует кадры |

`movies_estimate` в карточке PRO MAX = **3**, `movies_estimate_top` = **0**.
Оба числа правдивы: три клипа на Seedance 2 Mini или ни одного целого на
Seedance 2.5 (её хватает на 20 сцен — это движок для отдельных кадров, а не
для клипа целиком). Рисовать имеет смысл первое, второе — мелким шрифтом
рядом с названием топовой модели.

## 8. Тексты тарифов (`features`/`note` приезжают с сервера, но лендинг дублирует)

Новые формулировки уже лежат в `PLANS` бэкенда:

- PRO — «660 points every month — one full 3-minute clip on Seedance»
- PRO MAX — «Nano Banana Pro frames, Seedance 2.5 and Kling 3.0 Pro»
- STUDIO — «10500 points every month — two full clips on Seedance 2.5»

Если в `index.html`/`i18n.js` эти строки продублированы вручную — обновить,
иначе витрина и карточка тарифа разойдутся.
