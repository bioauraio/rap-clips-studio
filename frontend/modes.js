/* ═══════════════════════════════════════════════════════════════════════════
   qlolvideo — ЕДИНЫЙ КЛИЕНТСКИЙ РЕЕСТР РЕЖИМОВ

   Подключается ПЕРВЫМ, до app.js и до nav.js:
       <script src="/modes.js?v=57"></script>

   ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Реестров режимов было два — backend/formats.py и
   массив MODES внутри nav.js, — и синхронизировались они руками. Хуже того,
   nav.js подключается только по флагу ?nav=1, то есть его реестр владелец в
   обычной студии не видит вообще: тумблер, живущий только там, не показался
   бы никому. Здесь ОДИН клиентский реестр, который читают и классическая
   шапка (app.js + mode-menu.js), и верстак (nav.js).

   ЧТО СЧИТАЕТСЯ РЕЖИМОМ. Ровно то же, что на сервере (backend/formats.py):
   различаются четыре вещи — объект второго уровня, откуда берётся ритм,
   какие сценарные документы нужны и каким каркасом режется содержимое.
   Совпадают все четыре — это пресет ВНУТРИ режима, а не второй режим.

   ТРИ ТИПА ЗАПИСИ (поле kind):
     mode      — настоящий режим: свой Project.kind, свои шаги, свой каркас.
     shortcut  — ЯРЛЫК: выглядит в тумблере как режим, но ведёт в чужой режим
                 с преднастройкой. «3D Pixar» — это стиль (STYLES.pixar), у
                 него нет ни одного признака режима; заводить под него вид
                 проекта значило бы получить album, отличающийся одной
                 строкой стиля. Владелец при этом ждёт его в тумблере — и
                 ярлык даёт ему там место, не размывая реестр.
     external  — ПЕРЕХОД: свой экран в app.js (чат). Плитка обязана вести
                 туда, а не гасить рабочую область под пустой режим.

   ПОДПИСЕЙ ЗДЕСЬ НЕТ. Только i18n-ключи (modes.<id>.*) — иначе перевод
   расползётся между реестром и словарём, как уже было с сервером.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolModes) return;

  /* Подпись из общего словаря; в демо (nav-preview.html) словаря нет. */
  function T(key, fallback) {
    const v = typeof window.t === "function" ? window.t(key) : "";
    return v || fallback;
  }

  /* Шаг режима. `panel` — значение data-nav-stage разметки (story | docs |
     chars | tracks), `pane` — панель ВНУТРИ карточки объекта (setup | board |
     anim). Разводить их обязательно: один и тот же «tracks» обслуживает
     четыре режима с разными названиями шагов. */
  function step(id, num, icon, scope, panel, pane, fallback) {
    return {
      id, num, icon, scope, panel, pane: pane || "",
      get title() { return T("modes.steps." + id, fallback || id); },
    };
  }

  const MODES = [
    {
      id: "clip",
      kind: "mode",
      icon: "🎬",
      // id НЕ трогаем: он в адресе (#/clip/...), в state.lastStep и в
      // сохранённых ссылках владельца. Переименован только заголовок.
      get title() { return T("modes.clip.title", "rap clips"); },
      get full() { return T("modes.clip.full", "rap clips — клип под свой трек"); },
      get note() { return T("modes.clip.note", ""); },
      projectKinds: ["album", "single"],
      defaultKind: "album",
      object: "track",
      steps: [
        step("story", 1, "✍", "project", "story", "", "Сюжет"),
        step("chars", 2, "🎭", "project", "chars", "", "Персонажи"),
        step("tracks", 3, "🎵", "project", "tracks", "setup", "Треки"),
        step("board", 4, "🎞", "track", "tracks", "board", "Раскадровка"),
        step("anim", 5, "▶", "track", "tracks", "anim", "Анимация"),
      ],
    },
    {
      // UGC И ИИ-БЛОГЕРЫ — ОДИН РЕЖИМ, а не два. Владелец называет их через
      // слэш, и по всем четырём признакам они совпадают: объект — ролик,
      // ритм — слоты 5–8 секунд, документы — персона и бриф, каркасы —
      // UGC_FORMATS. Разница ровно в одном: есть ли сквозной ведущий. Это
      // формат ролика внутри режима, а не второй режим.
      id: "ugc",
      kind: "mode",
      icon: "📱",
      get title() { return T("modes.ugc.title", "UGC / блогеры"); },
      get full() { return T("modes.ugc.full", "UGC и ИИ-блогеры"); },
      get note() { return T("modes.ugc.note", ""); },
      projectKinds: ["ugc"],
      defaultKind: "ugc",
      object: "reel",
      steps: [
        step("persona", 1, "🙋", "project", "docs", "", "Блогер"),
        step("chars", 2, "🎭", "project", "chars", "", "Персонажи"),
        step("reels", 3, "📋", "project", "tracks", "setup", "Ролики"),
        step("board", 4, "🎞", "track", "tracks", "board", "Раскадровка"),
        step("anim", 5, "▶", "track", "tracks", "anim", "Анимация"),
      ],
    },
    {
      // ЯРЛЫК, а не режим. Полное объяснение — в backend/formats.py,
      // MODE_SHORTCUTS: у стиля нет ни своего объекта, ни ритма, ни
      // документов, ни каркаса. Здесь он стоит там, где его ждёт владелец.
      id: "pixar",
      kind: "shortcut",
      icon: "🧸",
      get title() { return T("modes.pixar.title", "3D Pixar"); },
      get full() { return T("modes.pixar.full", "3D Pixar — стиль поверх rap clips"); },
      get note() { return T("modes.pixar.note", ""); },
      target: { mode: "clip", style: "pixar", kind: "album" },
      steps: [],
    },
    {
      id: "mockup",
      kind: "mode",
      icon: "📦",
      get title() { return T("modes.mockup.title", "мокапы"); },
      get full() { return T("modes.mockup.full", "Мокапы — предметная съёмка товара"); },
      get note() { return T("modes.mockup.note", ""); },
      projectKinds: ["mockup"],
      defaultKind: "mockup",
      object: "item",
      aspect: "1:1",
      steps: [
        step("brand", 1, "📐", "project", "docs", "", "Бренд"),
        step("items", 2, "📦", "project", "tracks", "setup", "Товары"),
        step("board", 3, "🎞", "track", "tracks", "board", "Кадры"),
        step("anim", 4, "▶", "track", "tracks", "anim", "Оживление"),
      ],
    },
    {
      id: "series",
      kind: "mode",
      icon: "📺",
      get title() { return T("modes.series.title", "сериалы"); },
      get full() { return T("modes.series.full", "Сериал с сезонами и сериями"); },
      get note() { return T("modes.series.note", ""); },
      projectKinds: ["series"],
      defaultKind: "series",
      object: "episode",
      groupBy: "season",
      steps: [
        step("bible", 1, "📖", "project", "docs", "", "Библия сезона"),
        step("chars", 2, "🎭", "project", "chars", "", "Персонажи"),
        step("season", 3, "🗓", "project", "docs", "", "Поэпизодник"),
        step("episode", 4, "📝", "project", "tracks", "setup", "Серии"),
        step("board", 5, "🎞", "track", "tracks", "board", "Раскадровка"),
        step("anim", 6, "▶", "track", "tracks", "anim", "Анимация"),
      ],
    },
    {
      // Чат — переход, а не режим: у него свой экран в app.js. Без плитки
      // вход в него исчезает вместе с топбаром, который верстак прячет.
      id: "chat",
      kind: "external",
      icon: "💬",
      get title() { return T("modes.chat.title", "чат"); },
      get full() { return T("modes.chat.full", "Чат с моделью"); },
      get note() { return T("modes.chat.note", ""); },
      external() {
        const b = document.querySelector("#chat-btn");
        if (b) b.click();
      },
      steps: [],
    },
    {
      // Аудио — В ТУМБЛЕРЕ ЕГО НЕТ (seg: false). Шесть живых сегментов плюс
      // один мёртвый — это мёртвый отъедает место у живых на узком экране и
      // занимает слот в доке верстака. Место мёртвой плитки — подвал «Скоро»
      // в шторке режимов, где видно, но ничему не мешает.
      id: "audio",
      kind: "mode",
      icon: "🎧",
      seg: false,
      soon: true,
      get title() { return T("modes.audio.title", "аудио"); },
      get full() { return T("modes.audio.full", "Озвучка, музыка и мастеринг"); },
      get note() { return T("modes.audio.note", ""); },
      steps: [],
    },
  ];

  /* Кабинет — не режим. Это раздел с тем же вторым ярусом, и в тумблере ему
     места нет: режимы и разделы аккаунта — разные ярусы навигации. */
  const ACCOUNT_STEPS = [
    { id: "profile", num: 1, icon: "👤", acc: "account", get title() { return T("account.tabs.account", "Аккаунт"); } },
    { id: "plan", num: 2, icon: "⚡", acc: "plan", get title() { return T("account.tabs.plan", "Тариф"); } },
    { id: "files", num: 3, icon: "🗃", acc: "files", get title() { return T("account.tabs.files", "Файлы"); } },
    { id: "ref", num: 4, icon: "🤝", acc: "ref", get title() { return T("account.tabs.ref", "Амбассадор"); } },
    { id: "payouts", num: 5, icon: "💸", acc: "payouts", admin: true, get title() { return T("account.tabs.payouts", "Выплаты"); } },
    { id: "crm", num: 6, icon: "👥", acc: "crm", admin: true, get title() { return T("account.tabs.crm", "Клиенты"); } },
    { id: "bc", num: 7, icon: "📣", acc: "broadcast", admin: true, get title() { return T("account.tabs.broadcast", "Рассылка"); } },
  ];

  const byId = (id) => MODES.find((m) => m.id === String(id || "")) || null;

  /* Режим по виду проекта. Неизвестный вид — старый проект, то есть клип. */
  function ofKind(kind) {
    const k = String(kind || "").trim();
    return MODES.find((m) => (m.projectKinds || []).includes(k)) || byId("clip");
  }

  /* Что показывает тумблер: порядок массива и есть порядок сегментов.
     Владелец назвал его сам — rap clips, UGC, 3D Pixar, мокапы; сериалы и
     чат дописаны в хвост. */
  const seg = () => MODES.filter((m) => m.seg !== false);
  const soon = () => MODES.filter((m) => m.soon);
  const real = () => MODES.filter((m) => m.kind === "mode" && !m.soon);

  window.QlolModes = {
    MODES, ACCOUNT_STEPS,
    byId, ofKind, seg, soon, real,
    isShortcut: (m) => Boolean(m && m.kind === "shortcut"),
    isExternal: (m) => Boolean(m && m.kind === "external"),
    /* Все виды проектов в порядке реестра — окно «новый проект» рисует
       карточки отсюда, а не из захардкоженного списка из четырёх штук. */
    kinds() {
      const out = [];
      real().forEach((m) => (m.projectKinds || []).forEach((k) => out.push({ kind: k, mode: m.id })));
      return out;
    },
  };
})();
