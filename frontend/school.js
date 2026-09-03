/* ═══════════════════════════════════════════════════════════════════════════
   lolq.ai — ШКОЛА: КУРСЫ

   Отдельный файл, а не правка sections.js и уж тем более не app.js: школа —
   самостоятельный раздел со своей витриной, страницей курса, страницей урока
   и редактором для владельца. От студии ей нужны четыре глобали (api,
   escHtml, ldMd, openModal) и ничего больше.

   ПОЧЕМУ СТРАНИЦА, А НЕ МОДАЛКА. Курс читают долго: видео, текст, отметка
   «пройдено», переход к следующему уроку. Модалка для такого — это чтение
   в форточку: она закрывается кликом мимо, не даёт ссылки и не имеет адреса.
   Поэтому /school и /school/course/{id} — настоящие адреса, а страница живёт
   внутри #app тем же приёмом, что «Маркетинг» и «Тренды».

   СЛОВАРЬ ЗДЕСЬ, А НЕ В i18n.js. i18n.js — общий файл на 5000 строк, в нём
   параллельно работают другие руки; раздел, которого вчера не было, не должен
   требовать правки общего словаря. Формат тот же: ru + en.

   Публичный API: window.QlolSchool
     open(tab)        — витрина школы («courses» | «base»)
     openCourse(id)   — страница курса
     openLesson(id)   — страница урока
     close()          — убрать страницу
     visible()        — открыта ли школа
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolSchool) return;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (v) => (typeof window.escHtml === "function"
    ? window.escHtml(v)
    : String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c])));
  const api = (p, o) => window.api(p, o);

  /* ─────────────────────────── словарь ─────────────────────────── */
  const D = {
    ru: {
      title: "Школа", lead: "",
      tabCourses: "Курсы", tabBase: "База",
      empty: "Курсов пока нет.",
      emptyAdmin: "Курсов пока нет — создай первый.",
      newCourse: "Создать курс", newModule: "Создать модуль",
      newLesson: "Урок", create: "Создать", students: "Ученики",
      settings: "Настройки", authors: "Авторы", cases: "Кейсы",
      reviews: "Отзывы", actions: "Действия",
      lessons: "уроков", lessonsOne: "урок", lessonsFew: "урока",
      minutes: "мин", free: "бесплатно", paid: "платный",
      draft: "черновик", live: "Запущен", archived: "в архиве",
      done: "Пройдено", markDone: "Пройдено", undo: "Отменить отметку",
      prev: "Предыдущий", next: "Следующий", back: "К курсу",
      backAll: "Все курсы", progress: "пройдено {done} из {total}",
      buy: "Открыть за ⚡{n}", planLock: "Доступ по тарифу {plan}",
      loginLock: "Войди, чтобы открыть курс",
      lockedBody: "Урок закрыт — открой курс, чтобы читать и смотреть.",
      buying: "открываю…", bought: "Курс открыт",
      loading: "загружаю…", failed: "не загрузилось",
      noLessons: "Уроков пока нет.", published: "опубликован",
      unpublished: "черновик", publish: "Опубликовать",
      unpublish: "Снять с публикации", edit: "Править", del: "Удалить",
      save: "Сохранить", cancel: "Отмена", cover: "Обложка",
      video: "Видео", videoUrl: "Ссылка на видео", upload: "Загрузить файл",
      body: "Текст урока (markdown)", summary: "Короткое описание",
      minutesField: "Длительность, мин", nameField: "Название",
      subtitleField: "Подзаголовок", statusField: "Статус",
      accessField: "Доступ", priceField: "Цена, ⚡", planField: "Тариф",
      accessFree: "бесплатный", accessPaid: "платный",
      accessAdmin: "только для нас", planNone: "без тарифа",
      grant: "Выдать доступ", revoke: "Забрать",
      whoPlaceholder: "id, логин или email", noStudents: "Учеников пока нет.",
      addAuthor: "Добавить автора", role: "Роль", bio: "О себе",
      addCase: "Добавить кейс", caseTitle: "Название кейса",
      caseDesc: "Описание", noCases: "Кейсов пока нет.",
      leaveReview: "Оставить отзыв", reviewText: "Что скажешь о курсе?",
      reviewSent: "Спасибо! Отзыв уйдёт на витрину после модерации.",
      noReviews: "Отзывов пока нет.", rating: "рейтинг",
      moderate: "Опубликовать", hide: "Скрыть",
      confirmDel: "Удалить безвозвратно?",
      deleted: "удалено", saved: "сохранено",
      baseLead: "",
      openLesson: "Открыть урок", soon: "скоро",
    },
    en: {
      title: "School", lead: "",
      tabCourses: "Courses", tabBase: "Base",
      empty: "No courses yet.",
      emptyAdmin: "No courses yet — create the first one.",
      newCourse: "Create course", newModule: "Create module",
      newLesson: "Lesson", create: "Create", students: "Students",
      settings: "Settings", authors: "Authors", cases: "Cases",
      reviews: "Reviews", actions: "Actions",
      lessons: "lessons", lessonsOne: "lesson", lessonsFew: "lessons",
      minutes: "min", free: "free", paid: "paid",
      draft: "draft", live: "Live", archived: "archived",
      done: "Done", markDone: "Mark as done", undo: "Undo",
      prev: "Previous", next: "Next", back: "Back to course",
      backAll: "All courses", progress: "{done} of {total} done",
      buy: "Unlock for ⚡{n}", planLock: "Included in the {plan} plan",
      loginLock: "Sign in to unlock the course",
      lockedBody: "The lesson is locked — unlock the course to read and watch.",
      buying: "unlocking…", bought: "Course unlocked",
      loading: "loading…", failed: "failed to load",
      noLessons: "No lessons yet.", published: "published",
      unpublished: "draft", publish: "Publish",
      unpublish: "Unpublish", edit: "Edit", del: "Delete",
      save: "Save", cancel: "Cancel", cover: "Cover",
      video: "Video", videoUrl: "Video link", upload: "Upload a file",
      body: "Lesson text (markdown)", summary: "Short description",
      minutesField: "Duration, min", nameField: "Title",
      subtitleField: "Subtitle", statusField: "Status",
      accessField: "Access", priceField: "Price, ⚡", planField: "Plan",
      accessFree: "free", accessPaid: "paid",
      accessAdmin: "internal only", planNone: "no plan",
      grant: "Grant access", revoke: "Revoke",
      whoPlaceholder: "id, login or email", noStudents: "No students yet.",
      addAuthor: "Add author", role: "Role", bio: "Bio",
      addCase: "Add case", caseTitle: "Case title",
      caseDesc: "Description", noCases: "No cases yet.",
      leaveReview: "Leave a review", reviewText: "How was the course?",
      reviewSent: "Thanks! The review goes live after moderation.",
      noReviews: "No reviews yet.", rating: "rating",
      moderate: "Publish", hide: "Hide",
      confirmDel: "Delete permanently?",
      deleted: "deleted", saved: "saved",
      baseLead: "",
      openLesson: "Open lesson", soon: "soon",
    },
  };

  const lang = () => (typeof LANG === "string" && LANG === "ru" ? "ru" : "en");
  function T(key, vars) {
    let s = (D[lang()] || D.en)[key] || (D.en[key] || "");
    if (vars) {
      Object.keys(vars).forEach((k) => {
        s = s.replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
      });
    }
    return s;
  }
  function lessonWord(n) {
    if (lang() !== "ru") return n === 1 ? T("lessonsOne") : T("lessons");
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return T("lessons");
    if (b === 1) return T("lessonsOne");
    if (b >= 2 && b <= 4) return T("lessonsFew");
    return T("lessons");
  }
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try {
      return d.toLocaleDateString(lang() === "ru" ? "ru-RU" : "en-US",
        { day: "2-digit", month: "short", year: "numeric" });
    } catch (e) { return d.toISOString().slice(0, 10); }
  }
  function toast(text) {
    if (!text) return;
    if (window.QlolSections && window.QlolSections.toast) {
      window.QlolSections.toast(text);
      return;
    }
    if (window.QlolNav && window.QlolNav.toast) { window.QlolNav.toast(text); return; }
    console.log(text);           // eslint-disable-line no-console
  }
  const md = (src) => (typeof window.ldMd === "function"
    ? window.ldMd(src || "")
    : `<p>${esc(src || "")}</p>`);

  /* ─────────────────────────── каркас страницы ─────────────────────────── */

  let state = { tab: "courses", courseId: 0, lessonId: 0, isAdmin: false };

  function page() { return $("#school-page"); }
  function visible() { return Boolean(page()); }

  function close(updateUrl) {
    const p = page();
    if (!p) return;
    p.remove();
    const app = $("#app");
    if (app && !$("#trends-page") && !$("#marketing-page")) {
      app.classList.remove("trends-view");
    }
    if (updateUrl !== false && location.pathname.indexOf("/school") === 0) {
      history.pushState({}, "", "/studio");
    }
    if (window.QlolSections && window.QlolSections.paint) window.QlolSections.paint();
  }

  function ensurePage() {
    let p = page();
    if (p) return p;
    // Другие страницы-разделы закрываем их же руками: у каждой свой узел и
    // своя запись в адресе, и убрать чужой узел напрямую значит оставить
    // раздел в подсвеченном состоянии.
    if (window.QlolSections && window.QlolSections.closePages) {
      window.QlolSections.closePages();
    }
    const app = $("#app");
    if (!app) return null;
    app.classList.add("trends-view");
    p = document.createElement("main");
    p.id = "school-page";
    p.className = "trends-page sch-page";
    app.appendChild(p);
    return p;
  }

  function setUrl(path) {
    if (location.pathname !== path) history.pushState({}, "", path);
  }

  function busy(box) {
    // Стеклянный скелетон вместо строчки «загружаю…»: место будущих карточек
    // мерцает, и загрузка не выглядит пустотой.
    box.innerHTML = `<div class="skel"></div><div class="skel" style="margin-top:10px"></div>`;
  }
  function failed(box) {
    box.innerHTML = `<p class="sch-muted">${esc(T("failed"))}</p>`;
  }

  function form(obj) {
    const fd = new FormData();
    Object.keys(obj).forEach((k) => {
      if (obj[k] !== undefined && obj[k] !== null) fd.append(k, obj[k]);
    });
    return fd;
  }
  const post = (path, data) => api(path, { method: "POST", body: form(data || {}) });

  /* ─────────────────────────── витрина школы ─────────────────────────── */

  async function open(tab) {
    const p = ensurePage();
    if (!p) return;
    state.tab = tab === "base" ? "base" : "courses";
    state.courseId = 0;
    state.lessonId = 0;
    setUrl("/school");
    render(p);
    if (window.QlolSections && window.QlolSections.paint) window.QlolSections.paint();
  }

  /* Одна строка: заголовок + сегмент «Курсы | База». Тот же сегмент стоит
     на странице курса — база знаний доступна оттуда без возврата на витрину. */
  function tabs(cur) {
    return `<div class="sch-tabs" role="tablist">
        <button type="button" class="sch-tab${cur === "courses" ? " on" : ""}"
                data-tab="courses" role="tab">${esc(T("tabCourses"))}</button>
        <button type="button" class="sch-tab${cur === "base" ? " on" : ""}"
                data-tab="base" role="tab">${esc(T("tabBase"))}</button>
      </div>`;
  }
  function hero() {
    return `<section class="trends-hero sch-hero">
      <h1>${esc(T("title"))}</h1>
      ${tabs(state.tab)}
    </section>`;
  }
  function wireTabs(p) {
    $$(".sch-tab", p).forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.tab === "courses" && state.tab === "courses" && !state.courseId) return;
      open(b.dataset.tab);
    }));
  }

  function render(p) {
    p.innerHTML = hero() + `<section class="sch-body"></section>`;
    wireTabs(p);
    const body = $(".sch-body", p);
    if (state.tab === "base") renderBase(body);
    else renderCourses(body);
  }

  /* База знаний — те же уроки-маркдауны, что и раньше: они лежат файлами,
     версионируются коммитами и служат органике. Дублировать их в базу ради
     одной вкладки было бы вторым источником правды. */
  async function renderBase(body) {
    busy(body);
    let data;
    try {
      data = await api(`/api/learn?lang=${encodeURIComponent(lang())}`);
    } catch (e) { failed(body); return; }
    const courses = data.courses || [];
    body.innerHTML = `<div class="sch-base">` + courses.map((c) => `
        <article class="sch-base-card">
          <h3>${esc(c.title)}</h3>
          <ul>${(c.lessons || []).map((l) => `
            <li><button type="button" class="sch-base-lesson" data-slug="${esc(l.slug)}">
              <span>${esc(l.title)}</span>
              <span class="sch-mins">${esc(String(l.minutes || 0))} ${esc(T("minutes"))}</span>
            </button></li>`).join("")}</ul>
        </article>`).join("") + `</div>`;
    $$(".sch-base-lesson", body).forEach((b) => b.addEventListener("click", () => {
      if (window.QlolSections && window.QlolSections.openAcademy) {
        window.QlolSections.openAcademy(b.dataset.slug);
      }
    }));
  }

  function badge(c) {
    if (c.status === "draft") return `<span class="sch-badge draft">${esc(T("draft"))}</span>`;
    if (c.access === "paid") return `<span class="sch-badge paid">${esc(T("paid"))}</span>`;
    return `<span class="sch-badge free">${esc(T("free"))}</span>`;
  }

  /* Курс «скоро» — все уроки заглушки. Флаг c.soon отдаёт сервер
     (_course_card); без него считаем по странице курса (allSoon). */
  function courseSoon(c) {
    if (typeof c.soon === "boolean") return c.soon;
    const mods = c.modules || [];
    const ls = mods.flatMap((m) => m.lessons || []);
    return ls.length > 0 && ls.every((l) => l.soon);
  }
  /* Одна meta-строка: «6 уроков · бесплатно» / «· ⚡120» / «· скоро». */
  function courseMeta(c) {
    const bits = [`${c.lessons} ${lessonWord(c.lessons)}`];
    if (courseSoon(c)) bits.push(T("soon"));
    else if (c.status === "draft") bits.push(T("draft"));
    else if (c.access === "paid") {
      bits.push(c.price_points && !c.unlocked ? `⚡${c.price_points}` : T("paid"));
    } else bits.push(T("free"));
    if (c.rating) bits.push(`★ ${c.rating}`);
    return bits.join(" · ");
  }
  function courseCard(c) {
    const cover = c.cover_url
      ? `<span class="sch-cover" style="background-image:url('${esc(c.cover_url)}')"></span>`
      : `<span class="sch-cover ph"></span>`;
    const pct = Math.max(0, Math.min(100, c.percent || 0));
    return `<button type="button" class="sch-card" data-id="${c.id}">
      <span class="sch-cover-wrap">${cover}${pct
        ? `<span class="sch-bar line"><span style="width:${pct}%"></span></span>` : ""}</span>
      <span class="sch-card-body">
        <span class="sch-card-t">${esc(c.title)}</span>
        <span class="sch-card-m">${esc(courseMeta(c))}</span>
      </span>
    </button>`;
  }

  async function renderCourses(body) {
    busy(body);
    let data;
    try { data = await api("/api/courses"); } catch (e) { failed(body); return; }
    state.isAdmin = Boolean(data.is_admin);
    const rows = data.courses || [];
    if (!rows.length) {
      body.innerHTML = `<div class="sch-empty">
        <span>${esc(T("empty"))}</span>
        ${state.isAdmin
          ? `<button type="button" class="sch-ghost" data-new>${esc(T("newCourse"))}</button>` : ""}
      </div>`;
    } else {
      body.innerHTML = (state.isAdmin
        ? `<div class="sch-admin-row">
             <button type="button" class="sch-fire" data-new>${esc(T("newCourse"))}</button>
           </div>` : "")
        + `<div class="sch-grid">${rows.map(courseCard).join("")}</div>`;
    }
    $$(".sch-card", body).forEach((b) => b.addEventListener("click", () =>
      openCourse(Number(b.dataset.id))));
    const nb = $("[data-new]", body);
    if (nb) {
      nb.addEventListener("click", async () => {
        const r = await post("/api/admin/courses", { title: T("newCourse") });
        openCourse(r.id);
      });
    }
  }

  /* ─────────────────────────── страница курса ─────────────────────────── */

  let course = null;

  async function openCourse(id) {
    const p = ensurePage();
    if (!p) return;
    state.courseId = id;
    state.lessonId = 0;
    setUrl(`/school/course/${id}`);
    busy(p);
    try {
      course = await api(`/api/courses/${id}`);
    } catch (e) { failed(p); return; }
    state.isAdmin = Boolean(course.is_admin);
    renderCourse(p);
    if (window.QlolSections && window.QlolSections.paint) window.QlolSections.paint();
  }

  function statusChip(c) {
    if (courseSoon(c)) return `<span class="sch-chip">${esc(T("soon"))}</span>`;
    if (c.status === "live") return "";
    const map = { draft: T("draft"), archived: T("archived") };
    return `<span class="sch-chip ${esc(c.status)}">${esc(map[c.status] || c.status)}</span>`;
  }

  function lockNote(c) {
    if (c.unlocked) return "";
    if (c.lock === "plan") {
      return `<p class="sch-lock">${esc(T("planLock", { plan: c.min_plan.toUpperCase() }))}</p>`;
    }
    if (c.lock === "login") {
      return `<p class="sch-lock">${esc(T("loginLock"))}</p>`;
    }
    return `<button type="button" class="sch-fire" data-buy>${
      esc(T("buy", { n: c.price_points }))}</button>`;
  }

  function renderCourse(p) {
    const c = course;
    const cover = c.cover_url
      ? `style="background-image:url('${esc(c.cover_url)}')"` : "";
    let n = 0;
    const modules = (c.modules || []).map((m, mi) => {
      const lessons = (m.lessons || []).map((l) => {
        n += 1;
        const num = `${mi + 1}.${(m.lessons.indexOf(l) + 1)}`;
        return `<li class="sch-lrow${l.done ? " done" : ""}" data-lid="${l.id}"
                    ${state.isAdmin ? 'draggable="true"' : ""}>
          <button type="button" class="sch-lopen" data-lid="${l.id}">
            <span class="sch-lnum">${esc(num)}</span>
            <span class="sch-lt">${esc(l.title)}
              ${l.summary ? `<em>${esc(l.summary)}</em>` : ""}</span>
            <span class="sch-lmeta">
              ${l.minutes ? `${esc(String(l.minutes))} ${esc(T("minutes"))}` : ""}
              ${l.soon ? ` · ${esc(lang() === "ru" ? "скоро" : "soon")}` : ""}
              ${l.status !== "published" ? ` · ${esc(T("unpublished"))}` : ""}
            </span>
            <span class="sch-lmark" aria-hidden="true">${l.done ? "✓" : (l.locked ? "🔒" : "›")}</span>
          </button>
          ${state.isAdmin ? `<span class="sch-ledit">
            <button type="button" class="sch-mini" data-ledit="${l.id}">${esc(T("edit"))}</button>
            <button type="button" class="sch-mini danger" data-ldel="${l.id}">${esc(T("del"))}</button>
          </span>` : ""}
        </li>`;
      }).join("");
      return `<section class="sch-mod" data-mid="${m.id}">
        <header>
          <h3><span class="sch-mnum">${mi + 1}</span>${esc(m.title)}</h3>
          ${state.isAdmin ? `<span class="sch-ledit">
            <button type="button" class="sch-mini" data-medit="${m.id}">${esc(T("edit"))}</button>
            <button type="button" class="sch-mini" data-lnew="${m.id}">+ ${esc(T("newLesson"))}</button>
            <button type="button" class="sch-mini danger" data-mdel="${m.id}">${esc(T("del"))}</button>
          </span>` : ""}
        </header>
        <ul class="sch-lessons" data-mid="${m.id}">${lessons
          || `<li class="sch-muted sch-nolessons">${esc(T("noLessons"))}</li>`}</ul>
      </section>`;
    }).join("");

    const authors = (c.authors || []).length ? `<section class="sch-block">
      <h2>${esc(T("authors"))}</h2>
      <div class="sch-authors">${c.authors.map((a) => `
        <article class="sch-author">
          ${a.avatar_url
            ? `<span class="sch-ava big" style="background-image:url('${esc(a.avatar_url)}')"></span>`
            : `<span class="sch-ava big ph">${esc((a.name || "?").slice(0, 1))}</span>`}
          <div><b>${esc(a.name)}</b>
            <span class="sch-muted">${esc(a.role || "")}</span>
            ${a.bio ? `<p>${esc(a.bio)}</p>` : ""}</div>
          ${state.isAdmin
            ? `<button type="button" class="sch-mini danger" data-aunlink="${a.id}">×</button>` : ""}
        </article>`).join("")}</div></section>` : "";

    const cases = (c.cases || []).length ? `<section class="sch-block">
      <h2>${esc(T("cases"))}</h2>
      <div class="sch-cases">${c.cases.map((x) => `
        <article class="sch-case">
          ${x.media_url && /\.(mp4|webm|mov|m4v)$/i.test(x.media_url)
            ? `<video src="${esc(x.media_url)}" controls playsinline preload="metadata"></video>`
            : x.media_url
              ? `<span class="sch-case-img" style="background-image:url('${esc(x.media_url)}')"></span>`
              : ""}
          <b>${esc(x.title)}</b>
          ${x.description ? `<p class="sch-muted">${esc(x.description)}</p>` : ""}
          ${x.video_url ? `<a href="${esc(x.video_url)}" target="_blank" rel="noopener">↗</a>` : ""}
          ${state.isAdmin
            ? `<button type="button" class="sch-mini danger" data-casedel="${x.id}">${esc(T("del"))}</button>` : ""}
        </article>`).join("")}</div></section>` : "";

    const reviews = `<section class="sch-block">
      <h2>${esc(T("reviews"))}${c.rating
        ? ` <span class="sch-rate">★ ${esc(String(c.rating))}</span>` : ""}</h2>
      ${(c.reviews || []).length ? `<div class="sch-reviews">${c.reviews.map((r) => `
        <article class="sch-review${r.published ? "" : " pending"}">
          <b>${esc(r.author_name)}</b>
          <span class="sch-stars">${"★".repeat(Math.max(1, Math.min(5, r.rating)))}</span>
          <p>${esc(r.text)}</p>
          ${state.isAdmin ? `<span class="sch-ledit">
            <button type="button" class="sch-mini" data-rev="${r.id}" data-pub="${r.published ? 0 : 1}">
              ${esc(r.published ? T("hide") : T("moderate"))}</button>
            <button type="button" class="sch-mini danger" data-revdel="${r.id}">${esc(T("del"))}</button>
          </span>` : ""}
        </article>`).join("")}</div>`
        : `<p class="sch-muted">${esc(T("noReviews"))}</p>`}
      ${c.can_review
        ? `<button type="button" class="sch-ghost" data-review>${esc(T("leaveReview"))}</button>` : ""}
    </section>`;

    const admin = state.isAdmin ? `<aside class="sch-side">
      <h2>${esc(T("actions"))}</h2>
      <button type="button" class="sch-fire" data-mnew>${esc(T("newModule"))}</button>
      <button type="button" class="sch-ghost" data-students>${esc(T("students"))}</button>
      <button type="button" class="sch-ghost" data-settings>${esc(T("settings"))}</button>
      <button type="button" class="sch-ghost" data-authors>${esc(T("authors"))}</button>
      <button type="button" class="sch-ghost" data-cases>${esc(T("cases"))}</button>
      <button type="button" class="sch-ghost danger" data-cdel>${esc(T("del"))}</button>
    </aside>` : "";

    p.innerHTML = `
      <nav class="sch-crumbs sch-crumbs-row">
        <button type="button" class="sch-ghost sm" data-all>← ${esc(T("backAll"))}</button>
        ${tabs("courses")}
      </nav>
      <section class="sch-chero" ${cover}>
        <div class="sch-chero-in">
          ${statusChip(c)}
          <h1>${esc(c.title)}</h1>
          ${c.subtitle ? `<p>${esc(c.subtitle)}</p>` : ""}
          <p class="sch-chero-m">${esc(String(c.lessons))} ${esc(lessonWord(c.lessons))}
            · ${esc(T("progress", { done: c.done, total: c.lessons }))}</p>
          <span class="sch-bar"><span style="width:${Math.max(0, Math.min(100, c.percent))}%"></span></span>
          ${lockNote(c)}
        </div>
      </section>
      <div class="sch-cols">
        <div class="sch-main">
          ${modules || `<p class="sch-muted">${esc(T("noLessons"))}</p>`}
          ${authors}${cases}${reviews}
        </div>
        ${admin}
      </div>`;
    wireCourse(p);
    wireTabs(p);
  }

  function wireCourse(p) {
    const c = course;
    const on = (sel, fn) => $$(sel, p).forEach((b) => b.addEventListener("click", fn));
    on("[data-all]", () => open("courses"));
    on(".sch-lopen", (e) => {
      const id = Number(e.currentTarget.dataset.lid);
      const l = (c.modules || []).flatMap((m) => m.lessons || []).find((x) => x.id === id);
      // Урок-заглушка: открывать нечего — честный тост «скоро», не пустая страница.
      if (l && l.soon && !state.isAdmin) { toast(T("soon")); return; }
      openLesson(id);
    });
    const buy = $("[data-buy]", p);
    if (buy) {
      buy.addEventListener("click", async () => {
        buy.disabled = true;
        buy.textContent = T("buying");
        try {
          await post(`/api/courses/${c.id}/buy`);
          toast(T("bought"));
          openCourse(c.id);
        } catch (e) {
          buy.disabled = false;
          buy.textContent = T("buy", { n: c.price_points });
          toast((e && e.message) || T("failed"));
        }
      });
    }
    const rev = $("[data-review]", p);
    if (rev) rev.addEventListener("click", () => reviewModal(c.id));
    if (!state.isAdmin) return;

    on("[data-mnew]", async () => {
      await post(`/api/admin/courses/${c.id}/modules`, { title: T("newModule") });
      openCourse(c.id);
    });
    on("[data-medit]", (e) => {
      const id = Number(e.currentTarget.dataset.medit);
      const m = (c.modules || []).find((x) => x.id === id);
      textModal(T("nameField"), m ? m.title : "", async (v) => {
        await post(`/api/admin/modules/${id}`, { title: v });
        openCourse(c.id);
      });
    });
    on("[data-mdel]", async (e) => {
      if (!window.confirm(T("confirmDel"))) return;
      await post(`/api/admin/modules/${e.currentTarget.dataset.mdel}/delete`);
      openCourse(c.id);
    });
    on("[data-lnew]", async (e) => {
      const r = await post(`/api/admin/modules/${e.currentTarget.dataset.lnew}/lessons`,
        { title: T("newLesson") });
      lessonEditor(r.id);
    });
    on("[data-ledit]", (e) => lessonEditor(Number(e.currentTarget.dataset.ledit)));
    on("[data-ldel]", async (e) => {
      if (!window.confirm(T("confirmDel"))) return;
      await post(`/api/admin/lessons/${e.currentTarget.dataset.ldel}/delete`);
      openCourse(c.id);
    });
    on("[data-cdel]", async () => {
      if (!window.confirm(T("confirmDel"))) return;
      await post(`/api/admin/courses/${c.id}/delete`);
      open("courses");
    });
    on("[data-settings]", () => settingsModal(c));
    on("[data-students]", () => studentsModal(c));
    on("[data-authors]", () => authorsModal(c));
    on("[data-cases]", () => casesModal(c));
    on("[data-aunlink]", async (e) => {
      await post(`/api/admin/courses/${c.id}/authors`,
        { author_id: e.currentTarget.dataset.aunlink, unlink: 1 });
      openCourse(c.id);
    });
    on("[data-casedel]", async (e) => {
      if (!window.confirm(T("confirmDel"))) return;
      await post(`/api/admin/cases/${e.currentTarget.dataset.casedel}/delete`);
      openCourse(c.id);
    });
    on("[data-rev]", async (e) => {
      await post(`/api/admin/reviews/${e.currentTarget.dataset.rev}`,
        { published: e.currentTarget.dataset.pub });
      openCourse(c.id);
    });
    on("[data-revdel]", async (e) => {
      if (!window.confirm(T("confirmDel"))) return;
      await post(`/api/admin/reviews/${e.currentTarget.dataset.revdel}/delete`);
      openCourse(c.id);
    });
    wireDrag(p);
  }

  /* Перетаскивание порядка. Отдаём серверу ИТОГОВЫЙ список id, а не «сдвинь
     на единицу»: браузер знает порядок целиком, а пересчёт соседей на сервере
     разъезжается при двух правках подряд. */
  function wireDrag(p) {
    let dragged = null;
    $$(".sch-lrow", p).forEach((row) => {
      row.addEventListener("dragstart", (e) => {
        dragged = row;
        row.classList.add("dragging");
        try { e.dataTransfer.effectAllowed = "move"; } catch (x) { /* ok */ }
      });
      row.addEventListener("dragend", async () => {
        row.classList.remove("dragging");
        if (!dragged) return;
        const list = dragged.closest(".sch-lessons");
        dragged = null;
        if (!list) return;
        const ids = $$(".sch-lrow", list).map((x) => x.dataset.lid).join(",");
        await post("/api/admin/reorder", { kind: "lesson", ids });
        // Урок мог переехать в другой модуль — сервер об этом узнаёт отдельно.
        const mid = list.dataset.mid;
        for (const el of $$(".sch-lrow", list)) {
          await post(`/api/admin/lessons/${el.dataset.lid}`, { module_id: mid });
        }
        toast(T("saved"));
      });
    });
    $$(".sch-lessons", p).forEach((list) => {
      list.addEventListener("dragover", (e) => {
        e.preventDefault();
        if (!dragged) return;
        const after = $$(".sch-lrow:not(.dragging)", list).find((row) => {
          const box = row.getBoundingClientRect();
          return e.clientY < box.top + box.height / 2;
        });
        const ph = $(".sch-nolessons", list);
        if (ph) ph.remove();
        if (after) list.insertBefore(dragged, after);
        else list.appendChild(dragged);
      });
    });
  }

  /* ─────────────────────────── страница урока ─────────────────────────── */

  async function openLesson(id) {
    const p = ensurePage();
    if (!p) return;
    state.lessonId = id;
    busy(p);
    let l;
    try { l = await api(`/api/lessons/${id}`); } catch (e) { failed(p); return; }
    const locked = l.locked;
    const video = l.video_src
      ? `<video class="sch-video" src="${esc(l.video_src)}" controls playsinline
                preload="metadata"${l.cover_url ? ` poster="${esc(l.cover_url)}"` : ""}></video>`
      : "";
    p.innerHTML = `
      <nav class="sch-crumbs">
        <button type="button" class="sch-ghost sm" data-back>← ${esc(T("back"))}</button>
      </nav>
      <article class="sch-lesson">
        <p class="sch-muted">${esc(l.module_title || "")}</p>
        <h1>${esc(l.title)}</h1>
        <p class="sch-muted">${l.minutes ? esc(String(l.minutes)) + " " + esc(T("minutes")) : ""}
          ${l.published_at ? " · " + esc(fmtDate(l.published_at)) : ""}</p>
        ${video}
        <div class="sch-md">${locked
          ? `<p class="sch-lock">${esc(T("lockedBody"))}</p>`
          : md(l.body_md)}</div>
        ${locked || l.soon ? "" : `<div class="sch-lesson-acts">
          <button type="button" class="${l.done ? "sch-ghost" : "sch-fire"}" data-done>
            ${esc(l.done ? T("undo") : T("markDone"))}</button>
        </div>`}
        <nav class="sch-nav">
          ${l.prev ? `<button type="button" class="sch-ghost" data-go="${l.prev.id}">←
            ${esc(l.prev.title)}</button>` : "<span></span>"}
          ${l.next ? `<button type="button" class="sch-ghost" data-go="${l.next.id}">
            ${esc(l.next.title)} →</button>` : "<span></span>"}
        </nav>
      </article>`;
    $("[data-back]", p).addEventListener("click", () =>
      openCourse((l.course && l.course.id) || state.courseId));
    $$("[data-go]", p).forEach((b) => b.addEventListener("click", () =>
      openLesson(Number(b.dataset.go))));
    const dn = $("[data-done]", p);
    if (dn) {
      dn.addEventListener("click", async () => {
        dn.disabled = true;
        try {
          await post(`/api/lessons/${l.id}/${l.done ? "undone" : "done"}`);
          openLesson(l.id);
        } catch (e) {
          dn.disabled = false;
          toast((e && e.message) || T("failed"));
        }
      });
    }
  }

  /* ─────────────────────────── модалки редактора ─────────────────────────── */

  function modal(title, build) {
    if (typeof window.openModal !== "function") return;
    window.openModal(title, build, { wide: true });
  }

  function field(label, name, value, type) {
    if (type === "textarea") {
      return `<label class="sch-f"><span>${esc(label)}</span>
        <textarea name="${esc(name)}" rows="10">${esc(value || "")}</textarea></label>`;
    }
    return `<label class="sch-f"><span>${esc(label)}</span>
      <input name="${esc(name)}" type="${esc(type || "text")}"
             value="${esc(value == null ? "" : value)}" /></label>`;
  }

  function textModal(title, value, save) {
    modal(title, (body) => {
      body.innerHTML = `<div class="sch-form">${field(title, "v", value)}
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-save>${esc(T("save"))}</button>
        </div></div>`;
      $("[data-save]", body).addEventListener("click", async () => {
        await save($("[name=v]", body).value.trim());
        if (typeof window.closeModal === "function") window.closeModal();
      });
    });
  }

  async function lessonEditor(id) {
    let l;
    try { l = await api(`/api/lessons/${id}`); } catch (e) { toast(T("failed")); return; }
    modal(l.title || T("newLesson"), (body) => {
      body.innerHTML = `<div class="sch-form">
        ${field(T("nameField"), "title", l.title)}
        ${field(T("summary"), "summary", l.summary)}
        ${field(T("minutesField"), "minutes", l.minutes, "number")}
        ${field(T("videoUrl"), "video_url", l.video_src && !l.video_src.startsWith("/api/media/")
          ? l.video_src : "")}
        <label class="sch-f"><span>${esc(T("upload"))} · ${esc(T("video"))}</span>
          <input type="file" accept="video/*" data-vfile /></label>
        <label class="sch-f"><span>${esc(T("cover"))}</span>
          <input type="file" accept="image/*" data-cfile /></label>
        ${field(T("body"), "body_md", l.body_md, "textarea")}
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-save>${esc(T("save"))}</button>
          <button type="button" class="sch-ghost" data-pub>${
            esc(l.status === "published" ? T("unpublish") : T("publish"))}</button>
        </div></div>`;
      const val = (n) => ($(`[name=${n}]`, body) || {}).value || "";
      const saveAll = async (status) => {
        await post(`/api/admin/lessons/${id}`, {
          title: val("title"), summary: val("summary"),
          minutes: val("minutes") || 0, video_url: val("video_url"),
          body_md: val("body_md"), status,
        });
        const vf = $("[data-vfile]", body);
        if (vf && vf.files && vf.files[0]) {
          const fd = new FormData();
          fd.append("video", vf.files[0]);
          await api(`/api/admin/lessons/${id}/video`, { method: "POST", body: fd });
        }
        const cf = $("[data-cfile]", body);
        if (cf && cf.files && cf.files[0]) {
          const fd = new FormData();
          fd.append("cover", cf.files[0]);
          await api(`/api/admin/lessons/${id}/cover`, { method: "POST", body: fd });
        }
        toast(T("saved"));
        if (typeof window.closeModal === "function") window.closeModal();
        openCourse(state.courseId);
      };
      $("[data-save]", body).addEventListener("click", () => saveAll(undefined));
      $("[data-pub]", body).addEventListener("click", () =>
        saveAll(l.status === "published" ? "draft" : "published"));
    });
  }

  function settingsModal(c) {
    modal(T("settings"), (body) => {
      const plans = ["", "free", "pro", "pro_max", "studio"];
      body.innerHTML = `<div class="sch-form">
        ${field(T("nameField"), "title", c.title)}
        ${field(T("subtitleField"), "subtitle", c.subtitle)}
        <label class="sch-f"><span>${esc(T("statusField"))}</span>
          <select name="status">
            ${["draft", "live", "archived"].map((s) =>
              `<option value="${s}"${c.status === s ? " selected" : ""}>${esc(T(
                s === "live" ? "live" : s))}</option>`).join("")}
          </select></label>
        <label class="sch-f"><span>${esc(T("accessField"))}</span>
          <select name="access">
            <option value="free"${c.access === "free" ? " selected" : ""}>${esc(T("accessFree"))}</option>
            <option value="paid"${c.access === "paid" ? " selected" : ""}>${esc(T("accessPaid"))}</option>
            <option value="admin_only"${c.access === "admin_only" ? " selected" : ""}>${
              esc(T("accessAdmin"))}</option>
          </select></label>
        ${field(T("priceField"), "price_points", c.price_points, "number")}
        <label class="sch-f"><span>${esc(T("planField"))}</span>
          <select name="min_plan">${plans.map((pl) =>
            `<option value="${pl}"${c.min_plan === pl ? " selected" : ""}>${
              esc(pl || T("planNone"))}</option>`).join("")}</select></label>
        <label class="sch-f"><span>${esc(T("cover"))}</span>
          <input type="file" accept="image/*" data-cover /></label>
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-save>${esc(T("save"))}</button>
        </div></div>`;
      $("[data-save]", body).addEventListener("click", async () => {
        const v = (n) => $(`[name=${n}]`, body).value;
        await post(`/api/admin/courses/${c.id}`, {
          title: v("title"), subtitle: v("subtitle"), status: v("status"),
          access: v("access"), price_points: v("price_points") || 0,
          min_plan: v("min_plan"),
        });
        const cf = $("[data-cover]", body);
        if (cf && cf.files && cf.files[0]) {
          const fd = new FormData();
          fd.append("cover", cf.files[0]);
          await api(`/api/admin/courses/${c.id}/cover`, { method: "POST", body: fd });
        }
        toast(T("saved"));
        if (typeof window.closeModal === "function") window.closeModal();
        openCourse(c.id);
      });
    });
  }

  function studentsModal(c) {
    modal(T("students"), async (body) => {
      busy(body);
      let data;
      try {
        data = await api(`/api/admin/courses/${c.id}/students`);
      } catch (e) { failed(body); return; }
      const rows = data.students || [];
      body.innerHTML = `<div class="sch-form">
        <div class="sch-grant">
          <input data-who placeholder="${esc(T("whoPlaceholder"))}" />
          <button type="button" class="sch-fire" data-grant>${esc(T("grant"))}</button>
          <button type="button" class="sch-ghost" data-revoke>${esc(T("revoke"))}</button>
        </div>
        ${rows.length ? `<table class="sch-students"><tbody>${rows.map((s) => `
          <tr><td>${esc(s.name)}</td>
              <td class="sch-muted">${esc(s.login || s.email || "")}</td>
              <td>${esc(String(s.done))}/${esc(String(s.total))}</td>
              <td><span class="sch-bar sm"><span style="width:${s.percent}%"></span></span></td>
              <td class="sch-muted">${esc(s.access || "")}</td></tr>`).join("")}
          </tbody></table>`
          : `<p class="sch-muted">${esc(T("noStudents"))}</p>`}
      </div>`;
      const who = () => $("[data-who]", body).value.trim();
      $("[data-grant]", body).addEventListener("click", async () => {
        if (!who()) return;
        await post(`/api/admin/courses/${c.id}/grant`, { who: who() });
        studentsModal(c);
      });
      $("[data-revoke]", body).addEventListener("click", async () => {
        if (!who()) return;
        await post(`/api/admin/courses/${c.id}/revoke`, { who: who() });
        studentsModal(c);
      });
    });
  }

  function authorsModal(c) {
    modal(T("authors"), async (body) => {
      busy(body);
      let data;
      try { data = await api("/api/admin/authors"); } catch (e) { failed(body); return; }
      const linked = new Set((course.authors || []).map((a) => a.id));
      body.innerHTML = `<div class="sch-form">
        <div class="sch-authors edit">${(data.authors || []).map((a) => `
          <article class="sch-author">
            ${a.avatar_url
              ? `<span class="sch-ava" style="background-image:url('${esc(a.avatar_url)}')"></span>`
              : `<span class="sch-ava ph">${esc((a.name || "?").slice(0, 1))}</span>`}
            <div><b>${esc(a.name)}</b><span class="sch-muted">${esc(a.role || "")}</span></div>
            <button type="button" class="sch-mini" data-link="${a.id}">${
              linked.has(a.id) ? "−" : "+"}</button>
            <label class="sch-mini file">📷<input type="file" accept="image/*"
              data-ava="${a.id}" hidden /></label>
            <button type="button" class="sch-mini danger" data-adel="${a.id}">${esc(T("del"))}</button>
          </article>`).join("")}</div>
        <hr />
        ${field(T("nameField"), "name", "")}
        ${field(T("role"), "role", "")}
        ${field(T("bio"), "bio", "", "textarea")}
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-add>${esc(T("addAuthor"))}</button>
        </div></div>`;
      $("[data-add]", body).addEventListener("click", async () => {
        const v = (n) => $(`[name=${n}]`, body).value.trim();
        if (!v("name")) return;
        await post("/api/admin/authors",
          { name: v("name"), role: v("role"), bio: v("bio"), course_id: c.id });
        authorsModal(c);
        openCourse(c.id);
      });
      $$("[data-link]", body).forEach((b) => b.addEventListener("click", async () => {
        await post(`/api/admin/courses/${c.id}/authors`, {
          author_id: b.dataset.link, unlink: linked.has(Number(b.dataset.link)) ? 1 : 0,
        });
        await openCourse(c.id);
        authorsModal(c);
      }));
      $$("[data-adel]", body).forEach((b) => b.addEventListener("click", async () => {
        if (!window.confirm(T("confirmDel"))) return;
        await post(`/api/admin/authors/${b.dataset.adel}/delete`);
        await openCourse(c.id);
        authorsModal(c);
      }));
      $$("[data-ava]", body).forEach((inp) => inp.addEventListener("change", async () => {
        if (!inp.files || !inp.files[0]) return;
        const fd = new FormData();
        fd.append("avatar", inp.files[0]);
        await api(`/api/admin/authors/${inp.dataset.ava}/avatar`,
          { method: "POST", body: fd });
        await openCourse(c.id);
        authorsModal(c);
      }));
    });
  }

  function casesModal(c) {
    modal(T("cases"), (body) => {
      body.innerHTML = `<div class="sch-form">
        ${(course.cases || []).length
          ? `<ul class="sch-caselist">${course.cases.map((x) => `
              <li>${esc(x.title)}
                <label class="sch-mini file">📷<input type="file" data-cmedia="${x.id}"
                  accept="image/*,video/*" hidden /></label>
                <button type="button" class="sch-mini danger" data-cdelx="${x.id}">${
                  esc(T("del"))}</button></li>`).join("")}</ul>`
          : `<p class="sch-muted">${esc(T("noCases"))}</p>`}
        <hr />
        ${field(T("caseTitle"), "title", "")}
        ${field(T("caseDesc"), "description", "", "textarea")}
        ${field(T("videoUrl"), "video_url", "")}
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-add>${esc(T("addCase"))}</button>
        </div></div>`;
      $("[data-add]", body).addEventListener("click", async () => {
        const v = (n) => $(`[name=${n}]`, body).value.trim();
        if (!v("title")) return;
        await post(`/api/admin/courses/${c.id}/cases`, {
          title: v("title"), description: v("description"), video_url: v("video_url"),
        });
        await openCourse(c.id);
        casesModal(c);
      });
      $$("[data-cdelx]", body).forEach((b) => b.addEventListener("click", async () => {
        if (!window.confirm(T("confirmDel"))) return;
        await post(`/api/admin/cases/${b.dataset.cdelx}/delete`);
        await openCourse(c.id);
        casesModal(c);
      }));
      $$("[data-cmedia]", body).forEach((inp) => inp.addEventListener("change", async () => {
        if (!inp.files || !inp.files[0]) return;
        const fd = new FormData();
        fd.append("media", inp.files[0]);
        await api(`/api/admin/cases/${inp.dataset.cmedia}/media`,
          { method: "POST", body: fd });
        await openCourse(c.id);
        casesModal(c);
      }));
    });
  }

  function reviewModal(courseId) {
    modal(T("leaveReview"), (body) => {
      body.innerHTML = `<div class="sch-form">
        <label class="sch-f"><span>${esc(T("rating"))}</span>
          <select name="rating">${[5, 4, 3, 2, 1].map((n) =>
            `<option value="${n}">${"★".repeat(n)}</option>`).join("")}</select></label>
        ${field(T("reviewText"), "text", "", "textarea")}
        <div class="sch-form-acts">
          <button type="button" class="sch-fire" data-send>${esc(T("save"))}</button>
        </div></div>`;
      $("[data-send]", body).addEventListener("click", async () => {
        await post(`/api/courses/${courseId}/review`, {
          rating: $("[name=rating]", body).value,
          text: $("[name=text]", body).value.trim(),
        });
        toast(T("reviewSent"));
        if (typeof window.closeModal === "function") window.closeModal();
        openCourse(courseId);
      });
    });
  }

  /* ─────────────────────────── адрес и запуск ─────────────────────────── */

  function fromPath() {
    const path = location.pathname.replace(/\/+$/, "");
    const m = path.match(/^\/school\/course\/(\d+)$/);
    if (m) { openCourse(Number(m[1])); return true; }
    if (path === "/school") { open("courses"); return true; }
    return false;
  }

  window.addEventListener("popstate", () => {
    if (!fromPath() && visible()) close(false);
  });

  window.QlolSchool = { open, openCourse, openLesson, close, visible, fromPath };
})();
