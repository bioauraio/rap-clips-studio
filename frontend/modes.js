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
     external  — ПЕРЕХОД: свой экран в app.js (чат, музыка). Плитка обязана вести
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
      // МАСТЕРСКАЯ — ОСНОВНОЕ ОКНО СЕРВИСА, поэтому она стоит первой: тумблер
      // рисуется в порядке этого массива. Это переход, а не режим: у неё свой
      // экран в app.js.
      //
      // id НЕ ТРОГАЕМ. «chat» лежит в адресе (#/chat), в сохранённых ссылках
      // и в state.lastStep — переименование сломало бы закладки. Канонический
      // адрес окна теперь #/make, старый продолжает работать алиасом.
      //
      // Граница с режимами: мастерская — окно на ОДИН результат (кадр, ролик,
      // ответ), режим — конвейер на работу длиннее одного результата. Здесь
      // нельзя породить объект режима: ни трек, ни серию, ни товар.
      id: "chat",
      kind: "external",
      icon: "⚒",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M14.5 4.5l5 5-9 9H5.5v-5z'/><path d='M12 7l5 5'/></svg>",
      get title() { return T("modes.chat.title", "Мастерская"); },
      get full() { return T("modes.chat.full", "Мастерская — один кадр, ролик или ответ"); },
      get note() { return T("modes.chat.note", ""); },
      // Подпись кнопки перехода — своя у каждого экрана. Общая («Открыть
      // чат») была верна ровно до появления второго перехода.
      goKey: "modes.menu.openChat",
      external() {
        const b = document.querySelector("#chat-btn");
        if (b) b.click();
      },
      steps: [],
    },
    {
      id: "clip",
      kind: "mode",
      icon: "🎬",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='3' y='5' width='18' height='14' rx='3'/><path d='M3 9h18M8 5v14M16 5v14'/></svg>",
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
      // ЯРЛЫК, а не режим. Полное объяснение — в backend/formats.py,
      // MODE_SHORTCUTS: у стиля нет ни своего объекта, ни ритма, ни
      // документов, ни каркаса. Здесь он стоит там, где его ждёт владелец.
      id: "pixar",
      kind: "shortcut",
      icon: "🧸",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><circle cx='12' cy='9' r='4'/><path d='M5 20a7 7 0 0 1 14 0'/></svg>",
      get title() { return T("modes.pixar.title", "3D Pixar"); },
      get full() { return T("modes.pixar.full", "3D Pixar — стиль поверх rap clips"); },
      get note() { return T("modes.pixar.note", ""); },
      target: { mode: "clip", style: "pixar", kind: "album" },
      steps: [],
    },
    {
      // ИИ-БЛОГЕРЫ — отдельная страница, не формат внутри UGC. Центр тяжести
      // другой: здесь сначала СОБИРАЮТ ведущего (фото, развороты, голос), а
      // ролики — производное от него. В UGC наоборот: бриф продукта главный,
      // ведущий опционален.
      id: "blogger",
      kind: "mode",
      icon: "🎙",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='9' y='3' width='6' height='11' rx='3'/><path d='M5 11a7 7 0 0 0 14 0M12 18v3'/></svg>",
      get title() { return T("modes.blogger.title", "ИИ-блогеры"); },
      get full() { return T("modes.blogger.full", "ИИ-блогеры — сквозной ведущий и его ролики"); },
      get note() { return T("modes.blogger.note", ""); },
      projectKinds: ["blogger"],
      defaultKind: "blogger",
      object: "reel",
      steps: [
        step("chars", 1, "🎭", "project", "chars", "", "Блогеры"),
        step("persona", 2, "🙋", "project", "docs", "", "Характер"),
        step("reels", 3, "📋", "project", "tracks", "setup", "Ролики"),
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
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='7' y='2.5' width='10' height='19' rx='2.5'/><path d='M11 18h2'/></svg>",
      // Группа «Маркетинг (for brand)»: UGC и мокапы работают на бренд, а не
      // на артиста — меню режимов рисует их под общим заголовком.
      group: "brand",
      get title() { return T("modes.ugc.title", "UGC"); },
      get full() { return T("modes.ugc.full", "UGC — ролики про продукт"); },
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
      id: "mockup",
      kind: "mode",
      icon: "📦",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M12 3l8 4.5v9L12 21l-8-4.5v-9z'/><path d='M4 7.5l8 4.5 8-4.5M12 12v9'/></svg>",
      group: "brand",
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
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='3' y='6' width='18' height='12' rx='3'/><path d='M10 9.5v5l4-2.5z'/></svg>",
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
      // МУЗЫКА — ПЕРЕХОД, как чат: свой экран (#music), свой объект (релиз,
      // а не клип) и свой конвейер. Здесь же кончилась мёртвая плитка
      // «аудио»: раздел ожил, и держать рядом с ним запись «скоро» про то же
      // самое значило бы врать дважды — и о том, что готово, и о том, что нет.
      id: "music",
      kind: "external",
      icon: "🎚",
      svg: "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M9 18V6l10-2v12'/><circle cx='6.5' cy='18' r='2.5'/><circle cx='16.5' cy='16' r='2.5'/></svg>",
      get title() { return T("modes.music.title", "музыка"); },
      get full() { return T("modes.music.full", "Трек: загрузка, мастеринг, релиз"); },
      get note() { return T("modes.music.note", ""); },
      goKey: "modes.menu.openMusic",
      external() {
        const b = document.querySelector("#music-btn");
        if (b) { b.click(); return; }
        if (window.QlolMusic) window.QlolMusic.show();
      },
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
    // Админское больше НЕ вкладки кабинета: CRM, рассылка, выплаты и
    // редактор стилей уехали на отдельную страницу /admin. Здесь остался
    // один вход — ссылка, а не вкладка (href вместо acc): работа над
    // сервисом и работа над клипом не должны жить в одном модальном окне.
    { id: "admin", num: 5, icon: "🛠", href: "/admin", admin: true, get title() { return T("account.tabs.admin", "Админка"); } },
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

  /* Одноцветная SVG-иконка режима (stroke 1.75, currentColor) элементом. */
  function svgEl(m, cls) {
    const span = document.createElement("span");
    span.className = cls || "mode-svg";
    span.innerHTML = (m && m.svg) || "";
    return span;
  }

  window.QlolModes = {
    MODES, ACCOUNT_STEPS, svgEl,
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
