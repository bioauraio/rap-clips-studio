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

  /* ═════════════════════════ КАТАЛОГ ПРИЁМОВ («Промты») ═════════════════════
     ТРИ РАЗНЫЕ СУЩНОСТИ, И ПУТАТЬ ИХ НЕЛЬЗЯ:
       стиль  — «как выглядит клип» (закрытый текст, наружу подпись и описание);
       каркас — «что снимаем» (логлайн и биты);
       приём  — «как снята ОДНА сцена» (открытый текст: первый кадр, последний,
                движение).
     Здесь живёт третий слой. Первые два уже есть на витрине /prompts и в
     пикере стилей; дублировать их сюда значило бы завести вторую витрину
     стилей, которая начнёт отставать.

     ЧТО ОТКРЫТО И ПОЧЕМУ. Приёмы камеры, света и монтажа — ремесло из
     учебника, конкурентного преимущества в них ноль, а органики много: их
     текст показываем целиком, с кнопками «скопировать» и «применить».
     Фирменные стили закрыты навсегда — они и есть ров. */

  let library = null;
  let libraryLang = "";
  let libCat = "";
  let libQuery = "";

  async function loadLibrary(force) {
    if (!force && library && libraryLang === lang()) return library;
    libraryLang = lang();
    const [shots, packs] = await Promise.all([
      api(`/api/shots?lang=${encodeURIComponent(lang())}`),
      api(`/api/packs?lang=${encodeURIComponent(lang())}`),
    ]);
    library = { ...shots, packs: packs.packs || [] };
    return library;
  }

  function openLibrary(key) {
    openSheet("prompts", T("library.title", "Промты"), async (body) => {
      busy(body);
      try {
        await loadLibrary(true);
      } catch (e) {
        failed(body, "library.failed");
        return;
      }
      renderLibrary(body);
      if (key) {
        const card = $(`.lb-card[data-key="${key}"]`, body);
        if (card) card.scrollIntoView({ block: "center" });
      }
    });
  }

  function renderLibrary(body) {
    const cats = library.categories || [];
    body.innerHTML = `
      <div class="lb-top">
        <p class="ac-lead">${esc(T("library.lead", ""))}</p>
        <input class="lb-search" type="search" value="${esc(libQuery)}"
               placeholder="${esc(T("library.searchPh", ""))}" />
        <div class="lb-cats" role="tablist">
          <button type="button" class="lb-cat${libCat ? "" : " on"}" data-cat=""
            >${esc(T("library.all", "Все"))}</button>
          ${cats.map((c) => `<button type="button" class="lb-cat${
            libCat === c.key ? " on" : ""}" data-cat="${esc(c.key)}" title="${esc(c.hint)}"
            >${esc(c.label)}</button>`).join("")}
          <button type="button" class="lb-cat${libCat === "packs" ? " on" : ""}" data-cat="packs"
            >${esc(T("library.packs", "Наборы"))}</button>
        </div>
      </div>
      <div class="lb-list"></div>
      <p class="lb-styles muted">${esc(T("library.signatureNote", ""))}</p>`;

    const list = $(".lb-list", body);
    const paintList = () => { list.innerHTML = libraryHtml(); bindList(list); };
    $$(".lb-cat", body).forEach((b) => b.addEventListener("click", () => {
      libCat = b.dataset.cat;
      $$(".lb-cat", body).forEach((x) => x.classList.toggle("on", x === b));
      paintList();
    }));
    const search = $(".lb-search", body);
    search.addEventListener("input", () => { libQuery = search.value; paintList(); });
    paintList();
  }

  function match(s) {
    const q = libQuery.trim().toLowerCase();
    if (!q) return true;
    return [s.label, s.desc, s.gain, s.camera, s.shot, (s.tags || []).join(" ")]
      .join(" ").toLowerCase().includes(q);
  }

  function libraryHtml() {
    if (libCat === "packs") {
      const packs = (library.packs || []).filter(match);
      if (!packs.length) return `<p class="muted">${esc(T("library.empty", ""))}</p>`;
      return packs.map(packCard).join("");
    }
    const shots = (library.shots || [])
      .filter((s) => !libCat || s.category === libCat)
      .filter(match);
    if (!shots.length) return `<p class="muted">${esc(T("library.empty", ""))}</p>`;
    return shots.map(shotCard).join("");
  }

  /* КНОПКА «ПРИМЕНИТЬ» В СПИСКЕ — БЕЗ ОГНЯ, И ЭТО НЕ НЕДОСМОТР. Огонь стоит
     на главном действии ЭКРАНА, а в каталоге пятьдесят шесть карточек: с
     .primary на каждой экран превращается в пятьдесят шесть одинаково
     «главных» кнопок, то есть ни в одну. Огонь остаётся там, где действие
     действительно одно: артефакт урока и кнопка подтверждения в диалоге
     применения. */
  function shotCard(s) {
    const prompts = s.locked ? `
      <p class="lb-locked">${esc(TF("library.locked", { plan: planName(s.tier) }, ""))}</p>`
      : `
      <dl class="lb-prompts">
        <dt>${esc(T("library.first", "Первый кадр"))}</dt><dd>${esc(s.first)}</dd>
        ${s.last ? `<dt>${esc(T("library.last", "Последний кадр"))}</dt><dd>${esc(s.last)}</dd>` : ""}
        <dt>${esc(T("library.motion", "Движение"))}</dt><dd>${esc(s.motion)}</dd>
      </dl>`;
    const slots = (s.slots || []).map((x) => `<code>{${esc(x.key)}}</code>`).join(" ");
    return `<article class="lb-card" data-key="${esc(s.key)}">
      <header>
        <h4>${esc(s.label)}</h4>
        ${s.locked ? `<span class="lb-tier">${esc(planName(s.tier))}</span>` : ""}
      </header>
      <p class="lb-desc">${esc(s.desc)}</p>
      <p class="lb-gain"><b>${esc(T("library.gain", "Что даёт"))}:</b> ${esc(s.gain)}</p>
      ${prompts}
      ${slots ? `<p class="lb-slots">${esc(T("library.slotsTitle", "Слоты"))}: ${slots}</p>` : ""}
      ${s.result ? `<p class="lb-result muted">${esc(s.result)}</p>` : ""}
      <div class="row lb-actions">
        ${s.locked
          ? `<button type="button" class="lb-plans ghost">${esc(T("library.seePlans", ""))}</button>`
          : `<button type="button" class="lb-copy ghost">${esc(T("library.copy", "Скопировать"))}</button>
             <button type="button" class="lb-apply">${esc(T("library.apply", "Применить"))}</button>`}
      </div>
    </article>`;
  }

  function packCard(p) {
    return `<article class="lb-card lb-pack" data-key="${esc(p.key)}">
      <header>
        <h4>${esc(p.label)}</h4>
        ${p.locked ? `<span class="lb-tier">${esc(planName(p.tier))}</span>` : ""}
      </header>
      <p class="lb-desc">${esc(p.desc)}</p>
      <ul class="ac-art-shots">${(p.shots || []).map((s) =>
        `<li>${esc(s.label)}</li>`).join("")}</ul>
      <div class="row lb-actions">
        ${p.locked
          ? `<button type="button" class="lb-plans ghost">${esc(T("library.seePlans", ""))}</button>`
          : `<button type="button" class="lb-apply-pack">${
              esc(T("academy.apply", "Применить в проект"))}</button>`}
      </div>
    </article>`;
  }

  function bindList(list) {
    const find = (node) => {
      const key = node.closest(".lb-card").dataset.key;
      return (library.shots || []).find((s) => s.key === key);
    };
    $$(".lb-copy", list).forEach((b) => b.addEventListener("click", async () => {
      const s = find(b);
      if (!s) return;
      const text = [s.first, s.last, s.motion].filter(Boolean).join("\n\n");
      try {
        await navigator.clipboard.writeText(text);
        b.textContent = T("library.copied", "скопировано");
        setTimeout(() => { b.textContent = T("library.copy", "Скопировать"); }, 1400);
      } catch (e) { toast(T("common.copyManual", "")); }
    }));
    $$(".lb-apply", list).forEach((b) =>
      b.addEventListener("click", () => { const s = find(b); if (s) applyShot(s); }));
    $$(".lb-apply-pack", list).forEach((b) => b.addEventListener("click", () => {
      const key = b.closest(".lb-card").dataset.key;
      const p = (library.packs || []).find((x) => x.key === key);
      if (p) applyPack(p);
    }));
    $$(".lb-plans", list).forEach((b) => b.addEventListener("click", () => {
      if (typeof window.openAccountModal === "function") window.openAccountModal("plan");
    }));
  }

  /* ─────────────────────────────── запуск ─────────────────────────────── */

  function boot() {
    mount();
    if (typeof window.onLangChange === "function") {
      window.onLangChange(() => {
        relabel();
        academy = null;
        library = null;
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

  window.QlolSections = { mount, go, paint, openAcademy, openLibrary };
})();
