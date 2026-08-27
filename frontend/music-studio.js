/* ═══════════════════════════════════════════════════════════════════════════
   lolq.ai — раздел «Музыка» (экран #music)

   Отдельный файл, а не кусок app.js: app.js уже 7900 строк и держит студию
   клипов. Здесь другой объект (релиз, а не клип), другой конвейер и другой
   набор состояний — смешивать их в одном файле значит гарантировать, что
   правка в одном сломает другое.

   Зависит от глобалей app.js/i18n.js (`api`, `t`, `fail`, `escHtml`,
   `hideScreens`, `showApp`, `onLangChange`) — они объявлены обычными
   функциями верхнего уровня, то есть доступны отсюда как есть.

   ЧТО ЗДЕСЬ ГЛАВНОЕ, ПОМИМО ФУНКЦИЙ:
     * окно загрузки живёт по §4.12 дизайн-системы — вход и статус, доля
       загрузки известна и показана, отказ объясняет причину;
     * замеры (LUFS, пик, динамика) и волна появляются СРАЗУ после приёма
       файла, до всякой оплаты;
     * сравнение «до/после» идёт на полном треке и С ВЫРАВНИВАНИЕМ ГРОМКОСТИ:
       без него любой мастер звучит «лучше», и это враньё;
     * выключенное показано выключенным и подписано причиной (§7);
     * дистрибуции у нас нет, и кнопка называется «отправить заявку», а не
       «опубликовать».
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const M = {
    booted: false,
    status: null,       // GET /api/music/status
    refs: null,         // полка эталонов
    social: null,       // живость службы публикации
    items: [],
    cursor: 0,
    total: 0,
    listBusy: false,
    open: null,         // раскрытый трек целиком
    tab: "sound",
    poll: null,
    // Выбор в панели мастеринга — живёт до перезагрузки, чтобы человек не
    // выставлял профиль заново для каждого трека.
    target: "streaming",
    engine: "auto",
    refKind: "none",
    refId: "",
    refFile: null,
    ab: "after",
    match: true,
    genBusy: false,
    // Раздел верхнего уровня: Анализ / Мастеринг / Дорожки / Генерация /
    // Дистрибуция. Первые три работают с треком из списка, последние два —
    // самостоятельные страницы.
    section: "analysis",
    stemsTask: null,
    stemsRes: null,
    stemsBusy: false,
    keyBusy: false,
    analysis: null,     // {id, data} — разбор открытого трека
  };

  const SECTIONS = ["analysis", "master", "stems", "gen", "dist"];

  function defaultTab() {
    return M.section === "master" ? "master"
      : M.section === "stems" ? "stems" : "sound";
  }

  const byId = (id) => document.getElementById(id);
  /* Сводка раздела может не доехать (сеть моргнула на первом кадре). Все
     обращения к ней идут через эту функцию: экран без цен и лимитов лучше,
     чем экран, упавший на чтении поля у null. */
  const S = (key) => ((M.status && M.status[key]) || {});
  const T = (key, vars) => (typeof t === "function" ? t("mus." + key, vars) : key);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  /* Единица списания в нужной форме: «1 токен», «2 токена», «6 токенов».
     Шаблон в словаре — «{n} {unit}»: язык, которому нужен другой порядок,
     переставит его у себя, а не в коде. */
  function unit(n) {
    return typeof tPlural === "function" ? tPlural(n, tRaw("mus.unit")) : "";
  }

  function apiErr(e) {
    return typeof errText === "function" ? errText(e) : String((e && e.message) || e || "");
  }

  // ────────────────────────── вход и выход ──────────────────────────

  function show() {
    if (typeof hideScreens === "function") hideScreens();
    const box = byId("music");
    if (!box) return;
    box.classList.remove("hidden");
    if (location.hash !== "#/music") history.replaceState(null, "", "#/music");
    applySection();
    boot();
  }

  function leave() {
    clearTimeout(M.poll);
    stopAB();
    if (location.hash === "#/music") history.replaceState(null, "", location.pathname);
    if (typeof showApp === "function") showApp();
  }

  function visible() {
    const box = byId("music");
    return box && !box.classList.contains("hidden");
  }

  async function boot() {
    if (!M.booted) {
      M.booted = true;
      try {
        M.status = await api("/api/music/status");
      } catch (e) {
        M.booted = false;
        fail(e);
        return;
      }
      try { M.refs = await api("/api/music/references"); } catch (e) { M.refs = { items: [] }; }
    }
    renderIntake();
    applySection();
    if (!M.items.length) await loadList(true);
    renderAll();
  }

  // ─────────────────── разделы верхнего уровня ───────────────────

  function sectionNav() {
    let bar = byId("mus-sections");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "mus-sections";
      bar.className = "mus-tabs mus-sections";
      const wrap = document.querySelector("#music .mus-wrap");
      if (wrap) wrap.insertBefore(bar, wrap.firstChild);
    }
    bar.innerHTML = "";
    SECTIONS.forEach((id) => {
      const b = el("button", "mus-tab" + (M.section === id ? " on" : ""), T("sec." + id));
      b.type = "button";
      b.addEventListener("click", () => {
        if (M.section === id) return;
        M.section = id;
        if (M.open) M.tab = defaultTab();
        stopAB();
        applySection();
      });
      bar.appendChild(b);
    });
  }

  /* Единственное место, которое решает, что на экране: раздел + открыт ли
     трек. openTrack/closeTrack больше не трогают hidden сами. */
  function applySection() {
    sectionNav();
    const intake = byId("mus-intake");
    const drop = byId("mus-drop");
    const gen = byId("mus-gen");
    const list = byId("mus-list-card");
    const det = byId("mus-detail");
    let dist = byId("mus-dist");
    if (!dist) {
      dist = document.createElement("div");
      dist.id = "mus-dist";
      dist.className = "hidden";
      const wrap = document.querySelector("#music .mus-wrap");
      if (wrap) wrap.appendChild(dist);
    }
    const sct = M.section;
    const showDist = sct === "dist";
    const open = Boolean(M.open) && !showDist;
    dist.classList.toggle("hidden", !showDist);
    if (intake) intake.classList.toggle("hidden",
      showDist || open || sct === "master" || sct === "stems");
    if (drop) drop.classList.toggle("hidden", sct === "gen");
    if (gen) gen.classList.toggle("hidden", sct !== "gen" && sct !== "analysis");
    if (list) list.classList.toggle("hidden", showDist || open);
    if (det) det.classList.toggle("hidden", !open);
    if (open) renderDetail();
    if (showDist) renderDist(dist);
  }

  // ────────────────────────── лента треков ──────────────────────────

  async function loadList(reset) {
    if (M.listBusy) return;
    M.listBusy = true;
    if (reset) { M.items = []; M.cursor = 0; }
    renderList();
    try {
      const q = M.cursor ? `?limit=20&before=${M.cursor}` : "?limit=20";
      const data = await api("/api/music/tracks" + q);
      M.items = M.items.concat(data.items || []);
      M.cursor = data.next_cursor || 0;
      M.total = data.total || M.items.length;
    } catch (e) {
      fail(e);
    } finally {
      M.listBusy = false;
      renderList();
      renderTop();
    }
  }

  function renderTop() {
    const c = byId("mus-count");
    if (c) {
      // «1 трек», «2 трека», «5 треков» — форма слова из словаря.
      c.textContent = M.total
        ? M.total + " " + tPlural(M.total, tRaw("mus.nav.count"))
        : "";
    }
    const p = byId("mus-points");
    const pts = (typeof me === "object" && me && me.user) ? me.user.gen_points : null;
    if (p) {
      const admin = M.status && M.status.is_admin;
      // Формат ровно как в шапке студии (app.js renderUserBar): «1 000
      // токенов», а монетку марки рисует сама .points-badge фоном. Своя
      // молния рядом с монеткой смотрелась бы вторым значком валюты.
      p.textContent = (pts === null || admin)
        ? "" : `${tNum(pts)} ${t("top.pointsUnit")}`;
      p.classList.toggle("hidden", pts === null || Boolean(admin));
    }
  }

  function itemTags(it) {
    const out = [];
    if (it.master_status === "done" && it.master_filename) {
      out.push(["ok", T("item.mastered")]);
    } else if (it.master_status === "running" || it.master_status === "queued") {
      out.push(["warn", T("master.running")]);
    } else if (it.master_status === "error") {
      out.push(["bad", T("item.masterFailed")]);
    } else {
      out.push(["warn", T("item.noMaster")]);
    }
    if (it.release_status === "submitted") out.push(["ok", T("item.submitted")]);
    if (it.origin === "generated") out.push(["", T("item.generated")]);
    return out;
  }

  function fmtDur(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function fmtMB(bytes) {
    const mb = (Number(bytes) || 0) / 1048576;
    return mb >= 10 ? `${Math.round(mb)} MB` : `${mb.toFixed(1)} MB`;
  }

  function renderList() {
    const box = byId("mus-list");
    if (!box) return;
    box.innerHTML = "";
    if (M.listBusy && !M.items.length) {
      for (let i = 0; i < 3; i += 1) box.appendChild(el("div", "mus-skel"));
      return;
    }
    if (!M.items.length) {
      const empty = el("div", "mus-empty");
      empty.appendChild(el("div", null, T("list.empty")));
      empty.appendChild(el("div", null, T("list.emptyNote")));
      box.appendChild(empty);
      return;
    }
    M.items.forEach((it) => {
      const row = el("button", "mus-item");
      row.type = "button";
      const cover = el("div", "mus-item-cover");
      if (it.cover_url) {
        const img = el("img");
        img.src = it.cover_url;
        img.alt = "";
        img.loading = "lazy";
        cover.appendChild(img);
      } else {
        cover.textContent = "♪";
      }
      const mid = el("div");
      mid.appendChild(el("div", "mus-item-name",
        it.title || it.source_name || T("item.untitled")));
      const bits = [it.artist || "", fmtDur(it.duration_sec),
                    (it.source_ext || "").toUpperCase()];
      if (it.lufs) bits.push(`${it.lufs} LUFS`);
      if (it.bpm) bits.push(`${it.bpm} BPM`);
      mid.appendChild(el("div", "mus-item-meta", bits.filter(Boolean).join(" · ")));
      const tags = el("div", "mus-item-tags");
      itemTags(it).forEach(([cls, text]) => tags.appendChild(el("span", "mus-tag " + cls, text)));
      row.append(cover, mid, tags);
      row.addEventListener("click", () => openTrack(it.id));
      box.appendChild(row);
    });
    if (M.cursor) {
      const more = el("button", "ghost", T("list.loadMore"));
      more.type = "button";
      more.style.justifySelf = "center";
      more.addEventListener("click", () => loadList(false));
      box.appendChild(more);
      // Бесконечная прокрутка вместо страниц: кнопка остаётся запасным
      // путём для тех, у кого IntersectionObserver выключен.
      if (window.IntersectionObserver) {
        const io = new IntersectionObserver((entries) => {
          if (entries.some((x) => x.isIntersecting) && !M.listBusy) {
            io.disconnect();
            loadList(false);
          }
        }, { rootMargin: "200px" });
        io.observe(more);
      }
    }
  }

  // ────────────────────────── окно загрузки ──────────────────────────

  function renderIntake() {
    const drop = byId("mus-drop");
    if (!drop || !M.status) return;
    const u = S("upload");
    const hint = byId("mus-drop-hint");
    if (hint) {
      hint.textContent = T("drop.hint", {
        formats: (u.formats || []).slice(0, 6).join(", "),
        mb: Math.round(u.max_mb || 0),
        min: Math.round((u.max_sec || 0) / 60),
      });
    }
    renderGen();
  }

  function dropState(state, text, pct) {
    const drop = byId("mus-drop");
    if (!drop) return;
    drop.classList.toggle("busy", state === "busy");
    const err = byId("mus-drop-err");
    const prog = byId("mus-drop-prog");
    const bar = prog && prog.querySelector("i");
    const title = byId("mus-drop-title");
    if (err) {
      err.textContent = state === "error" ? text || "" : "";
      err.classList.toggle("hidden", state !== "error");
    }
    if (prog) prog.classList.toggle("hidden", state !== "busy");
    if (bar) bar.style.width = Math.round((pct || 0) * 100) + "%";
    if (title) {
      title.innerHTML = state === "busy"
        ? escHtml(T("drop.sending", { p: Math.round((pct || 0) * 100) }))
        : T("drop.titleHtml");
    }
  }

  /* Проверки ДО отправки: гнать по сети 200 МБ, чтобы узнать про формат,
     — это издевательство, а не валидация. Серверные проверки при этом
     остаются: клиент никогда не последняя линия. */
  function preCheck(file) {
    const u = S("upload");
    const name = (file.name || "").toLowerCase();
    const ext = name.slice(name.lastIndexOf(".") + 1);
    const formats = u.formats || [];
    if (formats.length && formats.indexOf(ext) < 0) {
      return T("drop.errFormat", { ext: ext || "?", formats: formats.join(", ") });
    }
    if (u.max_mb && file.size > u.max_mb * 1048576) {
      return T("drop.errBig", { mb: Math.round(u.max_mb), got: fmtMB(file.size) });
    }
    return "";
  }

  function xhrUpload(path, formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", path);
      const auth = (window.TGA && TGA.active) ? TGA.authHeader() : null;
      if (auth) Object.keys(auth).forEach((k) => xhr.setRequestHeader(k, auth[k]));
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText || "{}"); } catch (e) { body = {}; }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(body); return; }
        const err = new Error(body.detail || body.message || body.error || T("err.upload"));
        // Тело ошибки нужно вызывающему: дистрибуция показывает СПИСОК
        // проверок, а не одну строку.
        err.data = body;
        err.status = xhr.status;
        reject(err);
      };
      xhr.onerror = () => reject(new Error(T("err.net")));
      xhr.send(formData);
    });
  }

  async function takeFile(file) {
    if (!file) return;
    const bad = preCheck(file);
    if (bad) { dropState("error", bad); return; }
    dropState("busy", "", 0);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const data = await xhrUpload("/api/music/tracks", fd, (p) => dropState("busy", "", p));
      dropState("idle");
      M.items.unshift(data);
      M.total += 1;
      renderList();
      renderTop();
      openTrack(data.id, data);
    } catch (e) {
      dropState("error", apiErr(e));
    }
  }

  // ────────────────────────── генерация ──────────────────────────

  function renderGen() {
    const card = byId("mus-gen");
    if (!card || !M.status) return;
    const g = S("generation");
    // Ключа генерации нет — секции НЕТ: кнопка с ценой над строкой «нет
    // ключа ElevenLabs» — это обманка, а внутренняя кухня не для витрины.
    card.classList.toggle("hidden", !g.enabled);
    card.classList.toggle("is-off", !g.enabled);
    if (!g.enabled) return;
    const off = byId("mus-gen-off");
    if (off) off.classList.add("hidden");
    const btn = byId("mus-gen-run");
    if (btn) {
      btn.disabled = !g.enabled || M.genBusy;
      btn.textContent = M.genBusy ? T("gen.running") : T("gen.run");
    }
    const sel = byId("mus-gen-secs");
    if (sel && !sel.options.length) {
      [30, 60, 90, 120, 180].forEach((s) => {
        const o = document.createElement("option");
        o.value = String(s);
        o.textContent = fmtDur(s);
        sel.appendChild(o);
      });
      sel.value = "60";
    }
    renderGenPrice();
  }

  function renderGenPrice() {
    const price = byId("mus-gen-price");
    const sel = byId("mus-gen-secs");
    if (!price || !M.status) return;
    const per30 = S("cost").music_per_30_sec || 0;
    const secs = Number(sel && sel.value) || 60;
    const n = Math.max(1, Math.ceil(secs / 30)) * per30;
    price.textContent = T("gen.cost", { n, unit: unit(n) });
  }

  async function runGen() {
    const ta = byId("mus-gen-prompt");
    const sel = byId("mus-gen-secs");
    const instr = byId("mus-gen-instr");
    const prompt = (ta && ta.value || "").trim();
    if (!prompt) { ta && ta.focus(); return; }
    M.genBusy = true;
    renderGen();
    try {
      const fd = new FormData();
      fd.append("prompt", prompt);
      fd.append("seconds", String(Number(sel && sel.value) || 60));
      fd.append("instrumental", instr && instr.checked ? "true" : "false");
      const data = await api("/api/music/generate", { method: "POST", body: fd });
      M.items.unshift(data);
      M.total += 1;
      if (ta) ta.value = "";
      renderList();
      me = await api("/api/me").catch(() => me);
      renderTop();
      openTrack(data.id, data);
    } catch (e) {
      fail(e);
    } finally {
      M.genBusy = false;
      renderGen();
    }
  }

  // ────────────────────────── карточка трека ──────────────────────────

  async function openTrack(id, preload) {
    const det = byId("mus-detail");
    if (!det) return;
    if (M.section === "dist" || M.section === "gen") M.section = "analysis";
    M.open = preload || null;
    M.tab = defaultTab();
    M.stemsRes = null;
    M.stemsBusy = Boolean(M.stemsTask);
    M.analysis = null;
    applySection();
    try {
      M.open = await api("/api/music/tracks/" + id);
    } catch (e) {
      fail(e);
      closeTrack();
      return;
    }
    M.target = M.open.master_target || M.target;
    applySection();
    schedulePoll();
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function closeTrack() {
    clearTimeout(M.poll);
    stopAB();
    M.open = null;
    applySection();
    loadList(true);
  }

  function busyNow(o) {
    return Boolean(o) && [o.probe_status, o.master_status, o.video_status, o.social_status]
      .some((s) => s === "queued" || s === "running");
  }

  /* Слепок изменчивого состояния. Перерисовывать карточку на каждый тик
     опроса нельзя: пока идёт мастеринг, человек заполняет метаданные, а
     перерисовка пересобирает поля — и набранное исчезает под руками. */
  function pulse(o) {
    return [o.probe_status, o.master_status, o.video_status, o.social_status,
            o.master_filename, o.video_filename, o.social_url,
            o.lufs, o.bpm, (o.wave || []).length].join("|");
  }

  function schedulePoll() {
    clearTimeout(M.poll);
    if (!M.open || !busyNow(M.open) || !visible()) return;
    M.poll = setTimeout(async () => {
      try {
        const fresh = await api("/api/music/tracks/" + M.open.id);
        const wasMaster = M.open.master_status;
        const changed = pulse(fresh) !== pulse(M.open);
        M.open = fresh;
        if (changed) renderDetail();
        if (wasMaster !== fresh.master_status && fresh.master_status === "done") {
          me = await api("/api/me").catch(() => me);
          renderTop();
        }
        schedulePoll();
      } catch (e) { /* сеть моргнула — следующий тик разберётся */ }
    }, 3000);
  }

  /* Фокус и набранное переживают перерисовку. Поля помечены data-mus-field —
     без метки восстанавливать было бы нечего: после render это уже другие
     узлы DOM. */
  function captureFocus() {
    const a = document.activeElement;
    if (!a || !a.closest || !a.closest("#mus-detail")) return null;
    const key = a.getAttribute("data-mus-field");
    if (!key) return null;
    return { key, value: a.value, start: a.selectionStart, end: a.selectionEnd };
  }

  function restoreFocus(snap) {
    if (!snap) return;
    const node = document.querySelector('#mus-detail [data-mus-field="' + snap.key + '"]');
    if (!node) return;
    node.value = snap.value;
    node.focus();
    // У input[type=date] выделения нет — попытка его поставить бросает.
    try { node.setSelectionRange(snap.start, snap.end); } catch (e) { /* не текст */ }
  }

  function renderAll() {
    renderTop();
    renderList();
    if (M.open) renderDetail();
  }

  function renderDetail() {
    const det = byId("mus-detail");
    if (!det) return;
    const snap = captureFocus();
    det.innerHTML = "";
    const o = M.open;
    if (!o) return;

    // ── шапка карточки ──
    const head = el("div", "mus-head");
    const back = el("button", "ghost", "← " + T("open.back"));
    back.type = "button";
    back.addEventListener("click", closeTrack);
    const title = el("input", "mus-title-in");
    title.setAttribute("data-mus-field", "title");
    title.value = o.title || "";
    title.placeholder = T("open.titlePh");
    const artist = el("input", "mus-artist-in");
    artist.setAttribute("data-mus-field", "artist");
    artist.value = o.artist || "";
    artist.placeholder = T("open.artistPh");
    const save = (field, input) => {
      input.addEventListener("change", () => {
        patchMeta({ [field]: input.value.trim() });
      });
    };
    save("title", title);
    save("artist", artist);
    const del = el("button", "ghost danger", "✕");
    del.type = "button";
    del.title = T("open.del");
    del.addEventListener("click", async () => {
      if (!confirm(T("open.delConfirm"))) return;
      try {
        await api("/api/music/tracks/" + o.id, { method: "DELETE" });
        M.items = M.items.filter((x) => x.id !== o.id);
        M.total = Math.max(0, M.total - 1);
        closeTrack();
      } catch (e) { fail(e); }
    });
    head.append(back, title, artist, del);
    det.appendChild(head);

    // ── четыре раздела ──
    const tabs = el("div", "mus-tabs");
    [["sound", T("tabs.sound")], ["master", T("tabs.master")],
     ["stems", T("sec.stems")],
     ["release", T("tabs.release")], ["social", T("tabs.social")]].forEach(([id, label]) => {
      const b = el("button", "mus-tab" + (M.tab === id ? " on" : ""), label);
      b.type = "button";
      b.addEventListener("click", () => { M.tab = id; stopAB(); renderDetail(); });
      tabs.appendChild(b);
    });
    det.appendChild(tabs);

    const pane = el("div", "mus-pane on");
    det.appendChild(pane);
    if (M.tab === "sound") paneSound(pane, o);
    if (M.tab === "master") paneMaster(pane, o);
    if (M.tab === "stems") paneStems(pane, o);
    if (M.tab === "release") paneRelease(pane, o);
    if (M.tab === "social") paneSocial(pane, o);
    restoreFocus(snap);
  }

  async function patchMeta(patch) {
    if (!M.open) return;
    try {
      const fresh = await api("/api/music/tracks/" + M.open.id, { method: "PATCH", body: patch });
      M.open = fresh;
      const row = M.items.find((x) => x.id === fresh.id);
      if (row) Object.assign(row, { title: fresh.title, artist: fresh.artist });
      renderList();
    } catch (e) {
      fail(e);
    }
  }

  // ────────────────────────── раздел «Звук» ──────────────────────────

  function num(box, label, value, extra) {
    const n = el("div", "mus-num");
    n.appendChild(el("i", null, label));
    const b = el("b", null, value);
    if (extra) {
      const s = el("s", null, extra);
      b.appendChild(s);
    }
    n.appendChild(b);
    box.appendChild(n);
  }

  function drawWave(canvas, wave, playedRatio) {
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = canvas.clientWidth || 600;
    const h = canvas.clientHeight || 92;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const data = wave && wave.length ? wave : [];
    if (!data.length) return;
    const bars = Math.max(40, Math.min(data.length, Math.floor(w / 3)));
    const step = data.length / bars;
    const bw = w / bars;
    const mid = h / 2;
    for (let i = 0; i < bars; i += 1) {
      let peak = 0;
      for (let j = Math.floor(i * step); j < Math.floor((i + 1) * step); j += 1) {
        peak = Math.max(peak, data[j] || 0);
      }
      const bh = Math.max(1.5, peak * (h - 8));
      const played = (i / bars) <= (playedRatio || 0);
      ctx.fillStyle = played ? "#c1401b" : "rgba(45,33,26,.32)";
      ctx.fillRect(i * bw + bw * 0.18, mid - bh / 2, Math.max(1, bw * 0.64), bh);
    }
  }

  function paneSound(pane, o) {
    const card = el("div", "mus-card");
    const wrap = el("div", "mus-wave-wrap");
    const canvas = el("canvas", "mus-wave");
    wrap.appendChild(canvas);
    const note = el("div", "mus-wave-note");
    wrap.appendChild(note);
    card.appendChild(wrap);

    const nums = el("div", "mus-nums");
    num(nums, T("sound.dur"), fmtDur(o.duration_sec));
    num(nums, T("sound.format"), (o.source_ext || "?").toUpperCase(), fmtMB(o.source_bytes));
    if (o.probe_status === "done" || o.lufs) {
      num(nums, T("sound.lufs"), o.lufs ? `${o.lufs}` : "—", "LUFS");
      num(nums, T("sound.peak"), o.true_peak ? `${o.true_peak}` : "—", "dBTP");
      num(nums, T("sound.dyn"), o.lra ? `${o.lra}` : "—", "LU");
    }
    if (o.bpm) num(nums, T("sound.bpm"), String(o.bpm), "BPM");
    card.appendChild(nums);

    if (o.probe_status === "queued" || o.probe_status === "running") {
      note.textContent = T("sound.measuring");
      card.appendChild(status("run", T("sound.measuring")));
    } else if (o.probe_status === "error") {
      card.appendChild(status("err", T("sound.failed") + (o.probe_note ? " " + o.probe_note : "")));
    } else if (!(o.wave || []).length) {
      note.textContent = T("sound.noWave");
    }
    drawWave(canvas, o.wave, 0);

    const player = el("audio", "mus-player");
    player.controls = true;
    player.preload = "none";
    player.src = o.master_url || o.source_url;
    player.addEventListener("timeupdate", () => {
      if (!player.duration) return;
      drawWave(canvas, o.wave, player.currentTime / player.duration);
    });
    card.appendChild(player);
    if (o.master_url) card.appendChild(el("p", "mus-fh", T("sound.playingMaster")));

    // Секции трека — тонкая полоса под волной. Отдельная картинка секций
    // не нужна: это тот же трек, просто размеченный.
    const a = o.analysis;
    if (a && (a.sections || []).length && o.duration_sec) {
      const strip = el("div", "mus-sections");
      a.sections.forEach((s) => {
        const seg = el("div", "mus-sec mus-sec-" + (s.label || "steady"));
        seg.style.flex = String(Math.max(0.02, (s.end - s.start) / o.duration_sec));
        seg.title = `${fmtDur(s.start)} · ${T("sound.sec_" + (s.label || "steady"))}`;
        strip.appendChild(seg);
      });
      card.appendChild(strip);
      const legend = el("div", "mus-legend");
      ["quiet", "steady", "full", "peak"].forEach((k) => {
        legend.appendChild(el("span", null, T("sound.sec_" + k)));
      });
      if (a.bpm_alt && a.bpm_alt !== a.bpm) {
        legend.appendChild(el("span", null, T("sound.bpmAlt", { n: a.bpm_alt })));
      }
      card.appendChild(legend);
    } else if (M.status && M.status.analysis && !S("analysis").enabled) {
      card.appendChild(el("p", "mus-fh", T("sound.tempoOff")));
    }

    card.appendChild(el("p", "mus-fh", T("sound.note")));
    pane.appendChild(card);
    pane.appendChild(keyCard(o));
    // Волну перерисовываем после вставки в DOM: до неё clientWidth равен нулю.
    requestAnimationFrame(() => drawWave(canvas, o.wave, 0));
  }

  /* Темп и тональность: разбор на своём CPU, бесплатно. Результат кэшится
     на сервере (analysis_json), повторный клик — пересчёт. */
  function keyCard(o) {
    const card = el("div", "mus-card");
    card.appendChild(el("h3", null, T("key.title")));
    const a = M.analysis && M.analysis.id === o.id ? M.analysis.data : null;
    if (a) {
      const nums = el("div", "mus-nums");
      num(nums, T("sound.bpm"), String(a.bpm || "—"), "BPM");
      if (a.key) {
        num(nums, T("key.title"), T("key.keyLine", { key: a.key, scale: a.scale || "" }));
      }
      card.appendChild(nums);
      if (a.bpm_alt && a.bpm_alt !== a.bpm) {
        card.appendChild(el("p", "mus-fh", T("key.bpmAltNote", { alt: a.bpm_alt })));
      }
      if (a.key_confidence !== undefined && a.key_confidence !== null && a.key) {
        card.appendChild(el("p", "mus-fh", T("key.keyNote", { c: a.key_confidence })));
      }
      if (a.key_error) {
        card.appendChild(el("p", "mus-fh", T("key.failed", { err: a.key_error })));
      }
    }
    const row = el("div", "mus-run");
    const btn = el("button", a ? "ghost" : "primary",
      M.keyBusy ? T("key.running") : T("key.run"));
    btn.type = "button";
    btn.disabled = M.keyBusy;
    btn.addEventListener("click", async () => {
      M.keyBusy = true;
      renderDetail();
      try {
        const d = await api(`/api/music/tracks/${o.id}/analysis` + (a ? "?refresh=true" : ""));
        M.analysis = { id: o.id, data: d };
      } catch (e) { fail(e); } finally {
        M.keyBusy = false;
        renderDetail();
      }
    });
    row.appendChild(btn);
    card.appendChild(row);
    return card;
  }

  // ────────────────────────── раздел «Дорожки» ──────────────────────────

  function paneStems(pane, o) {
    const card = el("div", "mus-card");
    card.appendChild(el("h3", null, T("stems.title")));
    card.appendChild(el("p", "mus-sub", T("stems.note")));
    const row = el("div", "mus-run");
    const btn = el("button", "primary", M.stemsBusy ? T("stems.running") : T("stems.run"));
    btn.type = "button";
    btn.disabled = M.stemsBusy;
    btn.addEventListener("click", async () => {
      M.stemsBusy = true;
      M.stemsRes = null;
      renderDetail();
      try {
        const r = await api(`/api/music/tracks/${o.id}/stems`, { method: "POST" });
        M.stemsTask = r.task_id;
        pollStems(o.id);
      } catch (e) {
        M.stemsBusy = false;
        fail(e);
        renderDetail();
      }
    });
    row.appendChild(btn);
    card.appendChild(row);
    if (M.stemsBusy) card.appendChild(status("run", T("stems.running")));
    if (M.stemsRes) {
      if (M.stemsRes.error) {
        card.appendChild(status("err", T("stems.failed")));
      } else {
        [["vocal", M.stemsRes.vocal_url],
         ["instrumental", M.stemsRes.instrumental_url]].forEach(([k, url]) => {
          if (!url) return;
          const line = el("div", "mus-run");
          line.appendChild(el("b", null, T("stems." + k)));
          const au = el("audio");
          au.controls = true;
          au.preload = "none";
          au.src = url;
          au.style.flex = "1 1 200px";
          const dl = el("a", null, "⬇");
          dl.href = url;
          dl.target = "_blank";
          dl.rel = "noopener";
          line.append(au, dl);
          card.appendChild(line);
        });
      }
    }
    pane.appendChild(card);
  }

  function pollStems(trackId) {
    if (!M.stemsTask) return;
    setTimeout(async () => {
      let st = null;
      try {
        st = await api(`/api/music/stems/${M.stemsTask}`);
      } catch (e) { /* сеть моргнула — следующий тик */ }
      if (st) {
        const code = String(st.status || "").toUpperCase();
        const done = Boolean(st.vocal_url || st.instrumental_url);
        const dead = ["CREATE_TASK_FAILED", "GENERATE_FAILED", "FAILED",
                      "ERROR", "SENSITIVE_WORD_ERROR"].includes(code);
        if (done || dead) {
          M.stemsBusy = false;
          M.stemsRes = done ? st : { error: true };
          M.stemsTask = null;
          if (visible() && M.open && M.open.id === trackId && M.tab === "stems") renderDetail();
          return;
        }
      }
      pollStems(trackId);
    }, 5000);
  }

  // ────────────────────────── раздел «Дистрибуция» ──────────────────────────
  // Страница строится ОДИН раз: перерисовка формы на каждом заходе стирала
  // бы набранное. Список заявок обновляется отдельным контейнером.

  function distField(form, key, kind) {
    const wrap = el("label", kind === "textarea" ? "wide" : "");
    wrap.appendChild(el("span", null, T("dist." + key)));
    let input;
    if (kind === "textarea") { input = el("textarea"); input.rows = 2; }
    else { input = el("input"); input.type = "text"; }
    input.setAttribute("data-dist-field", key);
    wrap.appendChild(input);
    form.appendChild(wrap);
    return input;
  }

  async function renderDist(box) {
    if (!box.dataset.built) {
      box.dataset.built = "1";
      box.innerHTML = "";
      const card = el("div", "mus-card");
      card.appendChild(el("h3", null, T("dist.title")));
      card.appendChild(el("p", "mus-sub", T("dist.note")));
      const form = el("div", "mus-form");
      ["artist", "track", "genre", "contact", "isrc"].forEach((k) => distField(form, k));
      distField(form, "socials", "textarea");
      distField(form, "comment", "textarea");
      // раскрытие ИИ
      const aiWrap = el("label");
      aiWrap.appendChild(el("span", null, T("dist.aiLabel")));
      const ai = el("select");
      [["", "unset"], ["none", "none"], ["music", "music"],
       ["vocals", "vocals"], ["all", "all"]].forEach(([v, k]) => {
        const op = document.createElement("option");
        op.value = v;
        op.textContent = T("dist.ai_" + k);
        ai.appendChild(op);
      });
      aiWrap.appendChild(ai);
      form.appendChild(aiWrap);
      card.appendChild(form);

      // файлы + требования
      const req = el("div", "mus-dist");
      req.appendChild(el("b", null, T("dist.reqTitle")));
      req.appendChild(el("p", "mus-fh", T("dist.req1")));
      req.appendChild(el("p", "mus-fh", T("dist.req2")));
      card.appendChild(req);
      const files = el("div", "mus-form");
      const fileRow = (key, accept) => {
        const wrap = el("label", "wide");
        wrap.appendChild(el("span", null, T("dist." + key)));
        const input = el("input");
        input.type = "file";
        input.accept = accept;
        wrap.appendChild(input);
        files.appendChild(wrap);
        return input;
      };
      const audioIn = fileRow("audio", ".wav,.flac,.aif,.aiff,audio/wav,audio/flac");
      const coverIn = fileRow("cover", ".jpg,.jpeg,.png,image/jpeg,image/png");
      card.appendChild(files);

      // права и согласия
      const checks = el("div");
      const checkbox = (key) => {
        const lab = el("label", "mus-match");
        const cb = el("input");
        cb.type = "checkbox";
        lab.append(cb, el("span", null, T("dist." + key)));
        checks.appendChild(lab);
        return cb;
      };
      const cbOriginal = checkbox("original");
      const cbAgree = checkbox("agree");
      card.appendChild(checks);

      const errBox = el("div");
      card.appendChild(errBox);
      const row = el("div", "mus-run");
      const send = el("button", "primary", T("dist.submit"));
      send.type = "button";
      row.appendChild(send);
      card.appendChild(row);
      box.appendChild(card);

      // черновик соглашения
      const agr = el("div", "mus-card");
      agr.appendChild(el("h3", null, T("dist.agreementTitle")));
      agr.appendChild(el("p", "mus-sub", T("dist.agreementNote")));
      const pre = el("pre", "mus-agreement");
      pre.style.whiteSpace = "pre-wrap";
      pre.style.font = "12px/1.5 ui-monospace, monospace";
      pre.style.maxHeight = "320px";
      pre.style.overflow = "auto";
      agr.appendChild(pre);
      box.appendChild(agr);
      api("/api/music/demo/agreement")
        .then((r) => { pre.textContent = r.text || ""; })
        .catch(() => { pre.textContent = "—"; });

      const mine = el("div", "mus-card");
      mine.appendChild(el("h3", null, T("dist.mineTitle")));
      const mineList = el("div", "mus-checks");
      mine.id = "mus-dist-mine";
      mine.appendChild(mineList);
      box.appendChild(mine);

      send.addEventListener("click", async () => {
        errBox.innerHTML = "";
        const val = (k) => {
          const n = box.querySelector(`[data-dist-field="${k}"]`);
          return n ? n.value.trim() : "";
        };
        const fd = new FormData();
        fd.append("artist", val("artist"));
        fd.append("track_title", val("track"));
        fd.append("genre", val("genre"));
        fd.append("socials", val("socials"));
        fd.append("contact", val("contact"));
        fd.append("isrc", val("isrc"));
        fd.append("comment", val("comment"));
        fd.append("ai_disclosure", ai.value);
        fd.append("original_confirm", cbOriginal.checked ? "1" : "");
        fd.append("agree_terms", cbAgree.checked ? "1" : "");
        if (audioIn.files && audioIn.files[0]) fd.append("audio_file", audioIn.files[0]);
        if (coverIn.files && coverIn.files[0]) fd.append("cover_file", coverIn.files[0]);
        send.disabled = true;
        send.textContent = T("dist.sending");
        try {
          const r = await xhrUpload("/api/music/demo", fd);
          errBox.appendChild(status("ok", T("dist.sent")));
          (r.checks || []).filter((c) => c.level === "warn").forEach((c) => {
            errBox.appendChild(status("warn", c.text));
          });
          loadMyDemos();
        } catch (e) {
          const data = (e && e.data) || {};
          const list = data.checks;
          if (list && list.length) {
            errBox.appendChild(status("err", T("dist.fix")));
            list.filter((c) => c.level !== "ok").forEach((c) => {
              errBox.appendChild(status(c.level === "fail" ? "err" : "warn", c.text));
            });
          } else {
            errBox.appendChild(status("err", apiErr(e)));
          }
        } finally {
          send.disabled = false;
          send.textContent = T("dist.submit");
        }
      });
    }
    loadMyDemos();
  }

  async function loadMyDemos() {
    const mine = byId("mus-dist-mine");
    if (!mine) return;
    const list = mine.querySelector(".mus-checks");
    let data;
    try { data = await api("/api/music/demo/mine"); } catch (e) { return; }
    list.innerHTML = "";
    (data.items || []).forEach((d) => {
      const row = el("div", "mus-check " + (d.status === "accepted" ? "ok"
        : d.status === "declined" ? "fail" : "warn"));
      row.appendChild(el("span", "mus-dot"));
      const mid = el("span");
      mid.appendChild(el("b", null, `${d.artist} — ${d.track_title}`));
      mid.appendChild(el("span", null, " · " + (T("dist.st_" + d.status) || d.status)));
      row.appendChild(mid);
      row.appendChild(el("span", "mus-check-val",
        (d.created_at || "").slice(0, 10)));
      list.appendChild(row);
    });
    mine.classList.toggle("hidden", !(data.items || []).length);
  }

  function status(kind, text) {
    const box = el("div", "mus-status " + kind);
    box.appendChild(el("span", "mus-dot"));
    box.appendChild(el("span", null, text));
    return box;
  }

  // ────────────────────────── раздел «Мастеринг» ──────────────────────────

  function engineRow(id) {
    const list = S("mastering").engines || [];
    return list.find((x) => x.id === id) || null;
  }

  function paneMaster(pane, o) {
    const card = el("div", "mus-card");
    card.appendChild(el("h3", null, T("master.profile")));
    card.appendChild(el("p", "mus-sub", T("master.profileNote")));

    const targets = S("mastering").targets || [];
    const chips = el("div", "mus-chips");
    targets.forEach((tg) => {
      const b = el("button", "mus-chip" + (M.target === tg.id ? " on" : ""));
      b.type = "button";
      b.appendChild(el("span", null, T("master.p_" + tg.id)));
      b.appendChild(el("em", null, `${tg.lufs} LUFS`));
      b.addEventListener("click", () => { M.target = tg.id; renderDetail(); });
      chips.appendChild(b);
    });
    card.appendChild(chips);
    const chosen = targets.find((x) => x.id === M.target);
    if (chosen) {
      card.appendChild(el("div", "mus-forecast",
        T("master.targetLine", { lufs: chosen.lufs, tp: chosen.tp,
                                 what: T("master.p_" + chosen.id) })));
    }

    // ── движок ──
    const eng = el("div", "mus-engine");
    eng.appendChild(el("h3", null, T("master.engine")));
    const engChips = el("div", "mus-chips");
    const options = [["auto", true], ["matchering", true], ["ffmpeg", true], ["roex", true]];
    options.forEach(([id]) => {
      const row = id === "auto" ? null : engineRow(id);
      // Платный движок без ключа не показываем вовсе: кнопка, которая
      // отвечает «не подключено», ничего не продаёт и только раздражает.
      if (id === "roex" && (!row || !row.ready)) return;
      const b = el("button", "mus-chip" + (M.engine === id ? " on" : ""));
      b.type = "button";
      b.appendChild(el("span", null, T("master.e_" + id)));
      if (id === "roex") b.appendChild(el("em", null, T("master.paid")));
      if (row && !row.ready && id !== "auto") {
        b.disabled = true;
        b.title = T("master.offline");
      }
      b.addEventListener("click", () => { M.engine = id; renderDetail(); });
      engChips.appendChild(b);
    });
    eng.appendChild(engChips);
    eng.appendChild(el("p", "mus-engine-note", T("master.n_" + M.engine)));
    const mch = engineRow("matchering");
    if (mch && !mch.ready) {
      eng.appendChild(el("p", "mus-engine-note", T("master.matcheringOff")));
    }
    card.appendChild(eng);

    // ── эталон ──
    const refBox = el("div", "mus-ref");
    const shelf = (M.refs && M.refs.items) || [];
    const sel = el("select");
    const opt = (val, label) => {
      const o2 = document.createElement("option");
      o2.value = val;
      o2.textContent = label;
      sel.appendChild(o2);
    };
    opt("none", T("master.refNone"));
    shelf.forEach((r) => opt("lib:" + r.id, r.title + (r.genre ? ` · ${r.genre}` : "")));
    M.items.filter((x) => x.id !== o.id).slice(0, 20).forEach((x) => {
      opt("own:" + x.id, T("master.refOwn", { name: x.title || x.source_name }));
    });
    opt("file", T("master.refUpload"));
    sel.value = M.refKind === "library" ? "lib:" + M.refId
      : M.refKind === "track" ? "own:" + M.refId
        : M.refKind === "upload" ? "file" : "none";
    sel.addEventListener("change", () => {
      const v = sel.value;
      M.refFile = null;
      if (v === "none") { M.refKind = "none"; M.refId = ""; }
      else if (v.startsWith("lib:")) { M.refKind = "library"; M.refId = v.slice(4); }
      else if (v.startsWith("own:")) { M.refKind = "track"; M.refId = v.slice(4); }
      else {
        M.refKind = "upload";
        const inp = byId("mus-ref-file");
        if (inp) inp.click();
      }
      renderDetail();
    });
    refBox.appendChild(sel);
    if (M.refKind === "upload") {
      refBox.appendChild(el("span", "mus-refname",
        M.refFile ? M.refFile.name : T("master.refPick")));
    } else if (o.master_ref_title && M.refKind === "none") {
      refBox.appendChild(el("span", "mus-refname",
        T("master.refWas", { name: o.master_ref_title })));
    }
    card.appendChild(refBox);
    if (!shelf.length && M.refs) {
      card.appendChild(el("p", "mus-fh", T("master.shelfEmpty")));
    }

    // ── запуск ──
    const run = el("div", "mus-run");
    const cost = M.engine === "roex"
      ? S("cost").master_cloud
      : S("cost").master;
    const btn = el("button", "primary", T("master.run", { n: cost, unit: unit(cost) }));
    btn.type = "button";
    const busy = o.master_status === "queued" || o.master_status === "running";
    btn.disabled = busy;
    btn.addEventListener("click", () => runMaster(o));
    run.appendChild(btn);
    run.appendChild(el("span", "mus-price", T("master.costNote", { n: cost, unit: unit(cost) })));
    card.appendChild(run);
    if (M.engine === "roex") card.appendChild(el("p", "mus-fh", T("master.cloudNote")));

    if (busy) card.appendChild(status("run", T("master.running")));
    if (o.master_status === "error") {
      card.appendChild(status("err", [T("master.error"), o.master_note]
        .filter(Boolean).join(" ")));
    }

    // ── до/после ──
    if (o.master_status === "done" && o.master_url) card.appendChild(abBlock(o));
    pane.appendChild(card);
  }

  /* Сравнение «до/после».

     Оба файла играют одновременно, слышен ровно один — так переключение
     мгновенное и без рассинхрона. Выравнивание по громкости включено ПО
     УМОЛЧАНИЮ: мастер всегда громче, а громче на слух означает «лучше»,
     даже когда это не так. Коэффициент считается из измеренных LUFS и
     применяется к громкому файлу (volume ≤ 1 — линейная амплитуда). */
  const AB = { a: null, b: null };

  function stopAB() {
    [AB.a, AB.b].forEach((x) => { if (x) { try { x.pause(); } catch (e) { /* нет */ } } });
    AB.a = AB.b = null;
  }

  function applyMatch(o) {
    if (!AB.a || !AB.b) return;
    const src = Number(o.lufs || 0);
    const mas = Number(o.master_lufs || 0);
    let va = 1;
    let vb = 1;
    if (M.match && src && mas) {
      // Громкому — понижение, тихому — единица. Поднимать нельзя: > 1 в
      // HTML5 audio не бывает, и «выравнивание вверх» клиппировало бы.
      if (mas > src) vb = Math.pow(10, (src - mas) / 20);
      else va = Math.pow(10, (mas - src) / 20);
    }
    AB.a.volume = Math.max(0, Math.min(1, va));
    AB.b.volume = Math.max(0, Math.min(1, vb));
    AB.a.muted = M.ab !== "before";
    AB.b.muted = M.ab !== "after";
  }

  function abBlock(o) {
    const box = el("div", "mus-ab");
    const row = el("div", "mus-ab-row");
    [["before", T("master.listenBefore")], ["after", T("master.listenAfter")]].forEach(([id, label]) => {
      const b = el("button", "mus-chip" + (M.ab === id ? " on" : ""), label);
      b.type = "button";
      b.addEventListener("click", () => {
        M.ab = id;
        applyMatch(o);
        Array.from(row.querySelectorAll(".mus-chip")).forEach((x, i) => {
          x.classList.toggle("on", (i === 0) === (id === "before"));
        });
      });
      row.appendChild(b);
    });
    const play = el("button", null, "▶");
    play.type = "button";
    play.addEventListener("click", () => {
      if (!AB.a) {
        AB.a = new Audio(o.source_url);
        AB.b = new Audio(o.master_url);
        AB.a.preload = "auto";
        AB.b.preload = "auto";
      }
      applyMatch(o);
      if (AB.a.paused) {
        // Обе дорожки стартуют с одной позиции — иначе сравнение сравнивает
        // разные куски трека, и это худший вид вранья: незаметный.
        const at = AB.a.currentTime;
        AB.b.currentTime = at;
        AB.a.play();
        AB.b.play();
        play.textContent = "❚❚";
      } else {
        AB.a.pause();
        AB.b.pause();
        play.textContent = "▶";
      }
    });
    row.appendChild(play);

    const match = el("label", "mus-match");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = M.match;
    cb.addEventListener("change", () => { M.match = cb.checked; applyMatch(o); renderMatchNote(); });
    match.append(cb, el("span", null, T("master.match")));
    row.appendChild(match);
    box.appendChild(row);

    const mn = el("p", "mus-fh");
    mn.id = "mus-match-note";
    box.appendChild(mn);
    function renderMatchNote() {
      const node = document.getElementById("mus-match-note");
      if (node) node.textContent = M.match ? T("master.matchOn") : T("master.matchOff");
    }
    setTimeout(renderMatchNote, 0);

    const delta = el("div", "mus-delta");
    const line = (label, before, after, unit) => {
      const d = el("div");
      d.appendChild(el("span", null, label + ": "));
      d.appendChild(el("b", null, `${before ?? "—"} → ${after ?? "—"} ${unit}`));
      delta.appendChild(d);
    };
    line(T("sound.lufs"), o.lufs, o.master_lufs, "LUFS");
    line(T("sound.peak"), o.true_peak, o.master_true_peak, "dBTP");
    line(T("sound.dyn"), o.lra, o.master_lra, "LU");
    box.appendChild(delta);

    if (o.master_note) box.appendChild(el("p", "mus-ab-note", o.master_note));

    const dl = el("div", "mus-run");
    const btn = el("button", null, T("master.download"));
    btn.type = "button";
    btn.addEventListener("click", () => download(o.master_filename));
    dl.appendChild(btn);
    if (o.master_engine) {
      dl.appendChild(el("span", "mus-price",
        T("master.madeWith", { engine: T("master.e_" + o.master_engine) })));
    }
    box.appendChild(dl);
    return box;
  }

  async function download(filename) {
    if (!filename) return;
    try {
      const r = await api("/api/files/link/" + encodeURIComponent(filename));
      const a = document.createElement("a");
      a.href = r.url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) { fail(e); }
  }

  async function runMaster(o) {
    try {
      const fd = new FormData();
      fd.append("engine", M.engine);
      fd.append("target", M.target);
      fd.append("ref_kind", M.refKind);
      if (M.refKind === "library" || M.refKind === "track") fd.append("ref_id", M.refId);
      if (M.refKind === "upload" && M.refFile) fd.append("reference", M.refFile);
      await api(`/api/music/tracks/${o.id}/master`, { method: "POST", body: fd });
      M.open.master_status = "queued";
      renderDetail();
      schedulePoll();
    } catch (e) {
      fail(e);
    }
  }

  // ────────────────────────── раздел «Релиз» ──────────────────────────

  const FIELDS = [
    ["title", "text", false], ["artist", "text", false],
    ["feat", "text", false], ["genre", "text", false],
    ["version", "text", false], ["language", "text", false],
    ["release_date", "date", false], ["isrc", "text", false],
    ["upc", "text", false], ["ai_disclosure", "select", false],
    ["credits", "textarea", true], ["notes", "textarea", true],
  ];

  function paneRelease(pane, o) {
    const card = el("div", "mus-card");
    card.appendChild(el("h3", null, T("release.meta")));
    card.appendChild(el("p", "mus-sub", T("release.metaNote")));

    const form = el("div", "mus-form");
    FIELDS.forEach(([key, kind, wide]) => {
      const wrap = el("label", wide ? "wide" : "");
      wrap.appendChild(el("span", null, T("release.f_" + key)));
      let input;
      if (kind === "textarea") {
        input = el("textarea");
        input.rows = 2;
      } else if (kind === "select") {
        input = el("select");
        ["", "none", "music", "vocals", "all"].forEach((v) => {
          const op = document.createElement("option");
          op.value = v;
          op.textContent = T("release.ai_" + (v || "unset"));
          input.appendChild(op);
        });
      } else {
        input = el("input");
        input.type = kind;
      }
      input.setAttribute("data-mus-field", key);
      input.value = (o.meta && o.meta[key]) || "";
      input.addEventListener("change", () => patchMeta({ [key]: input.value }));
      wrap.appendChild(input);
      const hint = T("release.h_" + key);
      if (hint) wrap.appendChild(el("p", "mus-fh", hint));
      form.appendChild(wrap);
    });
    const exp = el("label", "wide");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = Boolean(o.meta && o.meta.explicit);
    cb.style.width = "auto";
    cb.addEventListener("change", () => patchMeta({ explicit: cb.checked }));
    const expRow = el("span", "mus-match");
    expRow.append(cb, el("span", null, T("release.f_explicit")));
    exp.appendChild(expRow);
    form.appendChild(exp);
    card.appendChild(form);

    // ── обложка ──
    const cover = el("label", "mus-cover");
    const img = el("img", "mus-cover-img");
    img.alt = "";
    if (o.cover_url) img.src = o.cover_url;
    const txt = el("div", "mus-cover-txt");
    txt.appendChild(el("b", null, o.cover_url ? T("release.coverHave") : T("release.coverNone")));
    txt.appendChild(el("span", null, o.cover_w
      ? T("release.coverSize", { w: o.cover_w, h: o.cover_h })
      : T("release.coverHint", { px: S("upload").cover_target || 3000 })));
    const file = el("input");
    file.type = "file";
    file.accept = "image/jpeg,image/png,image/webp";
    file.className = "hidden";
    file.addEventListener("change", async () => {
      const f = file.files && file.files[0];
      file.value = "";
      if (!f) return;
      try {
        const fd = new FormData();
        fd.append("file", f);
        await xhrUpload(`/api/music/tracks/${o.id}/cover`, fd);
        M.open = await api("/api/music/tracks/" + o.id);
        const row = M.items.find((x) => x.id === o.id);
        if (row) row.cover_url = M.open.cover_url;
        renderDetail();
        renderList();
      } catch (e) { alert(apiErr(e)); }
    });
    cover.append(img, txt, file);
    card.appendChild(cover);
    pane.appendChild(card);

    // ── проверки ──
    const checks = el("div", "mus-card");
    checks.appendChild(el("h3", null, T("release.checks")));
    const list = el("div", "mus-checks");
    checks.appendChild(list);
    loadChecks(o.id, list, checks);
    pane.appendChild(checks);

    // ── пакет и заявка ──
    const out = el("div", "mus-card");
    out.appendChild(el("h3", null, T("release.deliver")));
    out.appendChild(el("p", "mus-sub", T("release.deliverNote")));

    const row = el("div", "mus-run");
    const pack = el("button", null, T("release.pack"));
    pack.type = "button";
    pack.addEventListener("click", async () => {
      pack.disabled = true;
      pack.textContent = T("release.packing");
      try {
        const r = await api(`/api/music/tracks/${o.id}/package`, { method: "POST" });
        M.open.package_url = r.url;
        M.open.package_filename = r.filename;
        download(r.filename);
        renderDetail();
      } catch (e) {
        fail(e);
      } finally {
        pack.disabled = false;
        pack.textContent = T("release.pack");
      }
    });
    row.appendChild(pack);
    if (o.package_url) {
      const again = el("button", "ghost", T("release.packDownload"));
      again.type = "button";
      again.addEventListener("click", () => download(o.package_filename));
      row.appendChild(again);
    }
    out.appendChild(row);
    out.appendChild(el("p", "mus-fh", T("release.packNote")));

    const dist = el("div", "mus-dist");
    const d = S("distribution");
    dist.appendChild(el("b", null, T("release.distTitle")));
    dist.appendChild(el("span", null,
      d.ready ? T("release.distReady", { name: d.distributor })
        : T("release.distNotYet", { name: d.distributor })));
    const stores = el("div", "mus-stores");
    (d.stores || []).forEach((s) => stores.appendChild(el("span", "mus-store", s)));
    dist.appendChild(stores);
    dist.appendChild(el("p", "mus-fh", T("release.distTiming", {
      days: d.review_days || "1–5", pitch: d.pitch_days || 14 })));
    out.appendChild(dist);

    if (o.release_status === "submitted") {
      out.appendChild(status("ok", T("release.submitted")));
    } else {
      const send = el("div", "mus-run");
      const contact = el("input");
      contact.setAttribute("data-mus-field", "contact");
      contact.placeholder = T("release.contactPh");
      contact.style.flex = "1 1 200px";
      const btn = el("button", "primary", T("release.submit"));
      btn.type = "button";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await api(`/api/music/tracks/${o.id}/submit`, {
            method: "POST",
            body: { contact: contact.value.trim(), lang: typeof LANG === "string" ? LANG : "" },
          });
          M.open.release_status = "submitted";
          M.open.lead_id = r.lead_id;
          renderDetail();
        } catch (e) {
          const miss = e && e.data && e.data.missing;
          if (miss && miss.length) {
            alert(T("release.fixFirst", {
              list: miss.map((k) => T("release.c_" + k)).join(", ") }));
          } else {
            fail(e);
          }
        } finally {
          btn.disabled = false;
        }
      });
      send.append(contact, btn);
      out.appendChild(send);
    }
    pane.appendChild(out);
  }

  async function loadChecks(id, list, card) {
    list.innerHTML = "";
    for (let i = 0; i < 3; i += 1) list.appendChild(el("div", "mus-skel"));
    let data;
    try {
      data = await api(`/api/music/tracks/${id}/check`);
    } catch (e) {
      list.innerHTML = "";
      list.appendChild(el("p", "mus-fh", apiErr(e)));
      return;
    }
    list.innerHTML = "";
    (data.checks || []).forEach((c) => {
      const row = el("div", "mus-check " + c.level);
      row.appendChild(el("span", "mus-dot"));
      const mid = el("span");
      mid.appendChild(el("b", null, T("release.c_" + c.key)));
      const hint = T(`release.${c.level}_${c.key}`);
      if (hint) mid.appendChild(el("span", null, " — " + hint));
      row.appendChild(mid);
      row.appendChild(el("span", "mus-check-val", c.value || ""));
      list.appendChild(row);
    });
    if (card) {
      const sum = el("p", "mus-fh", data.fails
        ? T("release.checkFails", { n: data.fails })
        : T("release.checkOk", { n: data.warns }));
      card.appendChild(sum);
    }
  }

  // ────────────────────────── раздел «Соцсети» ──────────────────────────

  async function paneSocial(pane, o) {
    const card = el("div", "mus-card");
    card.appendChild(el("h3", null, T("social.title")));
    card.appendChild(el("p", "mus-sub", T("social.note")));

    // 1. видео
    const vrow = el("div", "mus-run");
    const vcost = S("cost").video;
    const build = el("button", "primary", T("social.build", { n: vcost, unit: unit(vcost) }));
    build.type = "button";
    const vbusy = o.video_status === "queued" || o.video_status === "running";
    build.disabled = vbusy;
    build.addEventListener("click", async () => {
      try {
        const fd = new FormData();
        fd.append("shape", "vertical");
        fd.append("seconds", "60");
        await api(`/api/music/tracks/${o.id}/video`, { method: "POST", body: fd });
        M.open.video_status = "queued";
        renderDetail();
        schedulePoll();
      } catch (e) { fail(e); }
    });
    vrow.appendChild(build);
    vrow.appendChild(el("span", "mus-price", T("social.buildNote")));
    card.appendChild(vrow);
    if (!o.cover_url) card.appendChild(el("p", "mus-fh", T("social.noCover")));
    if (vbusy) card.appendChild(status("run", T("social.building")));
    if (o.video_status === "error") {
      // Человеческая фраза + технический хвост: без первой непонятно, что
      // случилось, без второго нечего показать в поддержку.
      card.appendChild(status("err", [T("social.videoError"), o.video_note]
        .filter(Boolean).join(" ")));
    }
    if (o.video_url) {
      const v = el("video", "mus-video");
      v.src = o.video_url;
      v.controls = true;
      v.preload = "metadata";
      card.appendChild(v);
      if (o.video_note) card.appendChild(el("p", "mus-fh", o.video_note));
    }
    pane.appendChild(card);

    // 2. публикация
    const pub = el("div", "mus-card");
    pub.appendChild(el("h3", null, T("social.publishTitle")));
    const state = el("div", "mus-social-state");
    const dot = el("span", "mus-live");
    state.append(dot, el("span", null, T("social.checking")));
    pub.appendChild(state);

    const chips = el("div", "mus-chips");
    const platforms = S("social").platforms || ["instagram"];
    let picked = platforms[0];
    platforms.forEach((p, i) => {
      const b = el("button", "mus-chip" + (i === 0 ? " on" : ""), T("social.p_" + p));
      b.type = "button";
      b.addEventListener("click", () => {
        picked = p;
        Array.from(chips.children).forEach((c) => c.classList.toggle("on", c === b));
      });
      chips.appendChild(b);
    });
    pub.appendChild(chips);

    const caption = el("textarea");
    caption.setAttribute("data-mus-field", "caption");
    caption.rows = 2;
    caption.placeholder = T("social.captionPh");
    caption.value = [o.artist, o.title].filter(Boolean).join(" — ");
    pub.appendChild(caption);

    const prow = el("div", "mus-run");
    const send = el("button", "primary", T("social.publish"));
    send.type = "button";
    send.disabled = true;
    send.addEventListener("click", async () => {
      try {
        const fd = new FormData();
        fd.append("platform", picked);
        fd.append("caption", caption.value.trim());
        await api(`/api/music/tracks/${o.id}/publish`, { method: "POST", body: fd });
        M.open.social_status = "queued";
        renderDetail();
        schedulePoll();
      } catch (e) { fail(e); }
    });
    prow.appendChild(send);
    pub.appendChild(prow);

    if (o.social_status === "queued" || o.social_status === "running") {
      pub.appendChild(status("run", T("social.publishing")));
    } else if (o.social_status === "error") {
      pub.appendChild(status("err", [T("social.publishError"), o.social_note]
        .filter(Boolean).join(" ")));
    } else if (o.social_status === "done") {
      const ok = status("ok", T("social.published"));
      if (o.social_url) {
        const a = el("a", null, T("social.open"));
        a.href = o.social_url;
        a.target = "_blank";
        a.rel = "noopener";
        ok.appendChild(a);
      }
      pub.appendChild(ok);
    }
    pane.appendChild(pub);

    // Живость службы спрашиваем ПОСЛЕ отрисовки: проверка идёт секундами,
    // и держать из-за неё пустой экран нельзя.
    try {
      const h = M.social && M.social.at && Date.now() - M.social.at < 60000
        ? M.social.data : null;
      const data = h || await api("/api/music/social");
      M.social = { at: Date.now(), data };
      const ready = Boolean(data.ready);
      dot.className = "mus-live " + (ready ? "on" : "off");
      // Техническая подробность («подними службу командой…») адресована
      // владельцу сервиса, а не артисту: обычному человеку она ничего не
      // объясняет и выглядит как утечка внутренностей.
      const detail = (M.status && M.status.is_admin && data.detail) ? " " + data.detail : "";
      state.lastChild.textContent = ready ? T("social.on") : T("social.off") + detail;
      send.disabled = !ready || !o.video_url || o.social_status === "running";
      if (!o.video_url && ready) {
        send.title = T("social.needVideo");
      }
    } catch (e) {
      dot.className = "mus-live off";
      state.lastChild.textContent = T("social.off");
    }
  }

  // ────────────────────────── проводка ──────────────────────────

  function wire() {
    const btn = byId("music-btn");
    if (btn) btn.addEventListener("click", show);
    const back = byId("mus-back");
    if (back) back.addEventListener("click", leave);

    const drop = byId("mus-drop");
    const input = byId("mus-file");
    if (drop && input) {
      drop.addEventListener("click", (e) => {
        if (e.target === input) return;
        input.click();
      });
      input.addEventListener("change", () => {
        const f = input.files && input.files[0];
        input.value = "";
        takeFile(f);
      });
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.add("over");
      }));
      ["dragleave", "dragend", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.remove("over");
      }));
      drop.addEventListener("drop", (e) => {
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        takeFile(f);
      });
    }

    const gen = byId("mus-gen-run");
    if (gen) gen.addEventListener("click", runGen);
    const secs = byId("mus-gen-secs");
    if (secs) secs.addEventListener("change", renderGenPrice);

    const refFile = byId("mus-ref-file");
    if (refFile) refFile.addEventListener("change", () => {
      M.refFile = refFile.files && refFile.files[0];
      refFile.value = "";
      M.refKind = M.refFile ? "upload" : "none";
      if (M.open) renderDetail();
    });

    if (typeof onLangChange === "function") {
      onLangChange(() => {
        if (!visible()) return;
        renderIntake();
        renderAll();
      });
    }

    window.addEventListener("resize", () => {
      if (!visible() || M.tab !== "sound" || !M.open) return;
      const c = document.querySelector("#mus-detail .mus-wave");
      if (c) drawWave(c, M.open.wave, 0);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  // Прямая ссылка /#/music и переход из реестра режимов.
  window.QlolMusic = { show, leave };
})();
