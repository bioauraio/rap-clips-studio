/* ═══════════════════════════════════════════════════════════════════════════
   qlolvideo — ТУМБЛЕР РЕЖИМОВ И МЕНЮ РЕЖИМА

   Подключается ПОСЛЕ app.js:
       <script src="/modes.js?v=57"></script>   ← реестр
       <script src="/app.js?v=57"></script>
       <script src="/mode-menu.js?v=57"></script>

   ЧЕТЫРЕ ПОВЕРХНОСТИ — ОДНА РЕАЛИЗАЦИЯ:
     • лента сегментов в шапке (#mode-seg) на широком экране;
     • схлопнутая капсула (#project-mode) на узком — она же прежний бейдж
       режима, только теперь это кнопка, а не надпись;
     • шторка «Режимы» на телефоне — она же поповер на десктопе;
     • док верстака (nav.js) зовёт ту же шторку кнопкой «Ещё».

   ГЛАВНОЕ ПРАВИЛО ПЕРЕКЛЮЧЕНИЯ: клик по сегменту делает его активным и
   показывает карточку режима. ПРОЕКТ ПРИ ЭТОМ НЕ МЕНЯЕТСЯ. Проект меняет
   только явная кнопка на карточке — и у неё ровно четыре состояния: открыть
   существующий проект, создать новый, применить ярлык, «скоро» (без кнопки).
   Раньше клик по чужому режиму кидал тост и открывал ящик проектов: человек
   терял и контекст, и объяснение.

   ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО:
     • Фильтра #project-select. Это живой контрол, на нём висит смена проекта
       и с него читается вид открытого проекта. Убрать оттуда option — уронить
       активный выбор. Фильтр по режиму живёт только в списках меню.
     • Гашения рабочей области под чужой режим. Карточка режима показывается
       ПОВЕРХ нетронутого экрана.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolModeMenu) return;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const R = () => window.QlolModes;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function T(key, fallback) {
    const v = typeof window.t === "function" ? window.t(key) : "";
    return v || fallback;
  }

  /* Активный режим = вид ОТКРЫТОГО проекта. Читаем оттуда же, откуда его
     читает nav.js, — из dataset выбранного option: данные проекта для этого
     ждать не нужно, а второй копии списка проектов не появляется. */
  function currentModeId() {
    const sel = $("#project-select");
    const opt = sel && sel.selectedOptions && sel.selectedOptions[0];
    const kind = (opt && opt.dataset.kind) || "";
    const m = kind ? R().ofKind(kind) : null;
    return (m && m.id) || "clip";
  }

  function projectsOf(mode) {
    const sel = $("#project-select");
    if (!sel || !mode || !(mode.projectKinds || []).length) return [];
    return Array.from(sel.options)
      .filter((o) => mode.projectKinds.includes(o.dataset.kind || "album"))
      .map((o) => ({ id: o.value, name: o.textContent, current: o.selected }));
  }

  /* Какой сегмент подсвечен. Это НЕ обязательно режим открытого проекта:
     человек может смотреть карточку мокапов, работая над клипом. */
  let viewed = "";
  let openedAt = 0;          // когда меню открылось — см. сторож клика мимо
  const state = { open: false, anchorSeg: false };

  function activeId() { return viewed || currentModeId(); }

  /* ─────────────────────────── лента сегментов ─────────────────────────── */

  function paintSeg() {
    const box = $("#mode-seg");
    if (!box) return;
    const now = activeId();
    const live = currentModeId();
    box.innerHTML = "";
    R().seg().forEach((m, i) => {
      // Переходы отделены волосяной чертой: они уводят с текущего экрана.
      // Черта ставится там, где МЕНЯЕТСЯ природа соседей, а не «перед каждым
      // внешним»: мастерская теперь стоит первой, и правило «внешний с
      // ненулевым индексом» оставило бы её без границы, а музыке дало вторую.
      const prev = i ? R().seg()[i - 1] : null;
      if (prev && (prev.kind === "external") !== (m.kind === "external")) {
        box.appendChild(el("i", "mode-seg-sep"));
      }
      const b = el("button", "mode-seg-btn"
        + (m.id === now ? " on" : "")
        + (m.id === live ? " is-live" : "")
        + (m.kind === "shortcut" ? " is-shortcut" : ""));
      b.type = "button";
      b.dataset.mode = m.id;
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", m.id === now ? "true" : "false");
      b.title = m.full || m.title;
      b.append(el("span", "mode-seg-ico", m.icon), el("span", "mode-seg-cap", m.title));
      // stopPropagation обязателен: pick() перерисовывает ленту, кнопка
      // отрывается от DOM, и сторож «клик мимо меню» перестаёт узнавать в
      // ней своего — меню закрывалось той же кнопкой, что его открыла.
      b.addEventListener("click", (e) => { e.stopPropagation(); pick(m.id); });
      box.appendChild(b);
    });
  }

  /* Схлопнутая капсула — прежний бейдж режима. id сохранён: app.js ищет его
     голым $("#project-mode"), и пропажа уронила бы renderProjectBar. */
  function paintCapsule() {
    const cap = $("#project-mode");
    if (!cap) return;
    const m = R().byId(activeId()) || R().byId("clip");
    cap.innerHTML = "";
    cap.append(el("span", "mode-ico", m.icon),
               el("span", "mode-cap", m.title),
               el("span", "mode-caret", "▾"));
    cap.title = m.full || m.title;
    cap.setAttribute("aria-expanded", state.open ? "true" : "false");
    cap.classList.remove("hidden");
  }

  function pick(id) {
    const m = R().byId(id);
    if (!m) return;
    // Переход (чат) — это не «посмотреть режим», а уйти на другой экран.
    if (R().isExternal(m)) { close(); m.external(); return; }
    viewed = id;
    paintSeg();
    paintCapsule();
    open(id);
  }

  /* ───────────────────────── карточка режима ───────────────────────── */

  function routeLine(m) {
    const row = el("div", "mode-card-route");
    (m.steps || []).forEach((s, i) => {
      if (i) row.appendChild(el("i", "mc-route-sep", "—"));
      const chip = el("span", "mc-route-step");
      chip.append(el("b", null, s.icon), document.createTextNode(" " + s.title));
      row.appendChild(chip);
    });
    return row;
  }

  function factRow(label, value) {
    const r = el("div", "mode-card-fact");
    r.append(el("span", "mc-fact-k", label), el("span", "mc-fact-v", value));
    return r;
  }

  /* Единственная кнопка карточки. Четыре состояния, ни одно не притворяется
     другим: «скоро» не получает кнопки вовсе, ярлык честно говорит, что он
     стиль, режим без проектов честно говорит, что проектов нет. */
  function actionFor(m) {
    if (m.soon) {
      const note = el("p", "mode-card-soon", T("modes.menu.soonNote", "Режим готовится."));
      return note;
    }
    if (R().isShortcut(m)) {
      const b = el("button", "primary mode-card-go",
        T("modes.menu.applyShortcut", "Снимать в этом стиле"));
      b.type = "button";
      b.addEventListener("click", () => {
        close();
        if (typeof window.applyModeShortcut === "function") window.applyModeShortcut(m.id);
      });
      return b;
    }
    if (R().isExternal(m)) {
      // Подпись берём из записи режима: переходов теперь два, и «Открыть
      // чат» на карточке музыки было бы прямым враньём.
      const b = el("button", "primary mode-card-go",
        T(m.goKey || "modes.menu.openChat", "Открыть"));
      b.type = "button";
      b.addEventListener("click", () => { close(); m.external(); });
      return b;
    }
    const list = projectsOf(m);
    if (!list.length) {
      const b = el("button", "primary mode-card-go",
        T("modes.menu.create", "Создать проект") + ": " + m.title);
      b.type = "button";
      b.addEventListener("click", () => {
        close();
        if (typeof window.openNewProjectModal === "function") {
          window.openNewProjectModal(m.defaultKind || (m.projectKinds || [])[0] || "album");
        } else {
          const nb = $("#new-project-btn");
          if (nb) nb.click();
        }
      });
      return b;
    }
    const target = list.find((p) => p.current) || list[list.length - 1];
    const b = el("button", "primary mode-card-go",
      T("modes.menu.open", "Открыть") + " «" + target.name + "»");
    b.type = "button";
    b.addEventListener("click", () => {
      close();
      const sel = $("#project-select");
      if (!sel) return;
      if (String(sel.value) === String(target.id)) return;   // уже открыт
      sel.value = target.id;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
    return b;
  }

  function card(m) {
    const box = el("div", "mode-card");
    const head = el("div", "mode-card-head");
    head.append(el("span", "mode-card-ico", m.icon));
    const ttl = el("div", "mode-card-ttl");
    ttl.append(el("b", null, m.title));
    const list = projectsOf(m);
    const badge = m.soon ? T("modes.menu.soon", "скоро")
      : R().isShortcut(m) ? T("modes.menu.shortcut", "стиль")
      : R().isExternal(m) ? T("modes.menu.screen", "экран")
      : list.length ? T("modes.menu.projects", "проектов: {n}").replace("{n}", list.length)
      : T("modes.menu.noProjects", "проектов нет");
    ttl.append(el("span", "mode-card-badge", badge));
    head.appendChild(ttl);
    box.appendChild(head);

    if (m.note) box.appendChild(el("p", "mode-card-note", m.note));

    if (R().isShortcut(m)) {
      const to = R().byId((m.target || {}).mode) || R().byId("clip");
      box.appendChild(factRow(T("modes.menu.factWhere", "где снимается"), to.title));
    }
    if (m.object) {
      box.appendChild(factRow(T("modes.menu.factObject", "объект"),
        T("modes.object." + m.object + ".many", m.object)));
    }
    if (m.aspect) box.appendChild(factRow(T("modes.menu.factAspect", "кадр"), m.aspect));
    if ((m.steps || []).length) {
      box.appendChild(el("div", "mode-card-h", T("modes.menu.route", "маршрут")));
      box.appendChild(routeLine(m));
    }
    box.appendChild(actionFor(m));
    return box;
  }

  /* ────────────────────────── слой: шторка/поповер ────────────────────────── */

  let layer = null, scrim = null;

  function build() {
    if (layer) return;
    scrim = el("div", "mode-scrim");
    scrim.hidden = true;
    scrim.addEventListener("click", close);

    layer = el("div", "mode-sheet");
    layer.hidden = true;
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "false");
    layer.setAttribute("aria-label", T("modes.menu.title", "Режимы"));
    document.body.append(scrim, layer);

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && state.open) { e.stopPropagation(); close(); }
    });
    document.addEventListener("click", (e) => {
      if (!state.open || !state.anchorSeg) return;
      // Клик, КОТОРЫМ меню и открыли, до сторожа доходит уже после того, как
      // open() перерисовал ленту: нажатая кнопка к этому моменту оторвана от
      // DOM, contains() её не узнаёт, и меню закрывалось само собой. То же
      // самое приезжает из верстака, где кнопка вообще в другом контейнере.
      // Проверка по времени закрывает оба случая разом.
      if (Date.now() - openedAt < 320) return;
      const cap = $("#project-mode");
      const box = $("#mode-seg");
      if (layer.contains(e.target)) return;
      if (cap && cap.contains(e.target)) return;
      if (box && box.contains(e.target)) return;
      close();
    });
    window.addEventListener("resize", () => { if (state.open) place(); });
  }

  function place() {
    // На телефоне — нижняя шторка (позиционирует CSS), на десктопе поповер
    // под тем контролом, из которого его открыли.
    if (window.innerWidth <= 700) {
      layer.style.top = layer.style.left = "";
      layer.classList.add("is-sheet");
      return;
    }
    layer.classList.remove("is-sheet");
    const anchor = $("#mode-seg") && $("#mode-seg").offsetParent ? $("#mode-seg") : $("#project-mode");
    const r = anchor ? anchor.getBoundingClientRect() : { bottom: 60, left: 20, right: 400 };
    const w = 380;
    layer.style.top = Math.round(r.bottom + 8) + "px";
    layer.style.left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - w - 8))) + "px";
  }

  function open(id) {
    build();
    if (id) viewed = id;
    state.open = true;
    state.anchorSeg = true;
    openedAt = Date.now();
    layer.innerHTML = "";

    const head = el("div", "mode-sheet-head");
    head.append(el("b", null, T("modes.menu.title", "Режимы")));
    const x = el("button", "mode-sheet-x", "✕");
    x.type = "button";
    x.setAttribute("aria-label", T("common.close", "Закрыть"));
    x.addEventListener("click", close);
    head.appendChild(x);
    layer.appendChild(head);

    // Та же лента сегментов, что и в шапке: на телефоне она единственная.
    const rail = el("div", "mode-sheet-rail");
    R().seg().forEach((m) => {
      const b = el("button", "mode-seg-btn" + (m.id === activeId() ? " on" : ""));
      b.type = "button";
      b.append(el("span", "mode-seg-ico", m.icon), el("span", "mode-seg-cap", m.title));
      b.addEventListener("click", (e) => {
        e.stopPropagation();                     // см. комментарий в paintSeg
        if (R().isExternal(m)) { close(); m.external(); return; }
        viewed = m.id;
        paintSeg(); paintCapsule(); open(m.id);
      });
      rail.appendChild(b);
    });
    layer.appendChild(rail);

    const m = R().byId(activeId()) || R().byId("clip");
    layer.appendChild(card(m));

    // Подвал «Скоро»: мёртвые режимы видно, но они не отъедают место у живых.
    const later = R().soon();
    if (later.length) {
      const foot = el("div", "mode-sheet-soon");
      foot.appendChild(el("div", "mode-card-h", T("modes.menu.soon", "скоро")));
      later.forEach((s) => {
        const row = el("div", "mode-soon-row");
        row.append(el("span", null, s.icon + " " + s.title),
                   el("span", "muted", s.full || ""));
        foot.appendChild(row);
      });
      layer.appendChild(foot);
    }

    layer.hidden = false;
    scrim.hidden = window.innerWidth > 700;   // на десктопе поповер без затемнения
    place();
    paintCapsule();
  }

  function close() {
    if (!layer) return;
    state.open = false;
    layer.hidden = true;
    scrim.hidden = true;
    // Возвращаем подсветку на режим ОТКРЫТОГО проекта: смотреть чужую
    // карточку можно, «быть» в чужом режиме — нет.
    viewed = "";
    paintSeg();
    paintCapsule();
  }

  /* ─────────────────────────────── запуск ─────────────────────────────── */

  function sync() {
    if (!R()) return;
    viewed = "";
    paintSeg();
    paintCapsule();
  }

  function mount() {
    if (!R()) return;
    const cap = $("#project-mode");
    if (cap && !cap.dataset.wired) {
      cap.dataset.wired = "1";
      cap.addEventListener("click", (e) => {
        e.stopPropagation();
        state.open ? close() : open(currentModeId());
      });
    }
    sync();
  }

  window.QlolModeMenu = { mount, sync, open, close, pick, currentModeId };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
