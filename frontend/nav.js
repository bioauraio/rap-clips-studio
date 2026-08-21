/* ═══════════════════════════════════════════════════════════════════════════
   qlolvideo — НАВИГАЦИЯ «ВЕРСТАК + СТЕКЛО»
   Самодостаточный модуль. Подключается ПОСЛЕ app.js:
       <script src="/app.js?v=42"></script>
       <script src="/nav.js?v=1"></script>

   Принцип: модуль НЕ переписывает app.js. Он:
     • строит стеклянный верстак и переносит в него существующие узлы
       (appendChild сохраняет навешанные обработчики);
     • ни один id не удаляет и не переименовывает — $("#…") в app.js
       это голый querySelector без проверок, пропавший id роняет весь файл;
     • читает состояние прямо из DOM (скрытые .stage-tabs с .stage-dot),
       поэтому работает и без единой правки в app.js;
     • переживает поллинг: MutationObserver на #tracks пере-применяет вид
       после каждой перерисовки render().

   Публичный API: window.QlolNav
     init(cfg)            — запуск (вызывается сам на DOMContentLoaded)
     go(route)            — '#/clip/board/t3'
     refresh()            — пересобрать из DOM/данных
     setData({...})       — отдать данные явно (демо или app.js)
     registerMode(mode)   — новый режим одним объектом
     on(event, fn)        — 'route' | 'stage' | 'mode' | 'track'
     toast(text)          — стеклянный тост
     state, MODES
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolNav) return;

  /* ────────────────────────── мелкие помощники ────────────────────────── */

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function on(node, ev, fn, opts) {
    if (node) node.addEventListener(ev, fn, opts);
  }

  /* ──────────────────────── РЕЕСТР РЕЖИМОВ ────────────────────────
     Рельса, док, полка, мобильная шторка и роутер рендерятся ИЗ ЭТОГО
     МАССИВА. Новый режим = один объект, а не правки в трёх файлах.

     ВАЖНО: `steps` — это КОНТЕКСТ РЕЖИМА, а не «этапы клипа».
     У клипа тут 5 шагов, у сериала 6, у кабинета — 4 вкладки, у чата —
     селектор модели. Класть в этот ярус сам режим нельзя: иерархия
     развалится.

     ЧТО ИЗМЕНИЛОСЬ ПРОТИВ ПРОТОТИПА (и почему без этого реестр — декорация):
       • `panel` и `pane` разведены. Раньше шаг «Сюжет» указывал pane:"plot",
         а «Монтаж» — pane:"final"; панелей с такими именами в разметке нет
         уже давно (index.html знает setup|board|anim, см. STAGES в app.js).
         Шаг «Сюжет» гасил ВСЕ панели трека, «Монтаж» показывал пустоту.
       • stepState/nextAction больше не начинаются с `if (mode !== "clip")`.
         Правила переехали В ОБЪЕКТ РЕЖИМА — пока они были захардкожены,
         любой новый режим оставался немым: без точек состояния и без
         кнопки «что дальше».
       • Подписи берутся из общего словаря (i18n.js). В демо-странице
         словаря нет — поэтому у каждой подписи есть запасной текст. */

  /* Подпись из общего словаря; в демо (nav-preview.html) словаря нет. */
  function T(key, fallback) {
    const v = typeof window.t === "function" ? window.t(key) : "";
    return v || fallback;
  }

  /* Заполнена ли панель проекта: по ней считается состояние проектных шагов,
     у которых нет собственных .stage-tab в карточках треков. */
  function filled(sel) {
    const node = $(sel);
    if (!node) return false;
    if ("value" in node) return Boolean(String(node.value || "").trim());
    return Boolean(node.children.length || String(node.textContent || "").trim());
  }

  function docsFilled() {
    const box = $("#docs-grid");
    if (!box || !box.children.length) return false;
    const cards = $$(".doc-card", box);
    if (!cards.length) return false;
    const busy = cards.some((c) => c.classList.contains("is-busy"));
    if (busy) return "busy";
    const done = cards.filter((c) => {
      const body = $(".doc-body", c);
      return body && !body.classList.contains("muted");
    }).length;
    if (!done) return false;
    return done === cards.length ? "done" : "part";
  }

  /* Общее правило состояния шага. Режим переопределяет его только там, где
     оно действительно другое, а не копирует целиком. */
  function baseStepState(st, stepId, mode) {
    const def = (mode.steps || []).find((s) => s.id === stepId);
    if (!def) return "empty";
    if (def.id === "chars") return charsFilled() ? "done" : "empty";
    if (def.panel === "story") return filled("#story") ? "done" : "empty";
    if (def.panel === "docs") {
      const d = docsFilled();
      return d === false ? "empty" : d;
    }
    const key = def.pane;                       // setup | board | anim
    if (!key) return "empty";
    if (def.scope === "track") {
      const tr = activeTrack();
      return tr ? tr.states[key] || "empty" : "empty";
    }
    if (!st.tracks.length) return "empty";
    const all = st.tracks.map((x) => x.states[key] || "empty");
    if (all.includes("error")) return "error";
    if (all.includes("busy")) return "busy";
    if (all.every((s) => s === "done")) return "done";
    if (all.some((s) => s !== "empty")) return "part";
    return "empty";
  }

  /* Кнопка «что дальше»: одна акцентная на весь экран. Правила — В РЕЖИМЕ:
     у клипа сначала трек и сюжет, у сериала сначала библия и сценарий, у
     UGC — блогер. Общий список тут был бы враньём для двух режимов из трёх. */
  function clipNext(st, S) {
    return [
      { when: () => !st.tracks.length, label: T("modes.object.track.add", "Добавить трек"), step: "tracks", cost: 0 },
      { when: () => S("tracks") !== "done", label: "Дозаполнить трек", step: "tracks", cost: 0 },
      { when: () => S("story") === "empty", label: T("story.gen", "Сгенерировать сюжет"), step: "story", cost: 2 },
      { when: () => S("board") === "empty", label: T("track.genScenes", "Разбить на сцены"), step: "board", cost: 3 },
      { when: () => S("board") !== "done", label: "Сгенерировать кадры", step: "board", cost: 8 },
      // Стиль сменили, а кадры остались прежними — это и есть следующее
      // действие. Раньше на смене чипов «что дальше» молчало, и человек
      // видел ровно то, на что жаловался: другой стиль, те же картинки.
      { when: () => styleDirty(), label: T("restyle.next", "Перерисовать в новом стиле"), step: "board", cost: 8 },
      { when: () => S("anim") !== "done", label: "Оживить кадры", step: "anim", cost: 12 },
    ];
  }

  /* Есть ли у активного объекта кадры, снятые НЕ нынешним стилем. Читаем
     атрибут карточки, который проставляет app.js по ответу сервера
     (track.scenes_stale): второй счётчик на фронте однажды разойдётся с
     первым, и разойдётся молча. */
  function styleDirty() {
    const tr = activeTrack();
    return Boolean(tr && Number(tr.stale || 0) > 0);
  }

  function ugcNext(st, S) {
    return [
      { when: () => S("persona") === "empty", label: T("docs.genBibleUgc", "Собрать блогера"), step: "persona", cost: 0 },
      { when: () => !charsFilled(), label: T("chars.add", "Добавить персонажа"), step: "chars", cost: 0 },
      { when: () => !st.tracks.length, label: T("modes.object.reel.add", "Добавить ролик"), step: "reels", cost: 0 },
      { when: () => S("board") === "empty", label: T("track.genScenes", "Разбить на слоты"), step: "board", cost: 3 },
      { when: () => S("board") !== "done", label: "Сгенерировать кадры", step: "board", cost: 8 },
      { when: () => S("anim") !== "done", label: "Оживить кадры", step: "anim", cost: 12 },
    ];
  }

  function seriesNext(st, S) {
    return [
      { when: () => S("bible") === "empty", label: T("docs.genBibleSeries", "Собрать библию сезона"), step: "bible", cost: 0 },
      { when: () => !charsFilled(), label: T("chars.add", "Добавить персонажа"), step: "chars", cost: 0 },
      { when: () => !st.tracks.length, label: T("docs.createEpisodes", "Создать серии"), step: "season", cost: 0 },
      { when: () => S("episode") !== "done", label: T("docs.genScript", "Написать сценарий серии"), step: "episode", cost: 0 },
      { when: () => S("board") === "empty", label: T("track.genScenes", "Разбить на кадры"), step: "board", cost: 3 },
      { when: () => S("board") !== "done", label: "Сгенерировать кадры", step: "board", cost: 8 },
      { when: () => S("anim") !== "done", label: "Оживить кадры", step: "anim", cost: 12 },
    ];
  }

  /* РЕЕСТР — ОБЩИЙ, из modes.js. Своего массива у верстака больше нет: он
     жил здесь и синхронизировался с backend/formats.py руками, а видел его
     только тот, кто включил флаг ?nav=1. Тумблер режимов обязан быть один на
     обе шапки, значит и реестр один.

     Единственное, что верстак дописывает к записям, — правило «что дальше»:
     оно завязано на его собственное состояние (state, charsFilled) и в общем
     реестре ему делать нечего. */
  const NEXT_BY_MODE = { clip: clipNext, ugc: ugcNext, series: seriesNext };

  const REG = window.QlolModes || null;
  const MODES = REG ? REG.MODES : [];
  MODES.forEach((m) => { if (NEXT_BY_MODE[m.id]) m.next = NEXT_BY_MODE[m.id]; });

  /* Кабинет — не режим, но ярус у него тот же. Тоже из общего реестра. */
  const ACCOUNT_STEPS = REG ? REG.ACCOUNT_STEPS : [];

  /* Что рисуем в рейке и в доке: ярлыки и переходы там равноправны с
     режимами (владелец называет их в одном ряду), мёртвое «скоро» — нет. */
  function segModes() { return REG ? REG.seg() : MODES; }

  const DRAWER_SEGS = [
    { id: "projects", icon: "🗂", title: "Проекты" },
    { id: "chars",    icon: "🎭", title: "Персонажи" },
    { id: "history",  icon: "🕘", title: "История" },
  ];

  /* ──────────────────────────── состояние ──────────────────────────── */

  const state = {
    mode: "clip",
    step: "story",
    trackId: null,
    tracks: [],
    project: { name: "", kind: "", cover: "" },
    projects: [],
    user: { name: "", plan: "", points: null, isAdmin: false },
    lastStep: {},           // режим → последний открытый шаг
    drawer: false,
    drawerSeg: "projects",
    pop: false,
    sheet: null,
    stuck: false,
  };

  const listeners = { route: [], stage: [], mode: [], track: [] };
  function emit(ev, payload) { (listeners[ev] || []).forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } }); }

  let cfg = {
    appSelector: "#app",
    tracksSelector: "#tracks",
    charsSelector: "#characters",
    demo: false,
    getTracks: null,      // () => [{id,title,pos,cover,states:{...}}]
    onStage: null,        // (mode, step, trackId) => void
    startRoute: null,
  };

  const els = {};         // ссылки на созданные узлы
  let booted = false;

  /* ───────────────────── чтение состояния из DOM ─────────────────────
     Скрытые .stage-tabs у каждой карточки трека уже содержат .stage-dot
     с классами empty|part|busy|done|error — берём готовое, ничего не
     пересчитываем и не дублируем логику stageStates() из app.js. */

  const DOT_CLASSES = ["empty", "part", "busy", "done", "error"];

  function dotState(node) {
    if (!node) return "empty";
    for (const c of DOT_CLASSES) if (node.classList.contains(c)) return c;
    return "empty";
  }

  function readTracksFromDom() {
    const box = $(cfg.tracksSelector);
    if (!box) return [];
    return $$(".track-card", box).map((card, i) => {
      const states = {};
      $$(".stage-tab", card).forEach((tab) => {
        states[tab.dataset.stage] = dotState($(".stage-dot", tab));
      });
      const img = $(".t-cover-img", card);
      const titleEl = $(".t-title", card);
      const posEl = $(".pos", card);
      // states — СЫРЫЕ ключи панелей карточки (setup|board|anim). Раньше они
      // тут же переименовывались в имена шагов клипа (tracks/board/anim/final),
      // и любой другой режим читал бы чужие имена. Перевод «шаг → панель»
      // теперь живёт в реестре (step.pane) и делается в момент чтения.
      return {
        id: card.dataset.id || String(i + 1),
        title: (titleEl && (titleEl.value || titleEl.textContent)) || "Без имени",
        pos: (posEl && posEl.textContent.replace(/[^\d]/g, "")) || String(i + 1),
        cover: img && !img.classList.contains("hidden") ? img.getAttribute("src") : "",
        states: {
          setup: states.setup || "empty",
          board: states.board || "empty",
          anim: states.anim || "empty",
        },
        // Сколько кадров объекта сняты прежним стилем. Число считает сервер,
        // карточка кладёт его в data-stale — верстак только читает.
        stale: Number(card.dataset.stale || 0),
      };
    });
  }

  function charsFilled() {
    const box = $(cfg.charsSelector);
    return Boolean(box && box.children.length);
  }

  /* Режим = вид ОТКРЫТОГО проекта. Второго переключателя нет: сериал не
     превращается в клип, у них разные объекты второго уровня. Рейка режимов
     показывает, где ты, и ведёт в проект нужного вида. */
  function syncProjectMode() {
    if (cfg.demo) return false;
    const sel = $("#project-select");
    const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
    const kind = (opt && opt.dataset.kind) || "";
    if (!kind) return false;
    const m = MODES.find((x) => (x.projectKinds || []).includes(kind));
    if (m && m.id !== state.mode) {
      state.mode = m.id;
      const steps = activeSteps();
      const back = state.lastStep[m.id];
      state.step = steps.some((s) => s.id === back) ? back : (steps[0] && steps[0].id) || "story";
      // Сообщаем наверх, что режим сменился: проект приезжает АСИНХРОННО, уже
      // после первой отрисовки рейки, и без пересборки на ней остаётся
      // подсвеченным «rap clips», пока шаги ниже показывают сериал. Два
      // разных ответа на вопрос «где я» на одной панели.
      return true;
    }
    return false;
  }

  function syncData() {
    const modeChanged = syncProjectMode();
    if (cfg.getTracks) state.tracks = cfg.getTracks() || [];
    else state.tracks = readTracksFromDom();

    if (!cfg.demo) {
      const nameEl = $("#project-name");
      const kindEl = $("#project-kind");
      const coverEl = $("#project-cover-img");
      if (nameEl) state.project.name = nameEl.value || "";
      if (kindEl) state.project.kind = kindEl.textContent || "";
      if (coverEl) state.project.cover = coverEl.classList.contains("hidden") ? "" : coverEl.getAttribute("src") || "";
      const pts = $("#points-badge");
      if (pts) state.user.points = pts.textContent || "";
    }

    // активный трек всегда валиден
    if (state.tracks.length) {
      if (!state.tracks.some((t) => String(t.id) === String(state.trackId))) {
        const unfinished = state.tracks.find((t) => t.states.anim !== "done");
        state.trackId = (unfinished || state.tracks[0]).id;
      }
    } else {
      state.trackId = null;
    }
    return modeChanged;
  }

  function activeMode() { return MODES.find((m) => m.id === state.mode) || MODES[0]; }

  function activeSteps() {
    if (state.mode === "account") {
      return ACCOUNT_STEPS.filter((s) => !s.admin || state.user.isAdmin);
    }
    return activeMode().steps || [];
  }

  function activeTrack() {
    return state.tracks.find((t) => String(t.id) === String(state.trackId)) || null;
  }

  function stepDef(id) { return activeSteps().find((s) => s.id === id) || activeSteps()[0]; }

  /* Состояние шага. Правило берётся У РЕЖИМА: пока здесь стояло
     `if (state.mode !== "clip") return "empty"`, любой новый режим был нем —
     все его шаги показывали пустые точки, а кнопка «что дальше» молчала. */
  function stepState(stepId) {
    const mode = activeMode();
    if (state.mode === "account") return "empty";
    const fn = mode.stepState || baseStepState;
    return fn(state, stepId, mode) || "empty";
  }

  /* Доля готовности шага — заливка в нижней кромке капсулы. */
  function stepProgress(stepId) {
    if (state.mode === "account" || !state.tracks.length) return 0;
    const def = stepDef(stepId);
    if (!def || !def.pane) {
      const s = stepState(stepId);
      return s === "done" ? 1 : s === "part" || s === "busy" ? 0.5 : 0;
    }
    if (def.scope === "track") {
      const s = stepState(stepId);
      return s === "done" ? 1 : s === "part" || s === "busy" ? 0.5 : 0;
    }
    const done = state.tracks.filter((t) => (t.states[def.pane] || "empty") === "done").length;
    return done / state.tracks.length;
  }

  function anyBusy() {
    return state.tracks.some((t) => Object.values(t.states).includes("busy"));
  }

  /* ───────────────────────── построение каркаса ───────────────────────── */

  function buildChrome() {
    const app = $(cfg.appSelector);
    if (!app) return false;

    // всё, что уже лежит в #app, уедет в рабочую область
    const existing = Array.from(app.children);

    /* ── ярус 1+2: одна липкая стеклянная плита ── */
    const head = el("div", "wb-head");
    const glass = el("div", "wb-head-glass");
    head.appendChild(glass);

    const rail = el("div", "wb-rail");
    const burger = el("button", "wb-burger", "☰");
    burger.type = "button";
    burger.setAttribute("aria-label", "Проекты и материалы");
    burger.setAttribute("aria-expanded", "false");

    const brand = el("a", "wb-brand");
    brand.href = "#/clip";
    brand.appendChild(el("b", null, "⬢"));
    brand.appendChild(el("span", null, "qlol"));

    const modes = el("nav", "wb-modes");
    modes.setAttribute("role", "tablist");
    modes.setAttribute("aria-label", "Режим генерации");

    const right = el("div", "wb-rail-right");

    // Очки и «Тариф» — канон апселла: всегда на виду, верх-право.
    const points = el("span", "wb-points kind-badge");
    points.hidden = true;
    points.title = "остаток генераций";
    const planBtn = el("button", "wb-plan", "Тариф");
    planBtn.type = "button";
    const avatar = el("button", "wb-avatar", "◎");
    avatar.type = "button";
    avatar.title = "Кабинет";
    avatar.setAttribute("aria-haspopup", "menu");
    avatar.setAttribute("aria-expanded", "false");
    right.append(points, planBtn, avatar);

    rail.append(burger, brand, modes, right);

    const shelfRow = el("div", "wb-shelf-row");
    const shelf = el("div", "wb-shelf");
    const projBtn = el("button", "wb-project");
    projBtn.type = "button";
    projBtn.setAttribute("aria-haspopup", "dialog");
    const projImg = el("img");
    projImg.alt = "";
    const projName = el("span", "wb-project-name", "Проект");
    projBtn.append(projImg, projName, el("i", null, "▾"));
    const sep = el("span", "wb-shelf-sep");
    const stages = el("nav", "wb-stages");
    stages.setAttribute("role", "tablist");
    stages.setAttribute("aria-label", "Шаги режима");
    const shelfNext = el("button", "wb-shelf-next");
    shelfNext.type = "button";
    shelf.append(projBtn, sep, stages, shelfNext);
    shelfRow.appendChild(shelf);

    glass.append(rail, shelfRow);   // ярус объектов допишется ниже — он тоже липкий

    /* ── ярус 3: выбор объекта ── */
    const objects = el("div", "wb-objects");
    objects.hidden = true;
    const stripPrev = el("button", "wb-strip-btn", "◀");
    stripPrev.type = "button";
    stripPrev.setAttribute("aria-label", "Предыдущий трек");
    const objs = el("div", "wb-objs");
    const stripNext = el("button", "wb-strip-btn", "▶");
    stripNext.type = "button";
    stripNext.setAttribute("aria-label", "Следующий трек");
    const objCount = el("span", "wb-obj-count", "");
    objects.append(stripPrev, objs, stripNext, objCount);

    /* ── рабочая область ── */
    // Строка треков живёт ВНУТРИ липкой плиты: это тот элемент, за которым
    // тянутся на шагах 4–6, прятать его при скролле нельзя.
    glass.appendChild(objects);

    const body = el("main", "wb-body");
    existing.forEach((n) => body.appendChild(n));

    app.append(head, body);

    /* ── плавающие слои живут в <body>, не в #app ── */
    const nextBtn = el("button", "wb-next");
    nextBtn.type = "button";
    nextBtn.hidden = true;

    const scrim = el("div", "wb-scrim");
    scrim.hidden = true;

    const drawer = el("aside", "wb-drawer");
    drawer.hidden = true;
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-label", "Проекты и материалы");
    const segs = el("div", "wb-drawer-segs");
    DRAWER_SEGS.forEach((s) => {
      const b = el("button", "wb-drawer-seg" + (s.id === "projects" ? " on" : ""), s.icon + " " + s.title);
      b.type = "button";
      b.dataset.seg = s.id;
      on(b, "click", () => { state.drawerSeg = s.id; renderDrawer(); });
      segs.appendChild(b);
    });
    const drawerBody = el("div", "wb-drawer-body");
    drawer.append(segs, drawerBody);

    const pop = el("div", "wb-pop");
    pop.hidden = true;
    pop.setAttribute("role", "menu");

    const sheet = el("div", "wb-sheet");
    sheet.hidden = true;
    sheet.setAttribute("role", "dialog");
    const sheetHandle = el("div", "wb-sheet-handle");
    const sheetTitle = el("h3", "wb-sheet-title", "");
    const sheetBody = el("div", "wb-sheet-body");
    sheet.append(sheetHandle, sheetTitle, sheetBody);

    const dock = el("nav", "wb-dock");
    dock.setAttribute("aria-label", "Режимы");

    document.body.append(nextBtn, scrim, drawer, pop, sheet, dock);

    Object.assign(els, {
      app, head, glass, rail, burger, brand, modes, right,
      shelfRow, shelf, projBtn, projImg, projName, sep, stages, shelfNext,
      objects, objs, stripPrev, stripNext, objCount, body,
      nextBtn, scrim, drawer, drawerBody, pop, sheet, sheetTitle, sheetBody, dock,
      points, planBtn, avatar,
    });

    on(avatar, "click", (e) => { e.stopPropagation(); togglePop(); });
    on(points, "click", () => openAccount("plan"));
    on(planBtn, "click", () => openAccount("plan"));
    return true;
  }

  /* ─────────────── перенос существующих узлов в новые места ───────────────
     appendChild ПЕРЕМЕЩАЕТ узел вместе с обработчиками — ничего не ломается.
     Ни один id не исчезает: $("#…") в app.js продолжает находить всё. */

  function adoptExisting() {
    if (cfg.demo) return;

    const pts = $("#points-badge");
    const accBtn = $("#account-btn");
    const logout = $("#logout-btn");
    const select = $("#project-select");
    const newProj = $("#new-project-btn");
    const cover = $("#project-cover");
    const name = $("#project-name");
    const kind = $("#project-kind");
    const modeBadge = $("#project-mode");

    // ПРАВЫЙ КРАЙ РЕЙКИ: усыновляем ВСЁ содержимое .tb-user списком, а не
    // поимённо. Поимённый список уже один раз тихо съел кнопку — #chat-btn
    // появился в топбаре позже прототипа, в перечень не попал, а верстак
    // прячет топбар целиком (body.wb-on .topbar{display:none}). Единственный
    // вход в чат просто исчезал бы с экрана, и следующая новая кнопка
    // шапки умерла бы так же молча.
    const tbUser = $(".topbar .tb-user");
    if (tbUser) {
      Array.from(tbUser.children).forEach((node) => {
        if (node === accBtn || node === logout) return;   // им место в поповере
        els.right.insertBefore(node, els.planBtn);
      });
    }
    if (pts) {
      els.points.hidden = true;
      els.points.dataset.adopted = "1";
      pts.style.cursor = "pointer";
      on(pts, "click", () => openAccount("plan"));
    }

    // «Кабинет» и «выйти» уезжают в поповер, но остаются в DOM
    if (accBtn) { accBtn.classList.add("hidden"); els.pop.appendChild(accBtn); }
    if (logout) { logout.classList.add("hidden"); els.pop.appendChild(logout); }
    els.accBtn = accBtn;
    els.logoutBtn = logout;

    // селект проекта прячем, но не трогаем: на нём висит обработчик app.js
    if (select) {
      select.classList.add("hidden");
      els.shelf.appendChild(select);
      // Смену проекта слушаем ЗДЕСЬ, а не через наблюдателя за #tracks: у
      // проекта без объектов список не меняется вовсе, наблюдатель молчит, и
      // рейка режимов остаётся показывать предыдущий вид. dataset.kind у
      // option уже верный — данные проекта для этого ждать не нужно.
      on(select, "change", () => { if (syncData()) renderModes(); renderShelf(); });
    }
    els.projSelect = select;

    // «+ проект» переезжает в ящик
    if (newProj) { newProj.classList.add("wb-drawer-new"); els.drawerNewBtn = newProj; }

    // Обложка + имя + вид + режим уезжают в шапку рабочей области, а не в
    // панель сюжета: у сериала и UGC панели сюжета нет вовсе, и привязка к
    // ней оставила бы их без имени проекта.
    if (cover || name || kind || modeBadge) {
      const head = el("div", "wb-projhead");
      head.style.cssText = "display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap";
      [cover, name, kind, modeBadge].forEach((n) => { if (n) head.appendChild(n); });
      els.body.insertBefore(head, els.body.firstChild);
      if (name) on(name, "input", () => { state.project.name = name.value; paintProjectChip(); });
    }
  }

  function panelOf(sel) {
    const node = $(sel);
    return node ? node.closest(".panel") || node.parentElement : null;
  }

  /* Разметка приложения размечается атрибутом data-nav-stage. */
  function tagPanels() {
    if (cfg.demo) return;
    // Имена здесь — ЗНАЧЕНИЯ step.panel из реестра, а не имена шагов: один
    // и тот же «tracks» обслуживает три режима с разными названиями шагов.
    const map = [["#story", "story"], ["#docs-panel", "docs"],
                 ["#characters", "chars"], ["#tracks", "tracks"],
                 ["#add-track-panel", "tracks"]];
    map.forEach(([sel, stages]) => {
      const p = panelOf(sel);
      if (p && !p.hasAttribute("data-nav-stage")) p.setAttribute("data-nav-stage", stages);
    });
  }

  /* ───────────────────────────── рендер ───────────────────────────── */

  function renderModes() {
    const list = segModes();
    els.modes.innerHTML = "";
    list.forEach((m) => {
      const b = el("button", "wb-mode" + (m.id === state.mode ? " on" : "")
        + (m.soon ? " is-soon" : "") + (m.kind === "shortcut" ? " is-shortcut" : ""));
      b.type = "button";
      b.dataset.mode = m.id;
      b.setAttribute("role", "tab");
      if (m.id === state.mode) b.setAttribute("aria-current", "page");
      b.title = m.full || m.title;
      b.append(el("span", "wb-mode-ico", m.icon), el("span", "wb-mode-cap", m.title));
      if (m.id === state.mode && anyBusy()) b.classList.add("is-busy");
      on(b, "click", () => goMode(m.id));
      els.modes.appendChild(b);
    });

    // док: те же режимы, теми же данными; при >5 пятый слот становится «Ещё»
    els.dock.innerHTML = "";
    const fits = list.length <= 5 ? list : list.slice(0, 4);
    fits.forEach((m) => {
      const b = el("button", "wb-dock-item" + (m.id === state.mode ? " on" : ""));
      b.type = "button";
      b.dataset.mode = m.id;
      if (m.id === state.mode) b.setAttribute("aria-current", "page");
      b.append(el("span", "wb-dock-ico", m.icon), el("span", "wb-dock-cap", m.title));
      if (m.id === state.mode && anyBusy()) b.classList.add("is-busy");
      on(b, "click", () => goMode(m.id));
      els.dock.appendChild(b);
    });
    if (list.length > 5) {
      const more = el("button", "wb-dock-item");
      more.type = "button";
      more.append(el("span", "wb-dock-ico", "✦"),
        el("span", "wb-dock-cap", T("modes.menu.more", "Ещё")));
      // «Ещё» открывает ТУ ЖЕ шторку режимов, что и капсула в классической
      // шапке: один компонент на четыре поверхности, а не два похожих.
      on(more, "click", () => {
        if (window.QlolModeMenu) { closeLayers(); window.QlolModeMenu.open(state.mode); }
        else openSheet(T("modes.menu.title", "Режимы"), modeTiles());
      });
      els.dock.appendChild(more);
    }
  }

  function modeTiles() {
    const list = el("div", "wb-sheet-list");
    segModes().forEach((m) => {
      const b = el("button", "wb-proj-card" + (m.id === state.mode ? " on" : ""));
      b.type = "button";
      const ico = el("span", "wb-proj-cover", m.icon);
      const main = el("div", "wb-proj-main");
      main.append(el("div", "wb-proj-name", m.full || m.title),
        el("div", "wb-proj-meta", m.soon ? T("modes.menu.soon", "скоро")
          : m.kind === "shortcut" ? T("modes.menu.shortcut", "стиль")
          : T("modes.menu.available", "доступен")));
      b.append(ico, main);
      on(b, "click", () => { closeLayers(); goMode(m.id); });
      list.appendChild(b);
    });
    return list;
  }

  function renderShelf() {
    const steps = activeSteps();
    els.stages.innerHTML = "";

    steps.forEach((s) => {
      const st = stepState(s.id);
      const b = el("button", "wb-stage" + (s.id === state.step ? " on" : ""));
      b.type = "button";
      b.dataset.step = s.id;
      b.setAttribute("role", "tab");
      if (s.id === state.step) b.setAttribute("aria-current", "step");
      b.append(el("span", "wb-num", String(s.num)),
        el("span", "wb-ico", s.icon),
        el("span", "wb-stage-cap", s.title));
      const dot = el("span", "wb-dot " + st);
      b.appendChild(dot);
      const fill = el("span", "wb-stage-fill");
      fill.style.setProperty("--p", String(stepProgress(s.id)));
      b.appendChild(fill);

      if (s.scope === "track" && !state.tracks.length) {
        b.disabled = true;
        b.title = "Сначала добавь объект на предыдущем шаге";
      }
      on(b, "click", () => goStep(s.id));
      els.stages.appendChild(b);
    });

    placeProjectChip();
    paintProjectChip();
    renderNext();
    requestAnimationFrame(() => {
      markCut();
      const cur = $(".wb-stage.on", els.stages);
      // nearest, а не center: если шаг и так виден — лента не дёргается
      if (cur) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    // разделитель между проектными и трековыми шагами
    els.sep.style.display = (activeMode().steps || []).length ? "" : "none";
    els.projBtn.style.display = state.mode === "account" ? "none" : "";
  }

  /* Лента шагов шире контейнера → включаем затухание у правого края. */
  function markCut() {
    // Шаги не влезли → сначала снимаем номера (это 6×24px), и только если
    // всё равно не влезли — включаем затухание края. Подписи не режем никогда:
    // «UGC» и «Доктор» по иконке неопознаваемы, шаги — тем более.
    if (els.stages) {
      els.stages.classList.remove("is-tight");
      let cut = els.stages.scrollWidth - els.stages.clientWidth > 4;
      if (cut) {
        els.stages.classList.add("is-tight");
        cut = els.stages.scrollWidth - els.stages.clientWidth > 4;
      }
      els.stages.classList.toggle("is-cut", cut);
    }
    if (els.objs) {
      els.objs.classList.toggle("is-cut", els.objs.scrollWidth - els.objs.clientWidth > 4);
    }
  }

  /* На мобиле чип проекта живёт в рейке: в полке место занято шагами.
     Узел один и тот же — просто переезжает, обработчик сохраняется. */
  function placeProjectChip() {
    if (!els.projBtn) return;
    const mobile = window.innerWidth <= 700;
    const wantRail = mobile && els.projBtn.parentElement !== els.rail;
    const wantShelf = !mobile && els.projBtn.parentElement !== els.shelf;
    if (wantRail) els.rail.insertBefore(els.projBtn, els.modes);
    else if (wantShelf) els.shelf.insertBefore(els.projBtn, els.sep);
  }

  function paintProjectChip() {
    const p = state.project;
    els.projName.textContent = p.name || (state.mode === "account" ? "Кабинет" : "Новый проект");
    if (p.cover) { els.projImg.src = p.cover; els.projImg.style.display = ""; }
    else els.projImg.style.display = "none";
  }

  function renderObjects() {
    const def = stepDef(state.step);
    const trackScope = Boolean(def && def.scope === "track");
    const show = trackScope && state.tracks.length > 0;
    if (!show) els.head.classList.remove("has-objects");
    els.objects.hidden = !show;
    els.head.classList.toggle("has-objects", show);
    if (!show) return;

    els.objs.innerHTML = "";
    state.tracks.forEach((t) => {
      const b = el("button", "wb-obj" + (String(t.id) === String(state.trackId) ? " on" : ""));
      b.type = "button";
      b.dataset.track = t.id;
      const cov = el("span", "wb-obj-cover");
      if (t.cover) {
        const img = el("img");
        img.src = t.cover;
        img.alt = "";
        img.style.cssText = "width:100%;height:100%;object-fit:cover;border-radius:4px";
        cov.appendChild(img);
      } else cov.textContent = t.pos;
      b.append(cov, el("span", "wb-obj-name", t.title));
      b.appendChild(el("span", "wb-dot " + (t.states[state.step] || "empty")));
      on(b, "click", () => goTrack(t.id));
      els.objs.appendChild(b);
    });

    const idx = state.tracks.findIndex((t) => String(t.id) === String(state.trackId));
    els.objCount.textContent = `${idx + 1} / ${state.tracks.length}`;
    const cur = $(`.wb-obj[data-track="${CSS.escape(String(state.trackId))}"]`, els.objs);
    if (cur) cur.scrollIntoView({ block: "nearest", inline: "center" });
  }

  /* ── «Что дальше»: одна акцентная кнопка на весь экран ── */

  /* Где именно сейчас крутится генерация — туда и ведёт кнопка.
     Уход в другой режим/шаг очередь не рвёт, поэтому кнопка кликабельна. */
  function busyAction() {
    // Ключи ПАНЕЛЕЙ, а не шагов: карточка трека знает только setup|board|anim.
    const order = ["board", "anim", "setup"];
    const pick = (t) => order.find((k) => t.states[k] === "busy");
    const cur = activeTrack();
    const track = cur && pick(cur) ? cur : state.tracks.find((t) => pick(t));
    if (!track) return null;
    const pane = pick(track);
    const step = (activeMode().steps || []).find((s) => s.pane === pane);
    return { label: "Генерится: " + track.title, cls: "is-busy",
             step: step ? step.id : null, track: track.id, cost: 0 };
  }

  /* «Что дальше» СПРАШИВАЕМ У РЕЖИМА. Захардкоженный список правил работал
     ровно для клипа и делал реестр режимов декорацией: сериал и UGC остались
     бы без единственной акцентной кнопки экрана. */
  function nextAction() {
    const mode = activeMode();
    if (state.mode === "account" || !mode.next) return null;
    if (anyBusy()) { const b = busyAction(); if (b) return b; }
    const rules = mode.next(state, stepState) || [];
    for (const r of rules) if (r.when()) return r;
    const un = state.tracks.find((t) => t.states.anim !== "done");
    if (un) {
      const label = T("modes.object." + (mode.object || "track") + ".one", "трек");
      return { label: `Следующий ${label}: ${un.title}`, step: "board", track: un.id, cost: 0 };
    }
    return { label: "Всё готово", cls: "is-done", step: null, cost: 0 };
  }

  function renderNext() {
    const a = nextAction();
    const btn = els.nextBtn;
    const shelfBtn = els.shelfNext;
    if (!a) { btn.hidden = true; shelfBtn.style.display = "none"; return; }

    const paint = (node, floating) => {
      node.innerHTML = "";
      if (a.cost) node.appendChild(el("span", floating ? "wb-next-cost" : "wb-cost", `−${a.cost} ⚡`));
      const lab = el("span", "wb-next-label", a.label + (a.step ? " →" : ""));
      node.appendChild(lab);
      node.className = (floating ? "wb-next " : "wb-shelf-next ") + (a.cls || "");
      node.disabled = !a.step;
    };
    paint(btn, true);
    paint(shelfBtn, false);
    btn.hidden = false;
    shelfBtn.style.display = "";

    const act = () => {
      if (!a.step) return;
      if (a.track) state.trackId = a.track;
      goStep(a.step);
    };
    btn.onclick = act;
    shelfBtn.onclick = act;
  }

  /* ─────────────── применение шага к реальной разметке ───────────────
     Работает и в демо, и на живом приложении: элементы помечаются
     data-nav-stage, карточки треков переключаются своими .stage-pane. */

  function applyStage() {
    const key = state.step;
    const stepPanel = (stepDef(key) || {}).panel || key;
    $$("[data-nav-stage]", els.body).forEach((node) => {
      const list = (node.getAttribute("data-nav-stage") || "").split(/\s+/).filter(Boolean);
      node.classList.toggle("wb-on", list.includes(stepPanel));
    });

    const def = stepDef(key);
    // paneKey — панель ВНУТРИ карточки трека (setup|board|anim). Раньше сюда
    // подставлялся id шага, и шаги «Сюжет»/«Монтаж» искали панели plot и
    // final, которых в разметке нет: первый гасил карточку целиком, второй
    // показывал пустое место.
    const paneKey = (def && def.pane) || "";
    const trackScope = Boolean(def && def.scope === "track");

    $$(".track-card", els.body).forEach((card) => {
      const mine = !trackScope || String(card.dataset.id) === String(state.trackId);
      card.hidden = !mine;
      if (!mine || !paneKey) return;
      $$(".stage-pane", card).forEach((p) => p.classList.toggle("on", p.dataset.stage === paneKey));
    });

    // Блок «+ добавить объект» — только на шаге настройки объекта (pane
    // "setup"): у клипа это «Треки», у UGC «Ролики», у сериала «Серии».
    const onSetup = Boolean(def && def.pane === "setup");
    $$(".add-track-panel, .add-track", els.body).forEach((d) => { d.hidden = !onSetup; });

    // Ни одна панель не подошла (режим ещё без контента, кабинет в модалке) —
    // показываем явное пустое состояние, а не белый лист.
    const matched = $$("[data-nav-stage]", els.body).some((n) => n.classList.contains("wb-on"));
    paintPlaceholder(!matched);

    if (typeof cfg.onStage === "function") cfg.onStage(state.mode, key, state.trackId);
    emit("stage", { mode: state.mode, step: key, trackId: state.trackId });
  }

  /* Очки, тариф, инициалы аватара. В живом приложении настоящий
     #points-badge усыновлён — свою пилюлю держим скрытой. */
  function renderUser() {
    const u = state.user;
    if (els.points && !els.points.dataset.adopted) {
      els.points.hidden = !u.points;
      els.points.textContent = u.points || "";
    }
    if (els.avatar) {
      const ini = String(u.name || "").trim().split(/\s+/).map((s) => s[0] || "").join("").slice(0, 2).toUpperCase();
      els.avatar.textContent = ini || "◎";
    }
    if (els.planBtn) els.planBtn.textContent = u.plan ? u.plan : "Тариф";
  }

  /* Заглушка для режимов и разделов, у которых пока нет разметки. */
  function paintPlaceholder(show) {
    if (!show) { if (els.placeholder) els.placeholder.hidden = true; return; }
    if (!els.placeholder) {
      els.placeholder = el("div", "panel wb-placeholder");
      els.body.appendChild(els.placeholder);
    }
    const ph = els.placeholder;
    ph.hidden = false;
    ph.innerHTML = "";
    const box = el("div", "wb-empty");
    if (state.mode === "account") {
      const def = ACCOUNT_STEPS.find((x) => x.id === state.step);
      box.append(el("span", "wb-empty-ico", (def && def.icon) || "👤"),
        el("h3", null, (def && def.title) || "Кабинет"),
        el("p", null, "Кабинет открывается отдельным окном — тариф, генерации и партнёрка живут там."));
      const b = el("button", "primary", "Открыть кабинет");
      b.type = "button";
      on(b, "click", () => openAccountPane(state.step));
      box.appendChild(b);
    } else {
      const m = activeMode();
      const st = activeSteps().find((x) => x.id === state.step);
      box.append(el("span", "wb-empty-ico", (st && st.icon) || m.icon),
        el("h3", null, (m.full || m.title) + (st ? " · " + st.title : "")),
        el("p", null, m.soon
          ? "Режим готовится. Полка шагов уже живая — сюда приедет содержимое."
          : "Здесь пока пусто."));
    }
    ph.appendChild(box);
  }

  function renderAll() {
    syncData();
    renderUser();
    renderModes();
    renderShelf();
    renderObjects();
    applyStage();
  }

  /* ───────────────────────────── роутер ─────────────────────────────
     #/<mode>[/<step>[/t<trackId>]] · #/account/<tab> · #/projects
     Бэк отдаёт SPA на любой путь кроме /api/*, хэш ничего не ломает. */

  function parseHash(h) {
    const raw = (h || location.hash || "").replace(/^#\/?/, "");
    const parts = raw.split("/").filter(Boolean);
    if (!parts.length) return { mode: "clip", step: null, trackId: null };
    if (parts[0] === "projects") return { mode: state.mode, step: state.step, trackId: state.trackId, drawer: "projects" };
    if (parts[0] === "account") return { mode: "account", step: parts[1] || "profile", trackId: null };
    const mode = MODES.some((m) => m.id === parts[0]) ? parts[0] : "clip";
    const step = parts[1] || null;
    let trackId = null;
    if (parts[2] && /^t/.test(parts[2])) trackId = parts[2].slice(1);
    return { mode, step, trackId };
  }

  function buildHash() {
    if (state.mode === "account") return `#/account/${state.step}`;
    const def = stepDef(state.step);
    const trackPart = def && def.scope === "track" && state.trackId ? `/t${state.trackId}` : "";
    return `#/${state.mode}/${state.step}${trackPart}`;
  }

  let silentHash = false;
  function pushHash() {
    const h = buildHash();
    if (location.hash === h) return;
    silentHash = true;
    location.hash = h;
    setTimeout(() => { silentHash = false; }, 0);
  }

  function applyRoute(r) {
    if (r.drawer) { openDrawer(r.drawer); return; }
    state.mode = r.mode;
    const steps = activeSteps();
    state.step = steps.some((s) => s.id === r.step) ? r.step : (steps[0] && steps[0].id) || "story";
    if (r.trackId) state.trackId = r.trackId;
    syncData();
    // шаг уровня трека без треков — откатываемся на «Треки»
    const def = stepDef(state.step);
    if (def && def.scope === "track" && !state.tracks.length) {
      const setup = activeSteps().find((x) => x.pane === "setup");
      if (setup) state.step = setup.id;
    }
    renderAll();
    emit("route", { mode: state.mode, step: state.step, trackId: state.trackId });
    if (els.body) els.body.style.animation = "none";
    requestAnimationFrame(() => { if (els.body) els.body.style.animation = ""; });
  }

  function goMode(id) {
    const m = MODES.find((x) => x.id === id);
    if (!m) return;
    // Режим-«переход»: у чата свой экран в app.js, и верстак его не рисует.
    // Клик по такой плитке должен вести туда, а не гасить рабочую область.
    if (typeof m.external === "function") { m.external(); return; }
    // ЯРЛЫК («3D Pixar») — не режим: он ведёт в чужой режим с преднастройкой.
    // Показываем его карточку, где написано, что это стиль поверх rap clips,
    // и там же лежит единственная кнопка действия.
    if (m.kind === "shortcut") {
      if (window.QlolModeMenu) { window.QlolModeMenu.open(id); }
      else if (typeof window.applyModeShortcut === "function") window.applyModeShortcut(id);
      return;
    }
    if (state.mode === id) return;
    // Режим = ВИД ОТКРЫТОГО ПРОЕКТА, и переключить его на месте нельзя:
    // сериал не превращается в клип, у них разные объекты второго уровня.
    // Раньше клик по чужому режиму кидал тост и открывал ящик проектов —
    // человек терял и контекст, и объяснение. Теперь открывается карточка
    // режима: что это, какой маршрут и одна кнопка действия.
    if ((m.projectKinds || []).length && !cfg.demo) {
      const sel = $("#project-select");
      const opts = sel ? Array.from(sel.options) : [];
      const have = opts.some((o) => m.projectKinds.includes(o.dataset.kind || "album"));
      if (window.QlolModeMenu) { window.QlolModeMenu.open(id); return; }
      if (!have) toast(`${m.full || m.title}: создай проект этого вида`);
      openDrawer("projects");
      return;
    }
    state.lastStep[state.mode] = state.step;
    state.mode = id;
    const steps = activeSteps();
    const back = state.lastStep[id];
    state.step = steps.some((s) => s.id === back) ? back : (steps[0] && steps[0].id) || "story";
    pushHash();
    renderAll();
    emit("mode", id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function goStep(id) {
    if (!activeSteps().some((s) => s.id === id)) return;
    state.step = id;
    state.lastStep[state.mode] = id;
    if (state.mode === "account") openAccountPane(id);
    pushHash();
    renderShelf();
    renderObjects();
    applyStage();
    if (els.body) els.body.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function goTrack(id) {
    state.trackId = id;
    pushHash();
    renderShelf();
    renderObjects();
    applyStage();
    emit("track", id);
  }

  function go(route) { applyRoute(parseHash(route)); pushHash(); }

  /* ─────────────────────── слои: ящик, поповер, шторка ───────────────────────
     Esc закрывает ПО СТЕКУ, а не всё разом. */

  function setQuiet(v) { document.body.classList.toggle("glass-quiet", v); }

  function anyLayer() { return state.drawer || state.pop || state.sheet; }

  function openDrawer(seg) {
    state.drawerSeg = seg || state.drawerSeg;
    state.drawer = true;
    els.drawer.hidden = false;
    els.scrim.hidden = false;
    requestAnimationFrame(() => {
      els.drawer.classList.add("is-open");
      els.scrim.classList.add("is-open");
    });
    els.burger.setAttribute("aria-expanded", "true");
    setQuiet(true);
    renderDrawer();
  }

  function closeDrawer() {
    state.drawer = false;
    els.drawer.classList.remove("is-open");
    els.scrim.classList.remove("is-open");
    els.burger.setAttribute("aria-expanded", "false");
    setTimeout(() => {
      if (!state.drawer) els.drawer.hidden = true;
      if (!anyLayer()) { els.scrim.hidden = true; setQuiet(false); }
    }, 240);
  }

  function renderDrawer() {
    $$(".wb-drawer-seg", els.drawer).forEach((b) => b.classList.toggle("on", b.dataset.seg === state.drawerSeg));
    const body = els.drawerBody;
    body.innerHTML = "";

    if (state.drawerSeg === "projects") {
      if (els.drawerNewBtn) body.appendChild(els.drawerNewBtn);
      else {
        const nb = el("button", "wb-drawer-new", "+ Новый проект");
        nb.type = "button";
        on(nb, "click", () => toast("Демо: тут откроется выбор вида проекта"));
        body.appendChild(nb);
      }
      const list = state.projects.length ? state.projects : [{ id: "cur", name: state.project.name || "Текущий проект", kind: state.project.kind || "клип", tracks: state.tracks.length }];
      list.forEach((p) => {
        const b = el("button", "wb-proj-card" + (p.current ? " on" : ""));
        b.type = "button";
        const cov = el("span", "wb-proj-cover", "♪");
        if (p.cover) { cov.textContent = ""; const i = el("img"); i.src = p.cover; i.alt = ""; i.style.cssText = "width:100%;height:100%;object-fit:cover;border-radius:8px"; cov.appendChild(i); }
        const main = el("div", "wb-proj-main");
        main.appendChild(el("div", "wb-proj-name", p.name));
        const meta = el("div", "wb-proj-meta");
        meta.appendChild(document.createTextNode(`${p.kind || "клип"} · ${p.tracks || 0} треков`));
        const dots = el("span", "wb-proj-dots");
        (p.dots || ["done", "done", "part", "", "", ""]).forEach((d) => dots.appendChild(el("i", d)));
        meta.appendChild(dots);
        main.appendChild(meta);
        b.append(cov, main);
        on(b, "click", () => {
          if (els.projSelect && p.id !== "cur") {
            els.projSelect.value = p.id;
            els.projSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
          state.project.name = p.name;
          paintProjectChip();
          closeDrawer();
        });
        body.appendChild(b);
      });
    } else if (state.drawerSeg === "chars") {
      body.appendChild(hint("🎭", "Персонажи альбома", "Библиотека героев: имя, фото-модельки, атрибуты. Открыть шаг «Персонажи».", () => { closeDrawer(); goStep("chars"); }, "Открыть шаг"));
    } else {
      body.appendChild(hint("🕘", "История", "Всё, что сгенерировано: клипы, кадры, видео. Фильтры по режиму, проекту и дате.", null, null));
    }
  }

  function hint(ico, title, text, action, actionLabel) {
    const box = el("div", "wb-empty");
    box.appendChild(el("span", "wb-empty-ico", ico));
    box.appendChild(el("h3", null, title));
    box.appendChild(el("p", null, text));
    if (action) {
      const b = el("button", "wb-drawer-new", actionLabel || "Открыть");
      b.type = "button";
      on(b, "click", action);
      box.appendChild(b);
    }
    return box;
  }

  function openAccount(step) {
    closePop();
    if (state.mode !== "account") state.lastStep[state.mode] = state.step;
    state.mode = "account";
    state.step = step || "profile";
    pushHash();
    renderAll();
    openAccountPane(state.step);
  }

  /* Кабинет живёт в app.js (ACC_TABS + openAccountModal). Мы его не переписываем:
     полка показывает вкладки, а содержимое открывает штатная функция. */
  function openAccountPane(step) {
    const def = ACCOUNT_STEPS.find((x) => x.id === step);
    if (!def) return;
    if (typeof window.openAccountModal === "function") window.openAccountModal(def.acc);
    else if (els.accBtn) els.accBtn.click();
  }

  function togglePop() { state.pop ? closePop() : openPop(); }

  function openPop() {
    state.pop = true;
    buildPop();
    els.pop.hidden = false;
    if (els.avatar) {
      const r = els.avatar.getBoundingClientRect();
      const w = 258;
      els.pop.style.top = `${Math.round(r.bottom + 8)}px`;
      els.pop.style.left = `${Math.round(Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)))}px`;
      els.avatar.setAttribute("aria-expanded", "true");
    }
    setQuiet(true);
  }

  function closePop() {
    state.pop = false;
    els.pop.hidden = true;
    if (els.avatar) els.avatar.setAttribute("aria-expanded", "false");
    if (!anyLayer()) setQuiet(false);
  }

  function buildPop() {
    const pop = els.pop;
    // сохранённые узлы app.js вынимаем, чистим, возвращаем
    const keep = [els.accBtn, els.logoutBtn].filter(Boolean);
    keep.forEach((n) => n.remove());
    pop.innerHTML = "";

    const head = el("div", "wb-pop-head");
    head.appendChild(el("b", null, state.user.name || "Гость"));
    head.appendChild(el("span", null, (state.user.plan || "FREE") + (state.user.points ? " · " + state.user.points : "")));
    pop.appendChild(head);

    ACCOUNT_STEPS.filter((s) => !s.admin || state.user.isAdmin).forEach((s) => {
      const b = el("button", "wb-pop-item");
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.append(el("span", "wb-pop-ico", s.icon), el("span", null, s.title));
      if (s.admin) b.appendChild(el("span", "wb-pop-tag", "админ"));
      on(b, "click", () => openAccount(s.id));
      pop.appendChild(b);
    });

    pop.appendChild(el("div", "wb-pop-sep"));

    const guide = el("button", "wb-pop-item");
    guide.type = "button";
    guide.append(el("span", "wb-pop-ico", "📖"), el("span", null, "Гайд"));
    on(guide, "click", () => {
      closePop();
      const link = $("#welcome-guide-link");
      if (link) link.click(); else toast("Гайд появится здесь");
    });
    pop.appendChild(guide);

    keep.forEach((n) => {
      n.classList.remove("hidden", "ghost");
      n.classList.add("wb-pop-item");
      if (n === els.logoutBtn) n.classList.add("danger");
      pop.appendChild(n);
    });
    if (!keep.length) {
      const out = el("button", "wb-pop-item danger");
      out.type = "button";
      out.append(el("span", "wb-pop-ico", "⏻"), el("span", null, "Выход"));
      on(out, "click", () => { closePop(); toast("Демо: выход"); });
      pop.appendChild(out);
    }
  }

  function openSheet(title, node) {
    state.sheet = title;
    els.sheetTitle.textContent = title;
    els.sheetBody.innerHTML = "";
    els.sheetBody.appendChild(node);
    els.sheet.hidden = false;
    els.scrim.hidden = false;
    requestAnimationFrame(() => {
      els.sheet.classList.add("is-open");
      els.scrim.classList.add("is-open");
    });
    setQuiet(true);
  }

  function closeSheet() {
    state.sheet = null;
    els.sheet.classList.remove("is-open");
    if (!state.drawer) els.scrim.classList.remove("is-open");
    setTimeout(() => {
      if (!state.sheet) els.sheet.hidden = true;
      if (!anyLayer()) { els.scrim.hidden = true; setQuiet(false); }
    }, 240);
  }

  function closeLayers() {
    if (state.pop) closePop();
    if (state.sheet) closeSheet();
    if (state.drawer) closeDrawer();
  }

  let toastTimer = null;
  function toast(text) {
    let node = $(".wb-toast");
    if (!node) { node = el("div", "wb-toast"); document.body.appendChild(node); }
    node.textContent = text;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 2600);
  }

  /* ───────────────────── скролл, клавиатура, клавиатура телефона ───────────────────── */

  function bindScroll() {
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const y = window.scrollY || document.documentElement.scrollTop;
        // гистерезис: включаем на 8px, выключаем на 2px — иначе дребезг
        if (!state.stuck && y > 8) { state.stuck = true; els.head.classList.add("is-stuck"); }
        else if (state.stuck && y < 3) { state.stuck = false; els.head.classList.remove("is-stuck"); }
      });
    };
    on(window, "scroll", onScroll, { passive: true });
    onScroll();
  }

  function bindKeys() {
    on(document, "keydown", (e) => {
      if (e.key === "Escape") {
        if (anyLayer()) { e.stopPropagation(); closeLayers(); }
        return;
      }
      const t = e.target;
      if (t && (t.matches("input, textarea, select") || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const steps = activeSteps();
      const si = steps.findIndex((s) => s.id === state.step);
      if (e.key === "[") { const p = steps[si - 1]; if (p) { e.preventDefault(); goStep(p.id); } }
      else if (e.key === "]") { const n = steps[si + 1]; if (n) { e.preventDefault(); goStep(n.id); } }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const ti = state.tracks.findIndex((x) => String(x.id) === String(state.trackId));
        const nt = state.tracks[ti + (e.key === "ArrowRight" ? 1 : -1)];
        if (nt) { e.preventDefault(); goTrack(nt.id); }
      } else if (e.key.toLowerCase() === "g") { e.preventDefault(); state.drawer ? closeDrawer() : openDrawer(); }
      else if (/^[1-9]$/.test(e.key)) {
        const m = segModes()[Number(e.key) - 1];
        if (m) { e.preventDefault(); goMode(m.id); }
      }
    });
  }

  function bindMobile() {
    // при открытой клавиатуре док уезжает, чтобы не наехать на поле
    on(document, "focusin", (e) => {
      if (window.innerWidth > 700) return;
      if (e.target && e.target.matches("input, textarea")) {
        els.dock.classList.add("is-down");
        els.nextBtn.classList.add("is-down");
      }
    });
    on(document, "focusout", () => {
      setTimeout(() => {
        els.dock.classList.remove("is-down");
        els.nextBtn.classList.remove("is-down");
      }, 140);
    });
  }

  function bindLayers() {
    on(els.burger, "click", () => (state.drawer ? closeDrawer() : openDrawer()));
    on(els.projBtn, "click", () => openDrawer("projects"));
    on(els.scrim, "click", closeLayers);
    on(document, "click", (e) => {
      if (!state.pop) return;
      if (els.pop.contains(e.target) || (els.avatar && els.avatar.contains(e.target))) return;
      closePop();
    });
    on(els.stripPrev, "click", () => {
      const i = state.tracks.findIndex((t) => String(t.id) === String(state.trackId));
      if (state.tracks[i - 1]) goTrack(state.tracks[i - 1].id);
    });
    on(els.stripNext, "click", () => {
      const i = state.tracks.findIndex((t) => String(t.id) === String(state.trackId));
      if (state.tracks[i + 1]) goTrack(state.tracks[i + 1].id);
    });
    on(window, "hashchange", () => { if (!silentHash) applyRoute(parseHash()); });
    on(window, "resize", () => { if (state.pop) openPop(); placeProjectChip(); markCut(); });
    on(els.stages, "scroll", markCut, { passive: true });
  }

  /* Поллинг app.js перерисовывает #tracks целиком — после каждой перерисовки
     возвращаем вид шага и пересобираем чипы. Наблюдаем только childList
     самого контейнера: наши переключения классов внутри карточек его не будят. */
  function watchApp() {
    const tracks = $(cfg.tracksSelector);
    if (tracks) {
      let t = null;
      new MutationObserver(() => {
        clearTimeout(t);
        t = setTimeout(() => {
          // Рейку режимов пересобираем ТОЛЬКО когда режим действительно
          // сменился: она перестраивает DOM целиком, а этот путь дёргает
          // поллинг раз в пару секунд.
          if (syncData()) renderModes();
          renderShelf(); renderObjects(); applyStage();
        }, 30);
      }).observe(tracks, { childList: true });
    }
    // Список проектов приезжает ПОСЛЕ первой отрисовки рейки, и у проекта без
    // объектов наблюдатель за #tracks не сработает никогда (пустой список
    // «меняется» на пустой без записей мутации). Поэтому вид первого
    // открытого проекта ловим по наполнению самого селекта.
    const projSel = $("#project-select");
    if (projSel) {
      new MutationObserver(() => {
        if (syncData()) { renderModes(); renderShelf(); renderObjects(); applyStage(); }
      }).observe(projSel, { childList: true });
    }
    const app = $(cfg.appSelector);
    if (app) {
      // Пока #app скрыт (лендинг/логин) — навигации на экране нет вообще,
      // включая плавающие слои, которые лежат в <body>, а не в #app.
      const sync = () => {
        const live = !app.classList.contains("hidden");
        const was = document.body.classList.contains("wb-on");
        document.body.classList.toggle("wb-on", live);
        if (live && !was) renderAll();
      };
      new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ["class"] });
      sync();
    }
  }

  /* ───────────────────────────── запуск ───────────────────────────── */

  function init(config) {
    if (booted) return window.QlolNav;
    cfg = Object.assign(cfg, config || {});
    if (!buildChrome()) return window.QlolNav;
    booted = true;

    tagPanels();
    adoptExisting();

    bindLayers();
    bindScroll();
    bindKeys();
    bindMobile();
    // wb-on ставит watchApp() по видимости #app: в демо он виден сразу,
    // в проде — только после showApp(). Безусловно включать нельзя.
    watchApp();
    applyRoute(parseHash(cfg.startRoute));
    pushHash();
    return window.QlolNav;
  }

  function setData(data) {
    if (!data) return;
    if (data.project) Object.assign(state.project, data.project);
    if (data.projects) state.projects = data.projects;
    if (data.user) Object.assign(state.user, data.user);
    if (data.tracks) cfg.getTracks = () => data.tracks;
    if (booted) renderAll();
  }

  /* Регистрация режима извне. Пишем В ОБЩИЙ реестр (MODES — это тот же
     массив, что и window.QlolModes.MODES), иначе новый режим увидел бы
     только верстак, а классическая шапка — нет. */
  function registerMode(mode) {
    if (!mode || !mode.id || MODES.some((m) => m.id === mode.id)) return;
    MODES.push(mode);
    if (booted) renderModes();
    if (window.QlolModeMenu) window.QlolModeMenu.sync();
  }

  window.QlolNav = {
    init, go, setData, registerMode, toast, closeLayers, openDrawer,
    refresh: () => { if (booted) renderAll(); },
    on: (ev, fn) => { if (listeners[ev]) listeners[ev].push(fn); },
    state, MODES, ACCOUNT_STEPS,
  };

  // Автозапуск на живом приложении. В демо страница вызывает init() сама.
  function autoBoot() {
    if (booted) return;
    if (document.body && document.body.dataset.navManual === "1") return;
    if ($("#app") && $("#tracks")) init({});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoBoot);
  else autoBoot();
})();
