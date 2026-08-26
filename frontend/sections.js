/* ═══════════════════════════════════════════════════════════════════════════
   lolq.ai — ВЕРХНЯЯ НАВИГАЦИЯ ПО РАЗДЕЛАМ, АКАДЕМИЯ И КАТАЛОГ ПРИЁМОВ

   Подключается ПОСЛЕ app.js и mode-menu.js:
       <script src="/modes.js?v=68"></script>
       <script src="/i18n.js?v=68"></script>
       <script src="/app.js?v=68"></script>
       <script src="/mode-menu.js?v=68"></script>
       <script src="/sections.js?v=68"></script>

   ═════════════ ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ПРАВКА app.js ═════════════
   app.js — 480 КБ и живой: в нём одновременно работают другие руки. Раздел,
   которого раньше не было, не должен требовать хирургии в общем файле; всё,
   что ему нужно от студии, — четыре глобальные функции (api, escHtml, ldMd,
   openModal) и объект project. Никаких id он не переименовывает.

   ═════════════════ ДВА ЯРУСА НАВИГАЦИИ, А НЕ ОДИН СПИСОК ═════════════════
   Верхняя строка отвечает на вопрос «ГДЕ Я» (студия, мастерская, академия,
   промты, музыка, кабинет). Тумблер режимов ниже отвечает на «ЧЕМ Я СНИМАЮ»
   (rap clips, UGC, сериал, мокапы). Свалить их в одну ленту — ровно та
   ошибка, из-за которой у конкурентов «Академия» стоит рядом с «Seedance»:
   разделы сервиса и инструменты внутри одного раздела не сравнимы между
   собой и не должны стоять в одном ряду.

   ═══════════ КНОПКИ ПЕРЕЕЗЖАЮТ, А НЕ КОПИРУЮТСЯ (важнейшее) ═══════════
   #chat-btn, #music-btn, #account-btn, #admin-btn ПЕРЕНОСЯТСЯ в новую ленту
   через appendChild. Это сохраняет и обработчики, и id, и класс .hidden,
   которым app.js управляет видимостью («Кабинет» появляется только у
   авторизованного, «Админка» — только у владельца). Нарисовать рядом свои
   копии значило бы завести второе состояние видимости и синхронизировать его
   наблюдателем — то есть городить механизм там, где хватает переноса узла.
   Та же техника, что у верстака (nav.js), и по той же причине.

   ═══════════════════════════ МИНИМАЛИЗМ ═══════════════════════════
   В навигации НЕТ огня. Активный раздел показан рамкой и весом текста, а не
   заливкой: огонь в сервисе один и стоит на главном действии экрана (снять,
   оплатить). Если подсветить огнём ещё и вкладку, главное действие перестанет
   быть заметным — это и есть цена «яркой» навигации. См. docs/DESIGN_SYSTEM.md.

   Публичный API: window.QlolSections
     mount()            — построить ленту (зовётся само)
     go(id)             — открыть раздел
     paint()            — пересчитать активный
     openAcademy(slug)  — академия, сразу на уроке
     openLibrary(key)   — каталог приёмов, сразу на карточке
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolSections) return;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* Подпись из общего словаря. Запасной текст нужен демо-странице, где
     словаря нет вовсе (nav-preview.html). */
  function T(key, fallback) {
    const v = typeof window.t === "function" ? window.t(key) : "";
    return v || fallback || "";
  }
  function TF(key, vars, fallback) {
    const v = typeof window.t === "function" ? window.t(key, vars) : "";
    return v || fallback || "";
  }
  const esc = (v) => (typeof window.escHtml === "function"
    ? window.escHtml(v)
    : String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c])));
  const lang = () => (typeof LANG === "string" ? LANG : "en");
  const num = (n) => (typeof tNum === "function" ? tNum(n) : String(n));

  /* Форма слова по числу. Без неё витрина писала «1 уроков» и «3 наборов по
     тарифу»: у русского три формы, и подставлять одну — не мелочь оформления,
     а текст, который читается как машинный. Правило выбора живёт в tPlural
     (i18n.js), формы — в словаре, поэтому язык без склонений не потребует ни
     строчки кода здесь. */
  function plural(n, key) {
    const forms = typeof window.tRaw === "function" ? window.tRaw(key) : "";
    if (!Array.isArray(forms) || typeof window.tPlural !== "function") return "";
    return window.tPlural(n, forms);
  }

  function shown(sel) {
    const n = $(sel);
    return Boolean(n) && !n.classList.contains("hidden");
  }

  /* Какой раздел открыт СЕЙЧАС — считается из DOM, а не хранится в
     переменной. Переменная разъехалась бы с экраном при первом же переходе,
     сделанном мимо ленты (ссылка, кнопка «← Студия» внутри мастерской,
     карточка режима). */
  let sheet = "";          // "academy" | "prompts" | "" — что открыто в модалке

  const SECTIONS = [
    {
      id: "studio",
      label: () => T("nav.sections.studio", "Студия"),
      title: () => T("nav.titles.studio", ""),
      active: () => shown("#app") && !shown("#chat") && !shown("#music") && !sheet,
      open() {
        // Выход из мастерской знает только app.js — у него там поллинг и
        // адресная строка. Своей копии этой логики здесь быть не должно.
        if (shown("#chat") && typeof window.qlolLeaveMake === "function") {
          window.qlolLeaveMake();
          return;
        }
        if (shown("#music") && window.QlolMusic && window.QlolMusic.leave) {
          window.QlolMusic.leave();
          return;
        }
        if (typeof window.showApp === "function") window.showApp();
      },
    },
    { id: "make", adopt: "#chat-btn", active: () => shown("#chat") },
    {
      // Тренды — витрина шаблонов «фото → ролик»: стоит первой из
      // контент-разделов, потому что это самый короткий путь новичка
      // к результату — ему не нужен ни трек, ни проект.
      id: "trends",
      label: () => T("nav.sections.trends", "Тренды"),
      title: () => T("nav.titles.trends", ""),
      active: () => sheet === "trends" || Boolean($("#trends-page")),
      open: () => openTrends(),
    },
    {
      // Заработок: партнёрские продукты — сгенерил ролик, запостил со своей
      // ссылкой, получаешь долю с заказов. Витрина на механике трендов.
      id: "earn",
      label: () => T("nav.sections.earn", "Заработок"),
      title: () => T("nav.titles.earn", ""),
      active: () => sheet === "earn",
      open: () => openEarn(),
    },
    {
      id: "academy",
      label: () => T("nav.sections.academy", "Академия"),
      title: () => T("nav.titles.academy", ""),
      active: () => sheet === "academy",
      open: () => openAcademy(),
    },
    {
      id: "prompts",
      label: () => T("nav.sections.prompts", "Промты"),
      title: () => T("nav.titles.prompts", ""),
      active: () => sheet === "prompts",
      open: () => openLibrary(),
    },
    { id: "music", adopt: "#music-btn", active: () => shown("#music") },
    { id: "account", adopt: "#account-btn", active: () => false },
    { id: "admin", adopt: "#admin-btn", active: () => false },
  ];

  /* ───────────────────────────── лента разделов ───────────────────────────── */

  function mount() {
    const bar = $(".topbar");
    if (!bar || $("#tb-sections")) return;
    const nav = document.createElement("nav");
    nav.id = "tb-sections";
    nav.className = "tb-sections";
    nav.setAttribute("aria-label", T("nav.aria", "Разделы сервиса"));

    SECTIONS.forEach((s) => {
      if (s.adopt) {
        const node = $(s.adopt);
        if (!node) return;
        node.classList.add("tb-sec");
        node.dataset.sec = s.id;
        nav.appendChild(node);        // ПЕРЕНОС: обработчики и .hidden целы
        return;
      }
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tb-sec";
      b.id = "sec-" + s.id;
      b.dataset.sec = s.id;
      b.textContent = s.label();
      if (s.title()) b.title = s.title();
      b.addEventListener("click", () => go(s.id));
      nav.appendChild(b);
    });

    // Лента встаёт СРАЗУ ЗА МАРКОЙ: разделы — верхний ярус, и читать их надо
    // первыми, слева направо, а не выискивать справа между кнопкой выхода и
    // переключателем языка, где они лежали раньше.
    const brand = $("#brand");
    if (brand && brand.parentNode === bar) brand.after(nav);
    else bar.prepend(nav);
    bar.classList.add("has-sections");
    paint();
  }

  function go(id) {
    const s = SECTIONS.find((x) => x.id === id);
    if (!s) return;
    if (id !== "trends") closeTrendsPage(false);
    if (s.adopt) {
      const node = $(s.adopt);
      if (node) node.click();
      return;
    }
    if (s.open) s.open();
    paint();
  }

  /* Ищем ПО data-sec, а не по id: у раздела может быть две кнопки — в обычной
     шапке и в рельсе верстака (?nav=1). Поиск по id нашёл бы только первую,
     и во втором месте подсветка молча не работала бы. */
  function nodesOf(s) {
    return s.adopt ? [$(s.adopt)].filter(Boolean)
                   : $$(`[data-sec="${s.id}"]`);
  }

  function paint() {
    SECTIONS.forEach((s) => {
      const on = Boolean(s.active && s.active());
      nodesOf(s).forEach((node) => {
        node.classList.toggle("on", on);
        // aria-current, а не только класс: скринридер обязан знать, где он,
        // и подсветка рамкой для него не существует.
        if (on) node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
      });
    });
  }

  function relabel() {
    SECTIONS.forEach((s) => {
      if (s.adopt) return;                     // у перенесённых подпись из data-i18n
      nodesOf(s).forEach((b) => {
        const cap = $(".wb-mode-cap", b) || b;
        cap.textContent = s.label();
        if (s.title()) b.title = s.title();
      });
    });
  }

  /* ВЕРСТАК (?nav=1) ГАСИТ .topbar ЦЕЛИКОМ и строит свою рельсу. Пока этого
     не учитывали, флаг ?nav=1 просто уносил «Академию» и «Промты» с экрана:
     разделы существовали, но открыть их было нечем. Своих кнопок у верстака
     для них нет и быть не может — он рисует РЕЖИМЫ из modes.js, а раздел не
     режим. Поэтому дописываем две кнопки прямо в его рельсу, в стилях
     верстака (.wb-mode), а не тащим туда чужую ленту: рядом с режимами она
     выглядела бы вторым тумблером.

     Всё это — только если рельса реально появилась. nav.js грузится
     асинхронно и вообще может быть выключен, поэтому ждём наблюдателем и
     ничего не делаем, если ждать нечего. */
  function mountRail() {
    const rail = $(".wb-rail");
    if (!rail) return false;          // верстак выключен или ещё не построен
    if ($(".wb-sec")) return true;    // уже дописались
    const own = SECTIONS.filter((s) => !s.adopt && s.id !== "studio");
    // Точка вставки СДВИГАЕТСЯ за каждой кнопкой: вставляя обе сразу после
    // .wb-modes, вторая встала бы перед первой и порядок разделов перевернулся.
    let after = $(".wb-modes", rail);
    own.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "wb-mode wb-sec";
      b.dataset.sec = s.id;
      b.innerHTML = `<span class="wb-mode-cap"></span>`;
      $(".wb-mode-cap", b).textContent = s.label();
      if (s.title()) b.title = s.title();
      b.addEventListener("click", () => go(s.id));
      if (after && after.parentNode === rail) after.after(b);
      else rail.appendChild(b);
      after = b;
    });
    paint();
    return true;
  }

  /* ────────────────────── общая обвязка листов раздела ────────────────────── */

  /* Академия и каталог живут в ОБЩЕЙ модалке студии (openModal), а не в
     собственном оверлее: у неё уже есть закрытие по Esc, по клику мимо и
     системная кнопка «назад» в Telegram. Второй оверлей означал бы второй
     набор этих правил, и один из двух отстал бы. */
  function openSheet(kind, title, build) {
    if (typeof window.openModal !== "function") return;
    sheet = kind;
    window.openModal(title, build, { wide: true });
    watchClose();
    paint();
  }

  let watching = false;
  function watchClose() {
    if (watching) return;
    const ov = $("#modal-overlay");
    if (!ov) return;
    watching = true;
    // Закрытие модалки происходит в четырёх местах app.js (крестик, клик мимо,
    // Esc, кнопка Telegram). Ловим не события, а РЕЗУЛЬТАТ — появление класса
    // hidden: так подсветка раздела не зависит от того, каким из четырёх
    // способов человек вышел.
    new MutationObserver(() => {
      if (ov.classList.contains("hidden") && sheet) { sheet = ""; paint(); }
    }).observe(ov, { attributes: true, attributeFilter: ["class"] });
  }

  function busy(box) {
    box.innerHTML = `<p class="muted">${esc(T("common.loading", "загружаю…"))}</p>`;
  }
  function failed(box, key) {
    box.innerHTML = `<p class="muted">${esc(T(key, T("common.loadFail", "не загрузилось")))}</p>`;
  }
  /* Тост свой, и это не изобретение велосипеда: единственный тост в проекте
     живёт в nav.js, а nav.js подключается по флагу ?nav=1 — то есть в обычной
     студии его нет, и сообщение «применено к 6 сценам» не увидел бы никто.
     Если верстак всё-таки загружен, отдаём сообщение ему. */
  function toast(text) {
    if (!text) return;
    if (window.QlolNav && window.QlolNav.toast) { window.QlolNav.toast(text); return; }
    let box = $("#sec-toast");
    if (!box) {
      box = document.createElement("div");
      box.id = "sec-toast";
      box.className = "sec-toast";
      box.setAttribute("role", "status");
      document.body.appendChild(box);
    }
    box.textContent = text;
    box.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => box.classList.remove("on"), 2600);
  }

  /* Текст ошибки на языке интерфейса — тем же переводчиком, что и вся студия. */
  function errorText(e) {
    if (typeof window.errText === "function") return window.errText(e);
    return (e && e.message) || T("academy.failed", "");
  }

  /* ═══════════════════════════════ АКАДЕМИЯ ═══════════════════════════════
     Курс = уровень (см. backend/learn.py, COURSES): реестра «какие уроки в
     каком курсе» на клиенте нет и быть не должно — новый урок обязан
     появляться в академии сам, из своего же фронтматтера.

     Замок висит НЕ на тексте. Все уроки читаются без подписки: это двигатель
     органики и прямое требование learn.py. Тариф решает, применится ли
     АРТЕФАКТ урока — набор приёмов. Поэтому у закрытого курса замок стоит на
     кнопке «применить», а не поверх текста. */

  let academy = null;             // ответ /api/learn
  let academyLang = "";

  async function loadAcademy(force) {
    if (!force && academy && academyLang === lang()) return academy;
    academyLang = lang();
    academy = await api(`/api/learn?lang=${encodeURIComponent(lang())}`);
    return academy;
  }

  /* ─────────────────────────── тренды ─────────────────────────── */
  // Витрина шаблонов: карточка с примером → загрузка фото → готовый ролик.
  // Ни трека, ни проекта: вся режиссура зашита владельцем в шаблон.

  let trendPoll = null;

  function openEarn() {
    openSheet("earn", T("earn.title", "Заработок с lolq.ai"), async (body) => {
      busy(body);
      let d;
      try { d = await api("/api/earn"); } catch (e) { failed(body, "trends.failed"); return; }
      body.innerHTML = "";
      // Дашборд партнёра — до карточек: свои цифры важнее витрины.
      if (d.authorized) {
        try {
          const st = await api("/api/earn/stats");
          const dash = document.createElement("div");
          dash.className = "earn-dash";
          dash.innerHTML = `
            <span><b>${st.clicks}</b> ${T("earn.dClicks", "переходов")}</span>
            <span><b>${st.sales}</b> ${T("earn.dSales", "продаж")}</span>
            <span><b>${(st.earned_kopeks / 100).toFixed(0)}₽</b> ${T("earn.dEarned", "начислено")}</span>
            <span><b>${(st.paid_kopeks / 100).toFixed(0)}₽</b> ${T("earn.dPaid", "выплачено")}</span>`;
          body.appendChild(dash);
        } catch (e) { /* дашборд не обязателен для витрины */ }
      }
      const intro = document.createElement("div");
      intro.className = "earn-howto muted";
      intro.innerHTML = T("earn.howto",
        "<b>Как это работает:</b> 1) выбери продукт — он уже встроен в шаблон, менять его нельзя · "
        + "2) опиши свой стиль: ИИ-блогер, мульт, 3D — что угодно · "
        + "3) запости ролик со своей ссылкой · "
        + "4) получай 10–20% с продаж; оплачивается только ПЕРВАЯ покупка каждого клиента.");
      body.appendChild(intro);
      if (!(d.products || []).length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = T("earn.empty", "Продукты скоро появятся.");
        body.appendChild(empty);
        return;
      }
      const grid = document.createElement("div");
      grid.className = "trend-grid";
      d.products.forEach((t) => {
        const card = document.createElement("div");
        card.className = "trend-card";
        card.innerHTML = `
          ${t.sample_url
            ? `<video src="${t.sample_url}" muted loop playsinline preload="metadata"
                 ${t.poster_url ? `poster="${t.poster_url}"` : ""}></video>`
            : t.poster_url ? `<img src="${t.poster_url}" alt="" loading="lazy" />`
            : `<div class="trend-ph"></div>`}
          <div class="trend-meta">
            <b>${esc(t.title)}</b>
            <span class="earn-reward">${esc(t.reward_note || "")}</span>
            <span class="muted">${t.duration_sec} ${T("trends.sec", "с")} · ⚡ ${t.cost_points}</span>
          </div>
          <input type="text" class="earn-style" maxlength="200"
                 placeholder="${T("earn.stylePh", "свой стиль: мульт, ИИ-блогер, 3D…")}" />
          ${t.my_link ? `
          <button type="button" class="earn-link ghost">${T("earn.copy", "Скопировать мою ссылку")}</button>` : ""}
          <label class="trend-go">
            <span>${T("earn.make", "Сделать ролик с продуктом")}</span>
            <input type="file" accept="image/*" hidden />
          </label>
          <div class="trend-state hidden"></div>`;
        const linkBtn = card.querySelector(".earn-link");
        if (linkBtn) linkBtn.addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(t.my_link); } catch (e) {}
          linkBtn.textContent = T("earn.copied", "Скопирована ✓ — вставляй в пост");
        });
        const inp = card.querySelector("input");
        inp.addEventListener("change", () => trendMake(card, t, inp));
        grid.appendChild(card);
      });
      body.appendChild(grid);
    });
  }

  function closeTrendsPage(updateUrl) {
    const page = $("#trends-page");
    if (!page) return;
    page.remove();
    const app = $("#app");
    if (app) app.classList.remove("trends-view");
    sheet = "";
    if (updateUrl !== false && location.pathname === "/trends") history.pushState({}, "", "/studio");
    paint();
  }

  const NEW_TRENDS = new Set([
    "agamemnon", "fallen angel", "bullet time", "cyclope", "pigeons",
    "pearl earring", "argus", "dolphin ride", "skatedog", "monet muse",
    "puffin ride", "lost in a book", "penguin ride",
  ]);

  const TREND_TITLES_RU = {
    "Agamemnon":"Агамемнон", "Earth Zoom":"Зум Земли", "Ink Riot":"Чернильный бунт",
    "Fallen Angel":"Падший ангел", "Bullet Time":"Замедление времени", "Fairytale Castle":"Сказочный замок",
    "Comic":"Комикс", "Cold Vision":"Холодное зрение", "Cyclope":"Циклоп", "Particles":"Частицы",
    "Mighty Fighter":"Могучий боец", "Windows":"Окна", "Canvas":"Холст", "Pigeons":"Голуби",
    "Tracking":"Трекинг", "Superstar":"Суперзвезда", "Pearl Earring":"Жемчужная серёжка", "LSD":"ЛСД",
    "Blue Depth":"Синяя глубина", "Palette":"Палитра", "Moonwalk":"Лунная походка",
    "Knight's Diary":"Дневник рыцаря", "Argus":"Аргус", "Fragments":"Фрагменты",
    "2000's Paparazzi":"Папарацци нулевых", "Overexposed":"Пересвет", "Dolphin Ride":"На дельфине",
    "Sticker Peel":"Отклеенная наклейка", "Multiverse":"Мультивселенная", "Skatedog":"Пёс на скейте",
    "Noir":"Нуар", "Casual Monster Slayer":"Будничный охотник на монстров", "Selfie Twin":"Селфи-двойник",
    "Sketch":"Эскиз", "Monet Muse":"Муза Моне", "Akrill":"Акрилл", "Puffin Ride":"Верхом на тупике",
    "Magazine":"Журнал", "Lost in a Book":"Затерянный в книге", "Penguin Ride":"На пингвине",
    "Cannabis":"Каннабис", "3D Render":"3D-рендер", "Action Figure":"Экшен-фигурка", "Bubbles":"Пузыри",
    "Orbit 360":"Орбита 360", "Orbital Presence":"Орбитальное присутствие", "Acid":"Кислота",
    "Race Track":"Гоночная трасса", "Flash Comic":"Флеш-комикс", "Paper":"Бумага",
    "Random Glow":"Случайное свечение", "Toxic":"Токсичный", "Broken Mirror":"Разбитое зеркало",
    "Hand Paint":"Ручная роспись", "Lava":"Лава", "Marble":"Мрамор", "Modern":"Модерн",
    "Ocean":"Океан", "Origami":"Оригами", "Two Color":"Два цвета", "Ultraviolet":"Ультрафиолет",
    "Vintage":"Винтаж",
  };

  function trendTitle(title) {
    if (lang() === "ru") return TREND_TITLES_RU[title] || title;
    if (title === "Кинотеатр смотрит на тебя") return "The Theater Is Watching You";
    return title;
  }

  function trendVisual(title, index) {
    let hash = 2166136261;
    for (const ch of String(title || "")) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619);
    const hue = Math.abs(hash) % 360;
    const hue2 = (hue + 70 + (index * 17)) % 360;
    const variant = Math.abs(hash >> 8) % 8;
    return `<div class="trend-ph trend-visual" data-visual="${variant}"
      style="--th:${hue};--th2:${hue2};--td:${-(index % 9) * .19}s">
      <i></i><i></i><i></i></div>`;
  }

  function openTrends() {
    closeTrendsPage(false);
    const app = $("#app");
    if (!app) return;
    sheet = "trends";
    app.classList.add("trends-view");
    if (location.pathname !== "/trends") history.pushState({}, "", "/trends");
    const page = document.createElement("main");
    page.id = "trends-page";
    page.className = "trends-page";
    page.innerHTML = `<section class="trends-hero">
      <h1>${lang() === "ru" ? "вирусные тренды" : "viral trends"}</h1>
      <div class="trends-filters" role="tablist"></div>
    </section><section class="trends-catalog"><p class="muted trends-loading">${esc(T("common.loading", "загружаю…"))}</p></section>`;
    app.appendChild(page);
    paint();
    const body = $(".trends-catalog", page);
    (async () => {
      let d;
      try {
        d = await api("/api/trends");
      } catch (e) {
        failed(body, "trends.failed");
        return;
      }
      body.innerHTML = "";
      if (!(d.presets || []).length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = T("trends.empty", "Шаблоны скоро появятся.");
        body.appendChild(empty);
        return;
      }
      const grid = document.createElement("div");
      grid.className = "trend-grid";
      d.presets.forEach((t, index) => {
        const card = document.createElement("div");
        card.className = `trend-card trend-tile-${index % 12}`;
        card.dataset.title = t.title;
        const displayTitle = trendTitle(t.title);
        card.innerHTML = `
          ${t.sample_url
            ? `<video src="${t.sample_url}" muted loop playsinline preload="metadata"
                 ${t.poster_url ? `poster="${t.poster_url}"` : ""}></video>`
            : t.poster_url ? `<img src="${t.poster_url}" alt="" loading="lazy" />`
            : trendVisual(t.title, index)}
          <div class="trend-card-name">${esc(displayTitle)}</div>
          <label class="trend-card-action">
            <span>${lang() === "ru" ? "Сгенерить" : "Generate"}</span>
            <input type="file" accept="image/*" hidden />
          </label>
          <div class="trend-state hidden"></div>`;
        const vid = card.querySelector("video");
        if (vid) {
          card.addEventListener("mouseenter", () => vid.play().catch(() => {}));
          card.addEventListener("mouseleave", () => vid.pause());
        }
        const inp = card.querySelector("input");
        inp.addEventListener("change", () => trendMake(card, t, inp));
        grid.appendChild(card);
      });
      body.appendChild(grid);
      const filters = $(".trends-filters", page);
      const tags = [{ title: T("trends.filterAll", "Все"), display: T("trends.filterAll", "Все"), all: true },
        ...d.presets.map((t) => ({ title: t.title, display: trendTitle(t.title) }))];
      tags.forEach((item, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "trends-filter" + (i === 0 ? " on" : "");
        const fresh = NEW_TRENDS.has(String(item.title).toLowerCase());
        b.innerHTML = `${esc(item.display)}${fresh ? `<small>${lang() === "ru" ? "новое" : "new"}</small>` : ""}`;
        b.addEventListener("click", () => {
          $$(".trends-filter", filters).forEach((x) => x.classList.toggle("on", x === b));
          $$(".trend-card", grid).forEach((c) => c.classList.toggle("hidden", !item.all && c.dataset.title !== item.title));
        });
        filters.appendChild(b);
      });
    })();
  }

  async function trendMake(card, t, inp) {
    const f = inp.files && inp.files[0];
    if (!f) return;
    if (!d0Authorized()) {
      location.hash = "#/login";
      return;
    }
    const state = card.querySelector(".trend-state");
    const go = card.querySelector(".trend-card-action, .trend-go");
    go.classList.add("hidden");
    state.classList.remove("hidden");
    state.textContent = T("trends.uploading", "загружаю фото…");
    const fd = new FormData();
    fd.append("photo", f);
    const styleEl = card.querySelector(".earn-style");
    if (styleEl && styleEl.value.trim()) fd.append("style", styleEl.value.trim());
    let job;
    try {
      job = await api(`/api/trends/${t.id}/make`, { method: "POST", body: fd });
    } catch (e) {
      state.textContent = String(e.message || e);
      go.classList.remove("hidden");
      return;
    }
    state.textContent = T("trends.working", "рисую кадр…");
    const tick = async () => {
      let st;
      try {
        st = await api(`/api/trends/jobs/${job.job_id}`);
      } catch (e) {
        state.textContent = String(e.message || e);
        return;
      }
      if (st.status === "error") {
        state.textContent = st.error || T("trends.errored", "не получилось — токены возвращены");
        go.classList.remove("hidden");
        return;
      }
      if (st.status === "done" && st.video_url) {
        state.innerHTML = `<video src="${st.video_url}" controls autoplay loop playsinline></video>
          <a class="trend-dl" href="${st.video_url}" download>${T("trends.download", "Скачать")}</a>
          <button type="button" class="trend-share ghost">${T("trends.share", "Поделиться ссылкой")}</button>`;
        // Петля Remix: автор публикует ролик, получает ссылку /v/{id} — на
        // странице кнопка «Сделать так же», и каждый репост приводит нового.
        state.querySelector(".trend-share").onclick = async (ev) => {
          const b = ev.target;
          try {
            const r = await api(`/api/trends/jobs/${job.job_id}/share`, { method: "POST" });
            try { await navigator.clipboard.writeText(r.url); } catch (e) {}
            b.textContent = T("trends.shared", "Ссылка скопирована ✓ — постись!");
          } catch (e) { b.textContent = String(e.message || e); }
        };
        return;
      }
      state.textContent = st.status === "video"
        ? T("trends.animating", "оживляю…")
        : T("trends.working", "рисую кадр…");
      trendPoll = setTimeout(tick, 4000);
    };
    tick();
  }

  function d0Authorized() {
    try { return Boolean(window.me && window.me.user); } catch (e) { return true; }
  }

  function openAcademy(slug) {
    openSheet("academy", T("academy.title", "Академия"), async (body) => {
      busy(body);
      try {
        await loadAcademy(true);
      } catch (e) {
        failed(body, "academy.failed");
        return;
      }
      if (slug) renderLesson(body, slug);
      else renderCourses(body);
    });
  }

  function courseHead() {
    const p = academy.progress || { done: 0, total: 0, percent: 0 };
    return `<div class="ac-head">
      <p class="ac-lead">${esc(T("academy.lead", ""))}</p>
      <div class="ac-bar" role="img" aria-label="${esc(TF("academy.progressAll",
        { done: p.done, total: p.total, percent: p.percent,
          word: plural(p.total, "academy.lessonWord") }, ""))}">
        <span style="width:${Math.max(0, Math.min(100, p.percent))}%"></span>
      </div>
      <p class="ac-prog">${esc(TF("academy.progressAll",
        { done: num(p.done), total: num(p.total), percent: p.percent,
          word: plural(p.total, "academy.lessonWord") }, ""))}</p>
    </div>`;
  }

  function renderCourses(body) {
    const rows = academy.courses || [];
    body.innerHTML = courseHead() + `<div class="ac-courses">${rows.map(courseCard).join("")}</div>`;
    $$(".ac-lesson", body).forEach((b) =>
      b.addEventListener("click", () => renderLesson(body, b.dataset.slug)));
  }

  function courseCard(c) {
    const lessons = (c.lessons || []).map((l) => `
      <li>
        <button type="button" class="ac-lesson${l.done ? " done" : ""}" data-slug="${esc(l.slug)}">
          <span class="ac-lesson-t">${esc(l.title)}</span>
          <span class="ac-lesson-m">${esc(TF("academy.minutes", { n: l.minutes }, l.minutes + " min"))}${
            l.pack ? " · " + esc(T("academy.hasPack", "с набором")) : ""}</span>
        </button>
      </li>`).join("");
    const outcome = (c.outcome || []).map((x) => `<li>${esc(x)}</li>`).join("");
    // Замок курса — честная цифра «столько наборов закрыто», а не абстрактное
    // «PRO». Ноль закрытых наборов = никакого замка вообще.
    const lock = c.locked_packs
      ? `<span class="ac-lock">${esc(TF("academy.lockedPacks",
          { n: c.locked_packs,
            word: plural(c.locked_packs, "academy.packWord") }, ""))}</span>` : "";
    return `<article class="ac-course">
      <header>
        <span class="ac-course-num">${esc(String(c.level))}</span>
        <h3>${esc(c.title)}</h3>
        ${lock}
      </header>
      <p class="ac-course-lead">${esc(c.lead)}</p>
      <p class="ac-course-meta">${esc(TF("academy.courseMeta",
        { n: c.total, min: c.minutes, done: c.done,
          word: plural(c.total, "academy.lessonWord") }, ""))}</p>
      <div class="ac-bar sm"><span style="width:${c.percent}%"></span></div>
      ${outcome ? `<div class="ac-outcome">
        <b>${esc(T("academy.outcome", "В конце курса"))}</b>
        <ul>${outcome}</ul></div>` : ""}
      <ul class="ac-lessons">${lessons}</ul>
    </article>`;
  }

  async function renderLesson(body, slug) {
    busy(body);
    let data;
    try {
      data = await api(`/api/learn/${encodeURIComponent(slug)}?lang=${encodeURIComponent(lang())}`);
    } catch (e) {
      failed(body, "academy.failed");
      return;
    }
    const md = typeof window.ldMd === "function" ? window.ldMd(data.markdown || "") : "";
    const pack = data.pack_card;
    body.innerHTML = `
      <div class="ac-lesson-page">
        <button type="button" class="ac-back ghost">${esc(T("academy.back", "← Все курсы"))}</button>
        <article class="ac-text">${md}</article>
        ${pack ? artifactBlock(pack, data) : ""}
        <div class="row ac-foot">
          <button type="button" class="ac-done ghost">${esc(
            data.done ? T("academy.undone", "Снять отметку")
                      : T("academy.markDone", "Прочитал"))}</button>
          <a class="ac-perma" href="${esc(data.url || "")}"
             >${esc(T("academy.openPage", "открыть страницей"))}</a>
        </div>
      </div>`;
    $(".ac-back", body).addEventListener("click", () => renderCourses(body));
    $(".ac-done", body).addEventListener("click", async () => {
      try {
        await api(`/api/learn/${encodeURIComponent(slug)}/done`,
                  { method: "POST", body: { done: !data.done } });
        await loadAcademy(true);
        renderLesson(body, slug);
      } catch (e) { toast(errorText(e)); }
    });
    const apply = $(".ac-apply", body);
    if (apply) apply.addEventListener("click", () => applyPack(pack));
  }

  /* Артефакт урока — то, ради чего урок вообще открывают второй раз.
     «Урок, после которого нечего нажать, — не урок, а статья». */
  function artifactBlock(pack, lesson) {
    const shots = (pack.shots || []).map((s) =>
      `<li>${esc(s.label)}</li>`).join("");
    const cta = pack.locked
      ? `<button type="button" class="ac-apply-locked ghost" disabled>${
          esc(TF("academy.lockedTier", { plan: planName(pack.tier) }, ""))}</button>`
      : `<button type="button" class="primary ac-apply">${
          esc(T("academy.apply", "Применить в проект"))}</button>`;
    return `<section class="ac-art">
      <span class="ac-art-tag">${esc(T("academy.artifact", "Артефакт урока"))}</span>
      <h4>${esc(pack.label)}</h4>
      <p class="muted">${esc(pack.desc)}</p>
      <ul class="ac-art-shots">${shots}</ul>
      ${lesson.preset_card ? `<p class="ac-art-preset">${
        esc(T("academy.withPreset", "Каркас"))}: ${esc(lesson.preset_card.label)}</p>` : ""}
      <p class="ac-art-note muted">${esc(T("academy.applyNote", ""))}</p>
      ${cta}
    </section>`;
  }

  function planName(tier) {
    const map = { free: "FREE", pro: "PRO", pro_max: "PRO MAX", studio: "ULTRA" };
    return map[tier] || String(tier || "").toUpperCase();
  }

  /* ═══════════════════ ПРИМЕНЕНИЕ НАБОРА И ОДНОГО ПРИЁМА ═══════════════════
     Считает подстановку СЕРВЕР (POST .../apply), пишет обычный PATCH сцены.
     Второй копии подстановки на клиенте нет намеренно: первая же правка
     формулировки приёма разъехалась бы с тем, что применяет урок. */

  /* Открытый проект берём НАПРЯМУЮ из app.js. `project` там объявлен через
     let, то есть в window его нет вовсе (лексическая привязка скрипта, а не
     свойство объекта) — обращение через window.project молча вернуло бы
     undefined, и «применить» уверенно сообщало бы «сначала создай трек»
     человеку с открытым альбомом. */
  function tracksOf() {
    const p = typeof project !== "undefined" ? project : null;
    return (p && p.tracks) || [];
  }

  function slotFields(keys) {
    return (keys || []).map((k) => `
      <label class="ac-slot">
        <span>${esc(T("library.slot." + k, k))}</span>
        <input data-slot="${esc(k)}" placeholder="${esc(T("library.slotPh", ""))}" />
      </label>`).join("");
  }

  function readSlots(root) {
    const out = {};
    $$("[data-slot]", root).forEach((i) => {
      const v = String(i.value || "").trim();
      if (v) out[i.dataset.slot] = v;
    });
    return out;
  }

  /* Набор ложится на ВЕСЬ трек: приёмы в нём идут в порядке сцен, и разбивать
     их по одному значило бы потерять то единственное, чем набор отличается от
     шести отдельных карточек, — порядок. */
  function applyPack(pack) {
    const tracks = tracksOf();
    if (!tracks.length) { toast(T("academy.noTracks", "")); return; }
    window.openModal(pack.label, (box) => {
      box.innerHTML = `
        <p class="muted">${esc(T("academy.chooseTrackHint", ""))}</p>
        <label class="ac-slot"><span>${esc(T("academy.chooseTrack", "Куда применить"))}</span>
          <select class="ac-track">${tracks.map((tr) => `
            <option value="${tr.id}">${esc(tr.title || tr.name || ("#" + tr.id))} · ${
              esc(TF("academy.scenesN", { n: (tr.scenes || []).length,
                word: plural((tr.scenes || []).length, "academy.sceneWord") }, ""))}</option>`).join("")}
          </select>
        </label>
        <div class="ac-slots">${slotFields(pack.slots_hint)}</div>
        <p class="muted ac-slot-note">${esc(T("academy.slotsNote", ""))}</p>
        <div class="row"><button type="button" class="primary ac-go">${
          esc(T("academy.apply", "Применить"))}</button>
          <span class="status ac-status"></span></div>`;
      $(".ac-go", box).addEventListener("click", async () => {
        const st = $(".ac-status", box);
        const trackId = Number($(".ac-track", box).value) || 0;
        const track = tracks.find((x) => x.id === trackId);
        const scenes = (track && track.scenes) || [];
        if (!scenes.length) { st.textContent = T("academy.needScenes", ""); return; }
        st.textContent = T("common.saving", "…");
        try {
          const res = await api(`/api/packs/${encodeURIComponent(pack.key)}/apply`,
                                { method: "POST", body: { slots: readSlots(box) } });
          const patches = res.scenes || [];
          // Приёмов в наборе может быть больше, чем сцен в треке, — тогда
          // применяем сколько влезло и ГОВОРИМ ОБ ЭТОМ. Молча обрезать
          // означало бы «применилось», когда применилась половина.
          const n = Math.min(patches.length, scenes.length);
          for (let i = 0; i < n; i += 1) {
            const p = { ...patches[i] };
            delete p.shot_key;
            await api(`/api/scenes/${scenes[i].id}`, { method: "PATCH", body: p });
          }
          if (res.styles && res.styles.length) {
            await api(`/api/tracks/${trackId}/style`, {
              method: "POST",
              body: { style_keys: res.styles, preset: res.preset || "" },
            });
          }
          if (typeof window.loadProject === "function") await window.loadProject();
          toast(n < patches.length
            ? TF("academy.appliedPart", { n: n, total: patches.length,
                                          word: plural(n, "academy.sceneWordDat") }, "")
            : TF("academy.applied", { n: n,
                                      word: plural(n, "academy.sceneWordDat") }, ""));
          window.closeModal();
        } catch (e) {
          st.textContent = errorText(e);
        }
      });
    }, {});
  }

  /* Один приём ложится на ОДНУ сцену — её человек и выбирает. */
  function applyShot(shot) {
    const tracks = tracksOf();
    if (!tracks.length) { toast(T("academy.noTracks", "")); return; }
    window.openModal(shot.label, (box) => {
      const first = tracks[0];
      box.innerHTML = `
        <label class="ac-slot"><span>${esc(T("academy.chooseTrack", "Куда применить"))}</span>
          <select class="ac-track">${tracks.map((tr) => `
            <option value="${tr.id}">${esc(tr.title || tr.name || ("#" + tr.id))}</option>`).join("")}
          </select></label>
        <label class="ac-slot"><span>${esc(T("library.chooseScene", "На какую сцену"))}</span>
          <select class="ac-scene">${sceneOptions(first)}</select></label>
        <div class="ac-slots">${slotFields((shot.slots || []).map((s) => s.key))}</div>
        <p class="muted ac-slot-note">${esc(T("academy.slotsNote", ""))}</p>
        <div class="row"><button type="button" class="primary ac-go">${
          esc(T("library.apply", "Применить"))}</button>
          <span class="status ac-status"></span></div>`;
      $(".ac-track", box).addEventListener("change", (e) => {
        const tr = tracks.find((x) => x.id === Number(e.target.value));
        $(".ac-scene", box).innerHTML = sceneOptions(tr);
      });
      $(".ac-go", box).addEventListener("click", async () => {
        const st = $(".ac-status", box);
        const sceneId = Number($(".ac-scene", box).value) || 0;
        if (!sceneId) { st.textContent = T("academy.needScenes", ""); return; }
        st.textContent = T("common.saving", "…");
        try {
          const res = await api(`/api/shots/${encodeURIComponent(shot.key)}/apply`,
                                { method: "POST", body: { slots: readSlots(box) } });
          const p = { ...(res.scene || {}) };
          delete p.shot_key;
          await api(`/api/scenes/${sceneId}`, { method: "PATCH", body: p });
          if (typeof window.loadProject === "function") await window.loadProject();
          toast(TF("academy.applied", { n: 1, word: plural(1, "academy.sceneWordDat") }, ""));
          window.closeModal();
        } catch (e) {
          st.textContent = errorText(e);
        }
      });
    }, {});
  }

  function sceneOptions(track) {
    const scenes = (track && track.scenes) || [];
    if (!scenes.length) return `<option value="">${esc(T("academy.needScenes", ""))}</option>`;
    return scenes.map((s, i) => `<option value="${s.id}">${
      esc(TF("library.sceneN", { n: i + 1 }, "#" + (i + 1)))}${
      s.lyric_line ? " · " + esc(String(s.lyric_line).slice(0, 40)) : ""}</option>`).join("");
  }

  /* ═════════════════════ КАТАЛОГ ПРОМТОВ («Промты») ═════════════════════
     ЧЕТЫРЕ РАЗНЫЕ СУЩНОСТИ, И ПУТАТЬ ИХ НЕЛЬЗЯ:
       сценарий — «что происходит во ВСЁМ клипе» (сюжет проекта и заметка);
       сцена    — «как снят ОДИН кадр» (шесть полей кадра разом);
       движение — «что делает камера и тело» (заменяет motion_prompt);
       свет     — «откуда светит и какого цвета» (дописывается в оба кадра).
     Пятая, стиль, здесь отсутствует намеренно и навсегда: текст фирменных
     пятнадцати закрыт, наружу уходят только подпись и описание.

     САМ КАТАЛОГ ЖИВЁТ В library.js. Здесь остались только вход в него и
     диалоги применения приёма и набора — их зовёт ещё и артефакт урока, и
     вторая копия разъехалась бы с ним на первой правке. */

  function openLibrary(key, context) {
    openSheet("prompts", T("promptbase.title", T("library.title", "Промты")),
      async (body) => {
        if (!window.QlolLibrary) { failed(body, "library.failed"); return; }
        await window.QlolLibrary.render(body, { key: key, ctx: context || null });
      });
  }

  /* ───────────────── высота шапки как размер, а не как число ─────────────────
     Шапка ЛИПКАЯ и лежит ВЫШЕ модалки (z-index 60 против 50), и это
     сознательно: лист раздела открывается в общей модалке, и переход
     «Академия → Промты» обязан быть одним кликом по ленте, а не «закрой,
     потом открой». Плата за это — модалка обязана начинаться ПОД шапкой,
     иначе её собственные заголовок и крестик уезжают под ленту. Ровно это и
     происходило: на телефоне лист академии открывался вообще без видимого
     способа закрыться — крестик был под шапкой, а фона рядом с карточкой на
     375px почти нет, оставался один Esc.

     Число в стилях тут не годится: рядов у шапки один, два или три (см.
     медиазапросы sections.css), и захардкоженные 94px разъехались бы с
     реальностью на первом же переносе. Поэтому высоту публикует тот, кто её
     знает, — сам браузер, наблюдателем за размером.

     +12px — липкий отступ шапки (`.topbar { top: 10px }` плюс граница). */
  function trackBarHeight() {
    const bar = $(".topbar");
    const root = document.documentElement;
    if (!bar) return;
    const publish = () => {
      // Верстак (?nav=1) гасит шапку целиком — тогда резервировать нечего.
      const h = bar.getClientRects().length ? bar.getBoundingClientRect().height : 0;
      root.style.setProperty("--tb-safe", `${Math.round(h ? h + 12 : 0)}px`);
    };
    publish();
    if (typeof ResizeObserver === "function") new ResizeObserver(publish).observe(bar);
    else window.addEventListener("resize", publish);
  }

  /* ─────────────────────────────── запуск ─────────────────────────────── */

  function boot() {
    mount();
    trackBarHeight();
    if (typeof window.onLangChange === "function") {
      window.onLangChange(() => {
        const trendsOpen = Boolean($("#trends-page"));
        relabel();
        academy = null;
        if (window.QlolLibrary) window.QlolLibrary.forget();
        // Страница трендов собрана JS, поэтому data-i18n её не обновит.
        // Перестраиваем её на новом языке без перезагрузки и смены URL.
        if (trendsOpen) openTrends();
        paint();
      });
    }
    // Экран мог смениться мимо ленты: ссылкой, кнопкой «← Студия» внутри
    // мастерской, карточкой режима. Подсветка обязана это заметить.
    ["#app", "#chat", "#music"].forEach((sel) => {
      const n = $(sel);
      if (n) new MutationObserver(paint).observe(n, { attributes: true, attributeFilter: ["class"] });
    });
    // Рельса верстака появляется позже нас: nav.js подключается скриптом в
    // конце страницы и только по флагу. Ждём её появления и уходим, как
    // только дописались; без флага наблюдатель просто никогда не сработает.
    if (!mountRail()) {
      const app = $("#app");
      if (app) {
        const obs = new MutationObserver(() => { if ($(".wb-rail")) { mountRail(); obs.disconnect(); } });
        obs.observe(app, { childList: true, subtree: true });
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  /* Обвязка листа отдаётся library.js ЦЕЛИКОМ, а не переписывается там заново.
     Тост, перевод, экранирование, список треков и диалоги применения приёма и
     набора — это ровно те места, где две копии означали бы два поведения: одно
     в академии, другое в каталоге. Отсюда же берётся openSheet, поэтому
     подсветка раздела продолжает работать, кто бы лист ни рисовал. */
  window.QlolSections = {
    mount, go, paint, openAcademy, openLibrary,
    ui: {
      T, TF, esc, lang, num, plural, toast, errorText, busy, failed,
      planName, tracksOf, sceneOptions, slotFields, readSlots,
      applyShot, applyPack,
    },
  };
})();
