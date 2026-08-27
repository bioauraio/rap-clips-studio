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
      /* Школа живёт ВНУТРИ #app своей страницей и переменную sheet не
         трогает — без этой проверки подсвечивались сразу два раздела:
         «Студия» (потому что #app виден) и «Школа». */
      active: () => shown("#app") && !shown("#chat") && !shown("#music") && !sheet
        && !(window.QlolSchool && window.QlolSchool.visible()),
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
      // Маркетинг: всё про продукты и бренды одним хабом — предметная
      // съёмка (мокапы), UGC-ролики и партнёрский заработок. Раньше здесь
      // стоял «Заработок»; /earn продолжает открывать его напрямую.
      id: "marketing",
      label: () => T("nav.sections.marketing", "Маркетинг"),
      title: () => T("nav.titles.marketing", ""),
      active: () => sheet === "earn" || Boolean($("#marketing-page")),
      open: () => openMarketing(),
    },
    {
      // «Школа» — полноценная страница с курсами и базой знаний. Модалка
      // академии осталась жива и открывается по клику на урок базы знаний:
      // текст урока-маркдауна читается в шторке, курс — на странице.
      id: "academy",
      label: () => T("nav.sections.academy", "Школа"),
      title: () => T("nav.titles.academy", ""),
      active: () => sheet === "academy"
        || Boolean(window.QlolSchool && window.QlolSchool.visible()),
      open: () => (window.QlolSchool
        ? window.QlolSchool.open("courses") : openAcademy()),
    },
    /* «Промты» ИЗ ПУБЛИЧНОГО МЕНЮ УБРАНЫ 27.08.2026 по решению владельца.
       Каталог никуда не делся и открывается оттуда, где он нужен: кнопкой
       на карточке кадра (.s-lib — сразу с номером кадра), кнопкой «Промты»
       в Генераторе и из админки. Раздел верхнего яруса он не заслуживал:
       это инструмент внутри работы, а не место, куда «идут». */
    { id: "music", adopt: "#music-btn", active: () => shown("#music") },
    // «Профиль» и «Админка» НЕ в ленте: профиль — правый угол шапки
    // (капсула с авой в .tb-user), админка — компактная шестерёнка рядом.
  ];

  /* ───────────────────────────── лента разделов ───────────────────────────── */

  function mount() {
    const bar = $(".topbar");
    if (!bar || $("#tb-sections")) return;
    const nav = document.createElement("nav");
    nav.id = "tb-sections";
    nav.className = "tb-sections";
    nav.setAttribute("aria-label", T("nav.aria", "Разделы сервиса"));

    // Иконка раздела — для мобильного сегмента (подпись мелко под значком).
    const ICONS = { studio: "🎬", make: "✨", trends: "🔥", marketing: "📦",
                    academy: "🎓", prompts: "📝", music: "🎵" };
    SECTIONS.forEach((s) => {
      if (s.adopt) {
        const node = $(s.adopt);
        if (!node) return;
        node.classList.add("tb-sec");
        node.dataset.sec = s.id;
        if (ICONS[s.id]) node.dataset.ico = ICONS[s.id];
        nav.appendChild(node);        // ПЕРЕНОС: обработчики и .hidden целы
        return;
      }
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tb-sec";
      b.id = "sec-" + s.id;
      b.dataset.sec = s.id;
      if (ICONS[s.id]) b.dataset.ico = ICONS[s.id];
      b.textContent = s.label();
      if (s.title()) b.title = s.title();
      b.addEventListener("click", () => go(s.id));
      nav.appendChild(b);
    });

    // Лента встаёт СРАЗУ ЗА МАРКОЙ: разделы — верхний ярус, и читать их надо
    // первыми, слева направо, а не выискивать справа между кнопкой выхода и
    // переключателем языка, где они лежали раньше.
    // Подложка активного пункта — ОДНА на всю ленту и ездит под ним, а не
    // заливка на каждой кнопке. Заливка мигает: она появляется в одном месте
    // и исчезает в другом, и глаз не связывает эти два события. Подложка
    // едет — переход читается как переключение тумблера.
    const ind = document.createElement("span");
    ind.className = "tb-seg-ind";
    ind.setAttribute("aria-hidden", "true");
    nav.prepend(ind);

    /* Тумблер этапов ВЫНОСИМ ИЗ ШАПКИ В BODY. Он закреплён position:fixed,
       а у шапки есть backdrop-filter — любой backdrop-filter у предка делает
       его containing block для fixed-потомков, и панель прилипала к шапке
       вместо края экрана (лезла на «Профиль» сверху и на режимную строку на
       телефоне). Узел тот же, id тот же: app.js ищет его глобально. */
    const jump = $("#stage-jump");
    if (jump && jump.parentNode !== document.body) document.body.appendChild(jump);

    profileMenu();

    const brand = $("#brand");
    if (brand && brand.parentNode === bar) brand.after(nav);
    else bar.prepend(nav);
    bar.classList.add("has-sections");
    paint();
  }

  /* МЕНЮ ПРОФИЛЯ. «выйти» стояло голой кнопкой в шапке и удлиняло правый
     край ради действия, которое делают раз в месяц. Теперь по клику на
     «Профиль» открывается список: Кабинет, Админка (владельцу), Выйти.
     Оригинальные кнопки НЕ дублируются, а нажимаются — у них свои
     обработчики в app.js, и вторая копия означала бы второе поведение. */
  function profileMenu() {
    const btn = $("#account-btn");
    const user = $(".tb-user");
    if (!btn || !user || $("#tb-profile-menu")) return;
    const menu = document.createElement("div");
    menu.id = "tb-profile-menu";
    menu.className = "tb-pmenu hidden";
    user.appendChild(menu);

    let pass = false;                 // пропуск клика к «родному» обработчику
    const hide = () => menu.classList.add("hidden");
    const item = (label, onPick) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tb-pitem";
      b.textContent = label;
      b.addEventListener("click", () => { hide(); onPick(); });
      return b;
    };
    const build = () => {
      menu.innerHTML = "";
      menu.appendChild(item(T("top.account", "Кабинет"), () => {
        pass = true;
        btn.click();
      }));
      const adm = $("#admin-btn");
      if (adm && !adm.classList.contains("hidden")) {
        menu.appendChild(item(T("top.adminTitle", "Админка"), () => adm.click()));
      }
      const save = $("#save-account-btn");
      if (save && !save.classList.contains("hidden")) {
        menu.appendChild(item(save.textContent || T("top.saveAccount", ""),
          () => save.click()));
      }
      // Тема — пунктом меню: с мобильной шапки переключатель убран совсем,
      // а прятать настройку глубже кабинета незачем. Клик листает по кругу
      // авто → светлая → тёмная; «авто» — тёмная после 21:00 и до 8:00.
      {
        const names = { auto: ["авто", "auto"], light: ["светлая", "light"],
                        dark: ["тёмная", "dark"], system: ["системная", "system"] };
        const cur = () => localStorage.getItem("rc_theme") || "auto";
        const label = () => (T("nav.themeWord", "Тема") + ": "
          + (names[cur()] || names.auto)[LANG === "ru" ? 0 : 1]);
        const b = document.createElement("button");
        b.type = "button";
        b.className = "tb-pitem";
        b.textContent = label();
        b.addEventListener("click", (e) => {
          e.stopPropagation();               // меню не закрываем: листают подряд
          const next = { auto: "light", light: "dark", dark: "auto" }[cur()] || "auto";
          if (typeof window.applyTheme === "function") window.applyTheme(next);
          else localStorage.setItem("rc_theme", next);
          b.textContent = label();
        });
        menu.appendChild(b);
      }
      const out = $("#logout-btn");
      if (out) {
        menu.appendChild(item(out.textContent || T("top.logout", "выйти"),
          () => out.click()));
      }
    };
    btn.addEventListener("click", (e) => {
      if (pass) { pass = false; return; }
      e.preventDefault();
      e.stopPropagation();
      build();
      menu.classList.toggle("hidden");
    }, true);
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target) && e.target !== btn) hide();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  }

  function go(id) {
    const s = SECTIONS.find((x) => x.id === id);
    if (!s) return;
    if (id !== "trends") closeTrendsPage(false);
    if (id !== "marketing") closeMarketingPage(false);
    // Школа — такая же страница внутри #app: уходя в другой раздел, её
    // нужно убрать, иначе два раздела окажутся открыты одновременно.
    if (id !== "academy" && window.QlolSchool) window.QlolSchool.close(false);
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

  /* Подложку двигаем ПОСЛЕ расстановки классов и только если активный пункт
     виден: у скрытых кнопок (музыка гостю) ширина 0, и подложка схлопнулась
     бы в точку посреди ленты. Нет активного — подложка гаснет. */
  function moveIndicator() {
    const nav = $("#tb-sections");
    const ind = nav && $(".tb-seg-ind", nav);
    if (!nav || !ind) return;
    const all = $$(".tb-sec.on", nav);
    const on = all[all.length - 1];
    if (!on || !on.offsetWidth) { ind.style.opacity = "0"; return; }
    ind.style.opacity = "1";
    ind.style.width = on.offsetWidth + "px";
    ind.style.height = on.offsetHeight + "px";
    ind.style.transform =
      `translate(${on.offsetLeft - nav.clientLeft}px, ${on.offsetTop}px)`;
  }

  /* Высота шапки — в переменную: под ней живёт экран Генератора, а шапка на
     телефоне занимает три строки. Фиксированное число в CSS срезало бы низ
     ленты сообщений ровно на высоту лишней строки. */
  function measureBar() {
    const bar = $(".topbar");
    if (!bar) return;
    document.documentElement.style.setProperty("--tbh", bar.offsetHeight + "px");
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
    // requestAnimationFrame: сразу после смены классов ширина кнопки ещё
    // старая (жирный шрифт активного пункта её меняет), и подложка встала бы
    // на полпикселя мимо.
    requestAnimationFrame(() => { moveIndicator(); measureBar(); });
  }

  // Ширина ленты меняется от языка, поворота экрана и появления «Кабинета»
  // после входа: подложка обязана переехать вместе с кнопкой.
  window.addEventListener("resize", () => { moveIndicator(); measureBar(); });

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
      // СВОЯ сетка, а не .trend-grid: та — masonry из абсолютных плиток под
      // витрину трендов, и партнёрская карточка в ней разъезжалась (пустое
      // превью 9:16 и текст враспор). Здесь карточка товарная: превью
      // фиксированной высоты, компактный блок текста, действия одной строкой.
      const grid = document.createElement("div");
      grid.className = "earn-grid";
      d.products.forEach((t) => {
        const card = document.createElement("div");
        card.className = "earn-card";
        card.innerHTML = `
          <div class="earn-shot">
            ${t.sample_url
              ? `<video src="${t.sample_url}" muted loop playsinline preload="metadata"
                   ${t.poster_url ? `poster="${t.poster_url}"` : ""}></video>`
              : t.poster_url ? `<img src="${t.poster_url}" alt="" loading="lazy" />`
              : `<span class="earn-ph">📦</span>`}
            ${t.reward_note ? `<span class="earn-reward">${esc(t.reward_note)}</span>` : ""}
          </div>
          <div class="earn-body">
            <b class="earn-name">${esc(t.title)}</b>
            <span class="muted earn-meta">${t.duration_sec} ${T("trends.sec", "с")} · ⚡ ${t.cost_points}</span>
            <input type="text" class="earn-style" maxlength="200"
                   placeholder="${T("earn.stylePh", "свой стиль: мульт, ИИ-блогер, 3D…")}" />
            <div class="earn-acts">
              <label class="trend-go earn-go">
                <span>${T("earn.make", "Сделать ролик с продуктом")}</span>
                <input type="file" accept="image/*" hidden />
              </label>
              ${t.my_link ? `
              <button type="button" class="earn-link ghost">${T("earn.copy", "Скопировать мою ссылку")}</button>` : ""}
            </div>
          </div>
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

  /* ─────────────────────────── маркетинг ───────────────────────────
     Хаб «всё про продукты и бренды»: три стеклянные карточки — мокапы, UGC
     и партнёрка. Своя страница на механике трендов (#trends-page): студия
     прячется классом trends-view, human-URL /marketing, закрытие при уходе
     в любой другой раздел — через go(). */

  /* Общая база предметов/персонажей: сетка карточек с фото и поиском.
     Правка открывает ТЕ ЖЕ модалки студии (openItemModal / досье героя) —
     второго редактора не заводим. */
  async function openBase(kind) {
    const isItems = kind === "items";
    const title = isItems ? T("marketing.items", "Предметы")
                          : T("marketing.chars", "Персонажи");
    openSheet("base", title, async (body) => {
      busy(body);
      let rows = [];
      try {
        rows = isItems
          ? (await api("/api/items/all")).items || []
          : (await api("/api/characters/all")).characters || [];
      } catch (e) { failed(body, "trends.failed"); return; }
      body.innerHTML = `
        <input type="search" class="base-search"
               placeholder="${T("common.search", "поиск…")}" />
        <div class="base-grid"></div>`;
      const grid = $(".base-grid", body);
      const paintRows = () => {
        const q = ($(".base-search", body).value || "").trim().toLowerCase();
        grid.innerHTML = "";
        const list = rows.filter((r) =>
          !q || ((r.title || r.name || "").toLowerCase().includes(q)));
        if (!list.length) {
          grid.innerHTML = `<p class="muted">${T("base.empty", "пока пусто")}</p>`;
          return;
        }
        list.forEach((r) => {
          const url = isItems ? r.url : r.photo_url;
          const name = r.title || r.name || "—";
          const b = document.createElement("button");
          b.type = "button";
          b.className = "base-card";
          b.innerHTML = (url
            ? `<img src="${url}" alt="" loading="lazy" />`
            : `<span class="base-ph">${isItems ? "📦" : "👤"}</span>`)
            + `<span class="base-cap">${esc(name)}</span>`;
          b.addEventListener("click", () => {
            // Правка живёт в студии: уходим туда и открываем досье.
            if (isItems && window.qlolOpenItem) window.qlolOpenItem(r.track_id);
            else if (!isItems && window.qlolOpenChar) window.qlolOpenChar(r.id);
          });
          grid.appendChild(b);
        });
      };
      paintRows();
      $(".base-search", body).addEventListener("input", paintRows);
    });
  }

  function closeMarketingPage(updateUrl) {
    const page = $("#marketing-page");
    if (!page) return;
    page.remove();
    const app = $("#app");
    if (app && !$("#trends-page")) app.classList.remove("trends-view");
    if (sheet === "marketing") sheet = "";
    if (updateUrl !== false && location.pathname === "/marketing") {
      history.pushState({}, "", "/studio");
    }
    paint();
  }

  /* Увести в студию сразу в нужный режим: тот же путь, что клик по чипу
     режима, — pick() открывает карточку режима с кнопкой «Открыть/Создать». */
  function goStudioMode(modeId) {
    closeMarketingPage(false);
    if (location.pathname === "/marketing") history.pushState({}, "", "/studio");
    const studio = SECTIONS.find((x) => x.id === "studio");
    if (studio && studio.open) studio.open();
    if (window.QlolModeMenu && window.QlolModeMenu.pick) {
      window.QlolModeMenu.pick(modeId);
    }
    paint();
  }

  function openMarketing() {
    if (window.QlolSchool) window.QlolSchool.close(false);
    closeTrendsPage(false);
    closeMarketingPage(false);
    const app = $("#app");
    if (!app) return;
    sheet = "marketing";
    app.classList.add("trends-view");
    if (location.pathname !== "/marketing") history.pushState({}, "", "/marketing");
    const page = document.createElement("main");
    page.id = "marketing-page";
    page.className = "trends-page marketing-page";
    page.innerHTML = `<section class="trends-hero">
      <h1>${esc(T("marketing.title", "Маркетинг"))}</h1>
      <p>${esc(T("marketing.lead", ""))}</p>
      ${window.lolqHowto ? window.lolqHowto("marketing") : ""}
    </section><section class="mk-grid"></section>`;
    const grid = $(".mk-grid", page);
    const cards = [
      { icon: "📦",
        t: T("marketing.mockups", "Мокапы — предметная съёмка товара"),
        d: T("marketing.mockupsNote", "Кадры упаковки для карточек и рекламы — по одному фото."),
        go: () => goStudioMode("mockup") },
      { icon: "🤳",
        t: T("marketing.ugc", "UGC / блогеры"),
        d: T("marketing.ugcNote", "ИИ-блогер рассказывает про твой продукт как живой человек."),
        go: () => goStudioMode("ugc") },
      { icon: "💸",
        t: T("marketing.earn", "Заработок с lolq.ai"),
        d: T("marketing.earnNote", "Постишь ролики с продуктами — получаешь долю с продаж."),
        go: () => openEarn() },
      // Общие базы: те же сущности, что в студии, отдельным входом.
      { icon: "🧴",
        t: T("marketing.items", "Предметы"),
        d: T("marketing.itemsNote", "Общая база предметов всех проектов: фото, описание, модельки."),
        go: () => openBase("items") },
      { icon: "👥",
        t: T("marketing.chars", "Персонажи"),
        d: T("marketing.charsNote", "Общая база героев: лица держатся во всех проектах и режимах."),
        go: () => openBase("chars") },
    ];
    cards.forEach((c, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "mk-card";
      // Мини-галерея «как это выглядит»: у мокапов — три превью шаблонов
      // (или стилизованные плашки, пока превью не сгенерированы), у UGC и
      // заработка — по одной тематической плашке.
      const gal = i === 0
        ? `<span class="mk-card-gallery" data-mk-gal>
             <span class="mk-gal-ph g1">🏛️</span>
             <span class="mk-gal-ph g2">🧊</span>
             <span class="mk-gal-ph g3">🛒</span></span>`
        : `<span class="mk-card-gallery">
             <span class="mk-gal-ph g${i + 1}">${i === 1 ? "🤳" : "💸"}</span></span>`;
      b.innerHTML = `<span class="mk-ico" aria-hidden="true">${c.icon}</span>
        <b>${esc(c.t)}</b><span class="mk-note">${esc(c.d)}</span>${gal}`;
      b.addEventListener("click", c.go);
      grid.appendChild(b);
    });
    // Живые превью шаблонов подъезжают асинхронно; без сессии или без
    // сгенерированных превью карточка остаётся на плашках — это норма.
    fetch("/api/mockup/templates", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const withPrev = ((data && data.templates) || [])
          .filter((x) => x.preview_url).slice(0, 3);
        const holder = $("[data-mk-gal]", page);
        if (!holder || !withPrev.length) return;
        holder.innerHTML = withPrev.map((x) =>
          `<img src="${esc(x.preview_url)}" alt="" loading="lazy" />`).join("");
      })
      .catch(() => {});
    app.appendChild(page);
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
    if (window.QlolSchool) window.QlolSchool.close(false);
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

  /* ─────────── Школа: база знаний «как это работает» ───────────
     Одни и те же тексты живут в двух местах: свёрнутым блоком на странице
     раздела (details «Как это работает») и оглавлением в «Школе». */
  const SCHOOL = {
    ru: [
      { id: "clips", icon: "🎬", title: "Клипы", steps: [
        "Создай проект и загрузи трек — сервис разберёт текст и ритм.",
        "Выбери стиль и персонажей: их фото держат лицо во всех кадрах.",
        "Сгенерируй раскадровку — каждая строка песни станет сценой.",
        "Cinema-бар над раскадровкой добавляет свободную сцену одним промптом, @ зовёт персонажа.",
        "Кадры → видео → сборка. «Супергенерация» проходит весь путь одной кнопкой.",
      ] },
      { id: "marketing", icon: "📦", title: "Маркетинг и мокапы", steps: [
        "Создай проект «Мокапы» и загрузи фото товара — этикетка сохранится точь-в-точь.",
        "Выбери готовый шаблон сцены в маркетинг-студии или опиши свою.",
        "Слоты «Персонаж» и «Продукт» подмешивают референсы из любой твоей базы.",
        "Кадр ложится в раскадровку — его можно оживить и собрать в ролик.",
        "3D-облёт крутит товар на 360° по восьми ракурсам.",
      ] },
      { id: "ugc", icon: "🤳", title: "UGC-ролики", steps: [
        "Создай проект UGC и выбери формат ролика — каркас уже расписан по слотам.",
        "Задай персону блогера и локацию — они держатся из ролика в ролик.",
        "Напиши бриф или доверься формату: раскадровка соберётся сама.",
        "Кадры и видео — тем же конвейером, что и клипы.",
      ] },
      { id: "earn", icon: "💸", title: "Заработок", steps: [
        "Сделай ролик с любым продуктом из витрины.",
        "Возьми свою ссылку в разделе «Заработок» и поставь её в пост.",
        "Каждый заказ с твоего трафика приносит долю с продажи.",
      ] },
      { id: "music", icon: "🎵", title: "Музыка", steps: [
        "Опиши трек или загрузи свой — студия соберёт бит и вокал.",
        "Мастеринг выравнивает громкость под площадки.",
        "Готовый трек сразу можно превратить в клип.",
      ] },
      { id: "tokens", icon: "⚡", title: "Токены и цены", steps: [
        "Каждая генерация стоит токены — цена написана прямо на кнопке (⚡).",
        "Цена зависит от движка: шлюзовые дешевле, топовые модели дороже.",
        "Если генерация упала, токены возвращаются автоматически.",
        "Пакеты токенов и тарифы — в кабинете.",
      ] },
    ],
    en: [
      { id: "clips", icon: "🎬", title: "Clips", steps: [
        "Create a project and upload a track — the service parses lyrics and rhythm.",
        "Pick a style and characters: their photos keep the face consistent.",
        "Generate the storyboard — every line becomes a scene.",
        "The cinema bar above the storyboard adds a free scene from one prompt, @ mentions a character.",
        "Frames → video → assembly. \u201cSupergen\u201d runs the whole pipeline in one click.",
      ] },
      { id: "marketing", icon: "📦", title: "Marketing & mockups", steps: [
        "Create a Mockups project and upload product photos — the label is preserved exactly.",
        "Pick a ready-made scene template in the marketing studio or describe your own.",
        "\u201cCharacter\u201d and \u201cProduct\u201d slots mix in references from any of your projects.",
        "The frame lands in the storyboard — animate it and build a video.",
        "The 3D turnaround spins the product through eight angles.",
      ] },
      { id: "ugc", icon: "🤳", title: "UGC videos", steps: [
        "Create a UGC project and pick a format — the slots are pre-planned.",
        "Set the creator persona and location — they persist across videos.",
        "Write a brief or trust the format: the storyboard builds itself.",
        "Frames and video run on the same pipeline as clips.",
      ] },
      { id: "earn", icon: "💸", title: "Earning", steps: [
        "Make a video with any product from the showcase.",
        "Grab your link in the Earn section and put it in your post.",
        "Every order from your traffic pays you a share.",
      ] },
      { id: "music", icon: "🎵", title: "Music", steps: [
        "Describe a track or upload your own — the studio builds beat and vocals.",
        "Mastering levels the loudness for the platforms.",
        "A finished track can become a clip right away.",
      ] },
      { id: "tokens", icon: "⚡", title: "Tokens & prices", steps: [
        "Every generation costs tokens — the price is right on the button (⚡).",
        "The price depends on the engine: gateway ones are cheaper, top models cost more.",
        "If a generation fails, tokens are refunded automatically.",
        "Token packs and plans live in the account.",
      ] },
    ],
  };

  function schoolCats() {
    return SCHOOL[(typeof LANG !== "undefined" && LANG === "ru") ? "ru" : "en"];
  }

  /* Свёрнутый блок «Как это работает» для страницы раздела. */
  window.lolqHowto = function lolqHowto(id) {
    const cat = schoolCats().find((c) => c.id === id);
    if (!cat) return "";
    const head = (typeof LANG !== "undefined" && LANG === "ru")
      ? "Как это работает" : "How it works";
    return `<details class="ms-howto"><summary>${cat.icon} ${esc(head)}</summary>
      <ol>${cat.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details>`;
  };

  function schoolBlock() {
    const lead = (typeof LANG !== "undefined" && LANG === "ru")
      ? "База знаний: как устроен каждый раздел." : "Knowledge base: how every section works.";
    return `<div class="school-grid"><p class="ac-lead">${esc(lead)}</p>` + schoolCats().map((c) =>
      `<details class="ms-howto school-cat"><summary>${c.icon} ${esc(c.title)}</summary>
        <ol>${c.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></details>`).join("") + "</div>";
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
    body.innerHTML = schoolBlock() + courseHead()
      + `<div class="ac-courses">${rows.map(courseCard).join("")}</div>`;
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
        const mkOpen = Boolean($("#marketing-page"));
        relabel();
        academy = null;
        if (window.QlolLibrary) window.QlolLibrary.forget();
        // Страница трендов собрана JS, поэтому data-i18n её не обновит.
        // Перестраиваем её на новом языке без перезагрузки и смены URL.
        if (trendsOpen) openTrends();
        if (mkOpen) openMarketing();
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

  /* Закрыть все страницы-разделы. Нужна школе (и любому будущему разделу
     со своей страницей): убрать чужой узел напрямую значит оставить раздел
     подсвеченным, а его запись — в адресной строке. */
  function closePages() {
    closeTrendsPage(false);
    closeMarketingPage(false);
  }

  /* Обвязка листа отдаётся library.js ЦЕЛИКОМ, а не переписывается там заново.
     Тост, перевод, экранирование, список треков и диалоги применения приёма и
     набора — это ровно те места, где две копии означали бы два поведения: одно
     в академии, другое в каталоге. Отсюда же берётся openSheet, поэтому
     подсветка раздела продолжает работать, кто бы лист ни рисовал. */
  window.QlolSections = {
    mount, go, paint, openAcademy, openLibrary, openMarketing, openEarn,
    closePages, toast, moveIndicator,
    ui: {
      T, TF, esc, lang, num, plural, toast, errorText, busy, failed,
      planName, tracksOf, sceneOptions, slotFields, readSlots,
      applyShot, applyPack,
    },
  };
})();
