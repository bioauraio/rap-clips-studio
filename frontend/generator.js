/* Генератор: одна точка входа, два разных рабочих стола.
   «Фото» — галерея результатов + плавающий композер снизу.
   «Видео» — студия: панель настроек слева, крупный просмотр в центре,
   история рядом. Управление параметрами строго от возможностей модели
   (/api/chat/models): чего модель не умеет — того на экране нет.
   Бэкенд не трогаем: это витрина поверх существующего чат-конвейера
   (/api/chats, /api/chat/upload, сообщения = генерации). */
(() => {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ru = () => (localStorage.getItem("lolq_lang") || document.documentElement.lang || "ru") !== "en";
  const t = (a, b) => (ru() ? a : b);

  async function api(path, opts = {}) {
    const o = { credentials: "same-origin", ...opts };
    if (o.body && !(o.body instanceof FormData)) {
      o.headers = { "Content-Type": "application/json", ...(o.headers || {}) };
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch(path, o);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  /* ─────────── состояние ─────────── */
  const S = {
    meta: null,          // ответ /api/chat/models
    chatId: null,        // личная лента «генератор»
    msgs: [],            // все сообщения ленты
    ws: "",              // "" | "photo" | "video"
    photo: { model: "", aspect: "", resolution: "", variants: 1, files: [] },
    video: { model: "", aspect: "", resolution: "", duration: 0, ref: null },
    poll: null,
    sel: null,           // текущее большое превью {url, kind}
    d3: { files: [], kind: "3d", views: "full", layout: "row", busy: false,
          url: "", err: "", info: null, n: 4, frame: 0, sheet: false,
          vbusy: "", vurl: "", trends: null },
    blog: { chars: null },
  };

  const models = (kind) => (S.meta?.models || []).filter((m) => m.kind === kind);
  const model = (id) => (S.meta?.models || []).find((m) => m.id === id);

  async function ensureData() {
    if (!S.meta) S.meta = await api("/api/chat/models");
    if (!S.chatId) {
      const d = await api("/api/chats");
      const mine = (d.chats || []).find((c) => (c.title || "") === "генератор");
      S.chatId = mine ? mine.id : (await api("/api/chats", { method: "POST", body: { title: "генератор" } })).id;
    }
    const pick = (kind, want) => {
      const list = models(kind);
      const rich = (x) => x.allowed && x.live && (x.aspects || []).length;
      const hit = list.find((x) => x.id === want);
      // Модель с настройками формата и качества важнее «голой» дефолтной:
      // человек должен видеть контролы, а не гадать, куда они делись.
      if (hit && rich(hit)) return hit.id;
      return (list.find(rich) || hit || list.find((x) => x.allowed && x.live) || list[0] || {}).id || "";
    };
    if (!model(S.photo.model)) S.photo.model = pick("image", S.meta.default_image);
    if (!model(S.video.model)) S.video.model = pick("video", S.meta.default_video);
    syncCaps("photo"); syncCaps("video");
    await loadMsgs();
  }

  function syncCaps(ws) {
    const st = S[ws]; const m = model(st.model); if (!m) return;
    if (!(m.aspects || []).includes(st.aspect)) st.aspect = (m.aspects || [])[0] || "";
    if (!(m.resolutions || []).includes(st.resolution)) st.resolution = (m.resolutions || [])[0] || "";
    const durs = m.durations || S.meta.durations || [];
    if (ws === "video" && !durs.includes(st.duration)) st.duration = durs[0] || 0;
    const maxV = Math.max(1, Math.min(S.meta.max_variants || 1, 4));
    if (st.variants > maxV) st.variants = maxV;
  }

  async function loadMsgs() {
    const d = await api(`/api/chats/${S.chatId}/messages`);
    S.msgs = d.messages || d.items || [];
    const busy = S.msgs.some((m) => m.role !== "user" && m.status && m.status !== "done" && m.status !== "error");
    if (busy && !S.poll) S.poll = setInterval(async () => {
      try { await loadMsgs(); render(); } catch (e) {}
    }, 5000);
    if (!busy && S.poll) { clearInterval(S.poll); S.poll = null; }
  }

  const cost = (ws) => {
    const st = S[ws]; const m = model(st.model); if (!m) return 0;
    let p = m.points || 0;
    if (ws === "video" && m.points_by_duration && m.points_by_duration[st.duration] != null) p = m.points_by_duration[st.duration];
    if (ws === "photo" && m.points_by_resolution && m.points_by_resolution[st.resolution] != null) p = m.points_by_resolution[st.resolution];
    return p * (ws === "photo" ? (st.variants || 1) : 1);
  };

  /* ─────────── страница ─────────── */
  function openGenerator() {
    const app = $("#app"); if (!app) return;
    if (typeof window.qlolLeaveMake === "function" && $("#chat") && !$("#chat").classList.contains("hidden")) window.qlolLeaveMake();
    if (window.QlolMusic && window.QlolMusic.leave && $("#music") && !$("#music").classList.contains("hidden")) window.QlolMusic.leave();
    $("#trends-page")?.remove();
    let page = $("#generator-page");
    if (!page) {
      page = document.createElement("main");
      page.id = "generator-page";
      app.appendChild(page);
    }
    app.classList.remove("hidden");
    document.body.classList.add("gen-open");
    app.classList.add("trends-view"); // тот же механизм «страницы поверх студии»
    history.pushState({}, "", "/generator");
    page.innerHTML = `<p class="muted" style="text-align:center;padding:60px 0">${t("загружаю…", "loading…")}</p>`;
    ensureData().then(render).catch((e) => {
      page.innerHTML = `<p class="muted" style="text-align:center;padding:60px 0">${esc(e.message)}</p>`;
    });
  }
  window.qlolOpenGenerator = openGenerator;

  function closeGenerator() {
    const page = $("#generator-page"); if (!page) return;
    if (S.poll) { clearInterval(S.poll); S.poll = null; }
    page.remove();
    if (location.pathname === "/generator") history.replaceState({}, "", "/studio");
    document.body.classList.remove("gen-open");
    $("#app")?.classList.remove("trends-view");
  }
  // Уход в другой раздел: чужие кнопки ленты просто убирают нашу страницу.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest(".tb-sec");
    if (b && b.id !== "chat-btn" && $("#generator-page")) closeGenerator();
  }, true);

  function render() {
    const page = $("#generator-page"); if (!page) return;
    if (S.ws === "photo") return renderPhoto(page);
    if (S.ws === "video") return renderVideo(page);
    if (S.ws === "model3d") return render3d(page);
    if (S.ws === "blogger") return renderBlogger(page);
    renderEntry(page);
  }

  /* Карточки «Клипы» и «Предметы» не дублируют студию — они В НЕЁ ведут:
     закрываем генератор и включаем нужный режим тем же путём, что тумблер. */
  function goStudioMode(modeId) {
    closeGenerator();
    if (window.QlolSections && window.QlolSections.go) window.QlolSections.go("studio");
    setTimeout(() => {
      if (window.QlolModeMenu && window.QlolModeMenu.pick) window.QlolModeMenu.pick(modeId);
    }, 350);
  }

  /* ─────────── 3D-моделька с твоим лицом: РАБОЧИЙ флоу ───────────
     Та же механика, что разворот в досье персонажа (generate-model,
     kind=model): создаём персонажа, грузим фото, просим лист ракурсов.
     Результат уже СОХРАНЁН в персонажах — «сохранить» не нужен. */
  function render3d(page) {
    page.className = "gen-page gen-3d-view";
    const d = S.d3;
    if (!d.info) {
      api("/api/model-sheet").then((r) => { d.info = r; render(); }).catch(() => {});
    }
    if (d.trends === null) {
      d.trends = false;
      api("/api/trends").then((r) => {
        d.trends = (r.presets || []).filter((x) => x.poster_url || x.sample_url).slice(0, 12);
        render();
      }).catch(() => { d.trends = []; });
    }
    const info = d.info || {};
    const kinds = [["3d", t("3D-рендер", "3D render")], ["real", t("фото", "photo")],
                   ["anime", t("аниме", "anime")]];
    const seg = (cls, items, cur) => `<div class="gen-3d-kinds ${cls}">${items.map(([k, cap]) =>
      `<button type="button" data-k="${k}" class="${cur === k ? "on" : ""}">${cap}</button>`).join("")}</div>`;
    page.innerHTML = `
      <header class="gen-head"><h1>${t("генератор · 3d-модель", "generator · 3d model")}</h1>
        <button type="button" class="gen-back ghosty">← ${t("к выбору", "back")}</button></header>
      <section class="gen-3d">
        <div class="gen-3d-left">
          <label class="gen-drop ${d.files.length ? "has" : ""}">
            ${d.files.length
              ? d.files.map((f) => `<img src="${URL.createObjectURL(f)}" alt=""/>`).join("")
              : `<span class="gen-drop-plus">＋</span>
                 <b>${t("загрузи 1–4 своих фото", "upload 1–4 photos of yourself")}</b>
                 <small>${t("анфас и три четверти работают лучше всего", "front and three-quarter shots work best")}</small>`}
            <input type="file" accept="image/*" multiple hidden />
          </label>
          <div class="gen-3d-row"><small>${t("стиль", "style")}</small>${seg("k-kind", kinds, d.kind)}</div>
          <div class="gen-3d-row"><small>${t("ракурсы", "views")}</small>${seg("k-views",
            (info.views || [{ id: "full", n: 4 }]).map((v) => [v.id, String(v.n)]), d.views)}</div>
          <div class="gen-3d-row"><small>${t("раскладка", "layout")}</small>${seg("k-layout",
            [["row", t("лента", "strip")], ["grid", t("сетка", "grid")]], d.layout)}</div>
          ${info.engine_title ? `<div class="gen-3d-row"><small>${t("движок", "engine")}</small>
            <span class="gen-3d-eng">${esc(info.engine_title)}</span></div>` : ""}
          <button type="button" class="gen-go gen-3d-go" ${d.files.length && !d.busy ? "" : "disabled"}>
            ${d.busy ? t("делаю модельку…", "building the model…")
                     : t("сделать 3D-модель", "build the 3D model")
                       + (info.cost ? ` <span>⚡ ${info.cost}</span>` : "")}</button>
          <div class="gen-note ${d.err ? "" : "hidden"}">${esc(d.err)}</div>
          ${(info.history || []).length ? `
            <div class="gen-3d-hist"><small>${t("мои модельки", "my models")}</small>
              <div class="gen-3d-hist-grid">${info.history.map((h) =>
                `<button type="button" data-h="${esc(h.url)}"><img src="${esc(h.url)}" alt="" loading="lazy"/></button>`).join("")}
              </div></div>` : ""}
        </div>
        <div class="gen-3d-stage">
          ${d.busy ? `<div class="skel" style="width:100%;min-height:260px"></div>
              <b>${t("генерирую разворот — до минуты", "generating the turnaround — up to a minute")}</b>`
            : d.url ? `
              <div class="gen-3d-spin3d ${d.sheet ? "hidden" : ""}" data-n="${d.n}">
                <canvas></canvas>
                <span class="gen-3d-draghint">⟲ ${t("тяни, чтобы крутить", "drag to spin")}</span>
              </div>
              <img class="gen-3d-sheetimg ${d.sheet ? "" : "hidden"}" src="${esc(d.url)}" alt=""/>
              <div class="gen-3d-acts">
                <button type="button" class="gen-3d-flip">${d.sheet
                  ? t("вертушка", "spin view") : t("показать лист", "show the sheet")}</button>
                <a href="${esc(d.url)}" download>⬇ ${t("скачать", "download")}</a>
                <span class="gen-3d-saved">✓ ${t("сохранено в персонажах", "saved to characters")}</span>
                <button type="button" class="gen-3d-again">${t("сделать ещё", "make another")}</button>
              </div>
              <div class="gen-3d-video">
                ${d.vurl ? `<video src="${esc(d.vurl)}" controls autoplay loop muted playsinline></video>` : ""}
                ${d.vbusy ? `<div class="gen-3d-vbusy"><span class="gen-spin"></span> ${esc(d.vbusy)}</div>` : ""}
                <div class="gen-3d-vrow">
                  <button type="button" class="gen-go gen-3d-orbit" ${d.vbusy ? "disabled" : ""}>
                    ${t("видео-облёт в один клик", "one-click orbit video")} <span>⚡ ${videoCost3d()}</span></button>
                </div>
                ${(d.trends || []).length ? `
                  <small class="gen-3d-vcap">${t("или ролик по тренду с твоей моделькой:", "or a trend video with your model:")}</small>
                  <div class="gen-3d-trends">${d.trends.map((x) => `
                    <button type="button" data-trend="${x.id}" title="${esc(x.title)}" ${d.vbusy ? "disabled" : ""}>
                      ${x.sample_url ? `<video src="${esc(x.sample_url)}" muted loop playsinline preload="metadata"></video>`
                        : `<img src="${esc(x.poster_url)}" alt="" loading="lazy"/>`}
                      <i>${esc(x.title)}</i>
                    </button>`).join("")}</div>` : ""}
              </div>`
            : `<b>${t("здесь появится твой разворот", "your turnaround appears here")}</b>
               <p class="gen-3d-hint">${t("лист ракурсов соберётся в вертушку, а моделька станет персонажем для клипов",
                 "the sheet turns into a spinner and the model becomes a clip character")}</p>`}
        </div>
      </section>`;
    $(".gen-back", page).addEventListener("click", () => { S.ws = ""; render(); });
    $(".gen-drop input", page).addEventListener("change", (e) => {
      d.files = Array.from(e.target.files || []).slice(0, 4);
      d.url = ""; d.err = ""; d.vurl = "";
      render();
    });
    $$(".k-kind button", page).forEach((b) => b.addEventListener("click", () => { d.kind = b.dataset.k; render(); }));
    $$(".k-views button", page).forEach((b) => b.addEventListener("click", () => { d.views = b.dataset.k; render(); }));
    $$(".k-layout button", page).forEach((b) => b.addEventListener("click", () => { d.layout = b.dataset.k; render(); }));
    $$(".gen-3d-hist-grid button", page).forEach((b) => b.addEventListener("click", () => {
      d.url = b.dataset.h; d.sheet = false; d.vurl = ""; d.err = "";
      d.n = (d.info.views.find((v) => v.id === d.views) || { n: 4 }).n;
      render();
    }));
    $(".gen-3d-flip", page)?.addEventListener("click", () => { d.sheet = !d.sheet; render(); });
    $(".gen-3d-again", page)?.addEventListener("click", () => { d.files = []; d.url = ""; d.vurl = ""; render(); });
    $(".gen-3d-go", page)?.addEventListener("click", () => make3d());
    $(".gen-3d-orbit", page)?.addEventListener("click", () => video3d(null));
    $$(".gen-3d-trends button", page).forEach((b) =>
      b.addEventListener("click", () => video3d(parseInt(b.dataset.trend, 10))));
    if (d.url && !d.sheet) mountSpin3d(page);
  }

  const videoCost3d = () => {
    const m = model(S.video.model) || {};
    return m.points_by_duration?.[S.video.duration] ?? m.points ?? 0;
  };

  async function make3d() {
    const d = S.d3;
    if (!d.files.length || d.busy) return;
    d.busy = true; d.err = ""; d.vurl = ""; render();
    try {
      const ch = await api("/api/characters", { method: "POST",
        body: { name: t("Моя 3D-модель", "My 3D model") } });
      for (const f of d.files) {
        const fd = new FormData(); fd.append("photo", f);
        await api(`/api/characters/${ch.id}/photos`, { method: "POST", body: fd });
      }
      const res = await api(`/api/characters/${ch.id}/generate-model`,
        { method: "POST", body: { kind: d.kind, views: d.views, layout: d.layout } });
      const m = (res.photos || []).filter((p) => p.kind === "model").pop();
      d.url = m ? m.url : "";
      d.n = (d.info?.views || []).find((v) => v.id === d.views)?.n || 4;
      d.sheet = false;
      if (!d.url) d.err = t("модель не вернула лист — попробуй ещё раз", "no sheet returned — try again");
      else if (d.info) { d.info.history.unshift({ id: 0, url: d.url, char_id: ch.id }); }
    } catch (e) { d.err = e.message; }
    d.busy = false; render();
  }

  /* Вертушка: лист режется canvas'ом на N кадров, драг листает их как
     вращение. Сетка 2 ряда режется по двум строкам. */
  function mountSpin3d(page) {
    const d = S.d3;
    const box = $(".gen-3d-spin3d", page);
    const cv = $("canvas", box);
    const img = new Image();
    img.onload = () => {
      const n = d.n || 4;
      const rows = d.layout === "grid" ? 2 : 1;
      const cols = Math.ceil(n / rows);
      const fw = Math.floor(img.naturalWidth / cols);
      const fh = Math.floor(img.naturalHeight / rows);
      cv.width = fw; cv.height = fh;
      const draw = () => {
        const k = ((d.frame % n) + n) % n;
        cv.getContext("2d").drawImage(img,
          (k % cols) * fw, Math.floor(k / cols) * fh, fw, fh, 0, 0, fw, fh);
      };
      draw();
      let sx = null, sf = 0;
      const move = (x) => { d.frame = sf + Math.round((x - sx) / 45); draw(); };
      box.onpointerdown = (e) => { sx = e.clientX; sf = d.frame; box.setPointerCapture(e.pointerId); };
      box.onpointermove = (e) => { if (sx !== null) move(e.clientX); };
      box.onpointerup = () => { sx = null; };
    };
    img.src = d.url;
  }

  /* Кадр вертушки → jpeg-блоб: он и реф видео, и вход трендов. */
  async function frameBlob3d() {
    const d = S.d3;
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const n = d.n || 4, rows = d.layout === "grid" ? 2 : 1;
        const cols = Math.ceil(n / rows);
        const fw = Math.floor(img.naturalWidth / cols), fh = Math.floor(img.naturalHeight / rows);
        const cv = document.createElement("canvas");
        cv.width = fw; cv.height = fh;
        cv.getContext("2d").drawImage(img, 0, 0, fw, fh, 0, 0, fw, fh);
        cv.toBlob((b) => (b ? resolve(b) : reject(new Error("crop failed"))), "image/jpeg", .92);
      };
      img.onerror = () => reject(new Error("sheet not readable"));
      img.src = d.url;
    });
  }

  async function video3d(trendId) {
    const d = S.d3;
    if (d.vbusy) return;
    d.vbusy = t("готовлю кадр…", "preparing the frame…"); d.vurl = ""; render();
    try {
      const blob = await frameBlob3d();
      if (trendId) {
        const fd = new FormData();
        fd.append("photo", new File([blob], "model.jpg", { type: "image/jpeg" }));
        const job = await api(`/api/trends/${trendId}/make`, { method: "POST", body: fd });
        d.vbusy = t("снимаю ролик по тренду…", "shooting the trend video…"); render();
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 5000));
          const st = await api(`/api/trends/jobs/${job.job_id}`);
          if (st.status === "error") throw new Error(st.error || "trend failed");
          if (st.video_url) { d.vurl = st.video_url; break; }
        }
      } else {
        const fd = new FormData(); fd.append("file", blob, "model.jpg");
        const up = await api("/api/chat/upload", { method: "POST", body: fd });
        const beforeIds = new Set(S.msgs.filter((m) => m.kind === "video" && m.url).map((m) => m.id));
        await api(`/api/chats/${S.chatId}/messages`, { method: "POST", body: {
          engine: S.video.model,
          text: "Slow smooth turntable rotation: the character stays in place "
              + "and the camera orbits a full circle around them. Neutral grey "
              + "studio background, even light, no zoom.",
          aspect: "9:16", duration: S.video.duration, file_ids: [up.id],
        } });
        d.vbusy = t("снимаю облёт…", "shooting the orbit…"); render();
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 5000));
          await loadMsgs();
          const vid = S.msgs.filter((m) => m.role !== "user" && m.kind === "video"
            && m.url && !beforeIds.has(m.id)).pop();
          const busyOne = S.msgs.some((m) => m.role !== "user" && m.status
            && m.status !== "done" && m.status !== "error");
          if (vid && !busyOne) { d.vurl = vid.url; break; }
          if (!busyOne && !vid && i > 2) throw new Error(t("видео не вышло — токены возвращены", "video failed — tokens refunded"));
        }
      }
      if (!d.vurl) throw new Error(t("не дождался видео — загляни в супергенератор позже", "timed out — check the supergenerator later"));
    } catch (e) { d.err = e.message; }
    d.vbusy = ""; render();
  }

  /* ─────────── ИИ-блогеры: выбор ведущего → съёмка ───────────
     Не дублируем UGC-конвейер — выбираем/создаём блогера и уводим в него. */
  function renderBlogger(page) {
    page.className = "gen-page gen-blog-view";
    const chars = S.blog.chars;
    page.innerHTML = `
      <header class="gen-head"><h1>${t("генератор · ИИ-блогеры", "generator · AI bloggers")}</h1>
        <button type="button" class="gen-back ghosty">← ${t("к выбору", "back")}</button></header>
      <section class="gen-blog">
        <p class="gen-blog-lead">${t("блогер — это персонаж: лицо держится во всех роликах. выбери своего или заведи нового — и снимай обзоры, распаковки и говорящую голову.",
          "a blogger is a character: the face holds across videos. pick yours or create one — then shoot reviews, unboxings and talking heads.")}</p>
        <div class="gen-blog-grid">
          ${chars === null ? `<span class="gen-spin"></span>`
            : (chars || []).map((c) => `
              <button type="button" class="gen-blog-card" data-id="${c.id}">
                ${c.photo_url ? `<img src="${esc(c.photo_url)}" alt=""/>` : `<span class="gen-blog-ph">👤</span>`}
                <b>${esc(c.name || "—")}</b>
              </button>`).join("")}
          <button type="button" class="gen-blog-card gen-blog-new">
            <span class="gen-blog-ph">＋</span><b>${t("новый блогер", "new blogger")}</b>
          </button>
        </div>
      </section>`;
    $(".gen-back", page).addEventListener("click", () => { S.ws = ""; render(); });
    if (chars === null) {
      api("/api/characters/all").then((r) => { S.blog.chars = r.characters || []; render(); })
        .catch(() => { S.blog.chars = []; render(); });
    }
    $$(".gen-blog-card", page).forEach((b) => b.addEventListener("click", () => {
      // И существующий, и новый ведут в UGC-режим студии: там персона,
      // формат и весь конвейер ролика. Дублировать его здесь нельзя.
      goStudioMode("ugc");
    }));
  }

  /* ─────────── экран выбора ───────────
     Карточки КОМПАКТНЫЕ и с ЖИВЫМИ примерами: кадры стилей, витринные
     мокап-шаблоны, персонажи. Абстрактные цветные квадраты — то, что
     владелец забраковал. */
  function renderEntry(page) {
    page.className = "gen-page gen-entry-view";
    const img = (src) => `<img src="${src}" alt="" loading="lazy"/>`;
    page.innerHTML = `<section class="gen-entry">
      <h1>${t("что создаём?", "what are we creating?")}</h1>
      <div class="gen-entry-cards">
        <button type="button" class="gen-entry-card" data-go="model3d">
          <span class="gen-entry-art">${img("/img/shots/feat-chars.jpg")}</span>
          <b>${t("3D модель персонажа", "3D character model")}</b>
          <small>${t("загрузи фото — получи разворот со своим лицом", "upload a photo — get a turnaround with your face")}</small>
        </button>
        <button type="button" class="gen-entry-card" data-go="mockup">
          <span class="gen-entry-art gen-art-mk" data-mk-live>${img("/img/shots/frame-4.jpg")}</span>
          <b>${t("предметы", "products")}</b>
          <small>${t("мокапы: съёмка товара по одному фото", "mockups: product shots from one photo")}</small>
        </button>
        <button type="button" class="gen-entry-card" data-go="clip">
          <span class="gen-entry-art">${img("/img/shots/clip.jpg")}</span>
          <b>${t("клипы", "clips")}</b>
          <small>${t("клип под свой трек: раскадровка и сборка", "a clip for your track: storyboard and assembly")}</small>
        </button>
        <button type="button" class="gen-entry-card" data-go="blogger">
          <span class="gen-entry-art">${img("/img/shots/frame-5.jpg")}</span>
          <b>${t("ИИ-блогеры", "AI bloggers")}</b>
          <small>${t("сквозной ведущий: обзоры и распаковки", "a recurring host: reviews and unboxings")}</small>
        </button>
        <button type="button" class="gen-entry-card gen-card-super" data-go="photo">
          <span class="gen-entry-art gen-art-super">
            ${img("/img/shots/frame-1.jpg")}${img("/img/shots/frame-2.jpg")}${img("/img/shots/frame-3.jpg")}
          </span>
          <b>${t("супергенератор", "supergenerator")}</b>
          <small>${t("свободные фото и видео любой моделью", "free-form photo and video with any model")}</small>
        </button>
      </div>
    </section>`;
    $$(".gen-entry-card", page).forEach((c) => c.addEventListener("click", () => {
      const go = c.dataset.go;
      if (go === "mockup" || go === "clip") return goStudioMode(go);
      S.ws = go === "photo" ? "photo" : go;
      render();
    }));
    // Живые превью мокапов: первые витринные шаблоны вместо статики.
    // Меняем ТОЛЬКО после реальной загрузки первой картинки: /api/media
    // прячет чужие файлы от гостя, и без проверки карточка оставалась бы
    // с тремя битыми <img> вместо запасного кадра.
    fetch("/api/mockup/templates", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const rows = ((d && d.templates) || []).filter((x) => x.preview_url).slice(0, 3);
        const holder = $("[data-mk-live]", page);
        if (!holder || !rows.length) return;
        const probe = new Image();
        probe.onload = () => {
          holder.classList.add("gen-art-super");
          holder.innerHTML = rows.map((x) => img(x.preview_url)).join("");
        };
        probe.src = rows[0].preview_url;
      }).catch(() => {});
  }

  const switcher = () => `<div class="gen-switch">
      <button type="button" data-ws="photo" class="${S.ws === "photo" ? "on" : ""}">${t("фото", "photo")}</button>
      <button type="button" data-ws="video" class="${S.ws === "video" ? "on" : ""}">${t("видео", "video")}</button>
    </div>`;
  function bindSwitch(page) {
    $$(".gen-switch button", page).forEach((b) =>
      b.addEventListener("click", () => { S.ws = b.dataset.ws; render(); }));
  }

  /* чипы-селекторы: показываем ТОЛЬКО то, что модель умеет */
  function chip(label, value, opts, onpick) {
    if (!opts || !opts.length) return "";
    if (opts.length < 2) return `<div class="gen-chip gen-chip-static"><span class="gen-chip-btn">${esc(String(value))}</span></div>`;
    return `<div class="gen-chip" data-chip="${label}">
      <button type="button" class="gen-chip-btn">${esc(String(value))}<span>▾</span></button>
      <div class="gen-chip-menu hidden">${opts.map((o) =>
        `<button type="button" data-v="${esc(String(o.v ?? o))}" class="${String(o.v ?? o) === String(value) ? "on" : ""}">${esc(String(o.label ?? o))}</button>`).join("")}</div>
    </div>`;
  }
  function bindChips(root, handlers) {
    $$(".gen-chip", root).forEach((c) => {
      const menu = $(".gen-chip-menu", c);
      $(".gen-chip-btn", c).addEventListener("click", (e) => {
        e.stopPropagation();
        $$(".gen-chip-menu", root).forEach((m) => { if (m !== menu) m.classList.add("hidden"); });
        menu.classList.toggle("hidden");
      });
      $$("button[data-v]", menu).forEach((b) => b.addEventListener("click", () => {
        handlers[c.dataset.chip]?.(b.dataset.v); render();
      }));
    });
    document.addEventListener("click", () => $$(".gen-chip-menu", root).forEach((m) => m.classList.add("hidden")), { once: true });
  }

  function modelChip(ws) {
    const st = S[ws]; const m = model(st.model);
    const list = models(ws === "photo" ? "image" : "video");
    return `<div class="gen-chip gen-chip-model" data-chip="model">
      <button type="button" class="gen-chip-btn">${esc(m ? m.title : "auto")}<span>▾</span></button>
      <div class="gen-chip-menu gen-model-menu hidden">
        <button type="button" data-v="${esc(ws === "photo" ? S.meta.default_image : S.meta.default_video)}">
          <b>auto</b><small>${t("система сама выберет подходящую модель", "the system picks a model for you")}</small></button>
        ${list.map((x) => `<button type="button" data-v="${esc(x.id)}" class="${x.id === st.model ? "on" : ""}" ${x.allowed ? "" : "disabled"}>
          <b>${esc(x.title)}</b><small>${x.allowed ? `✦ ${x.points}` : "🔒 " + t("в тарифе выше", "higher plan")}${x.live ? "" : " · " + t("офлайн", "offline")}</small>
        </button>`).join("")}
      </div>
    </div>`;
  }

  /* ─────────── ФОТО ─────────── */
  function renderPhoto(page) {
    page.className = "gen-page gen-photo-view";
    const done = S.msgs.filter((m) => m.role !== "user" && m.kind === "image");
    const st = S.photo; const m = model(st.model) || {};
    page.innerHTML = `
      <header class="gen-head"><h1>${t("генератор · фото", "generator · photo")}</h1>${switcher()}</header>
      <section class="gen-gallery">${done.length ? done.slice().reverse().map(card).join("")
        : `<div class="gen-empty">${t("здесь появятся твои изображения — опиши первое в панели снизу", "your images will appear here — describe the first one below")}</div>`}
      </section>
      <footer class="gen-composer">
        <div class="gen-refs">${st.files.map((f) =>
          `<span class="gen-ref"><img src="${f.thumb_url || f.url}" alt=""/><button data-rm="${f.id}">×</button></span>`).join("")}</div>
        <div class="gen-composer-row gen-composer-top">
          <label class="gen-attach" title="${t("референсы", "references")}">＋<input type="file" accept="image/*" multiple hidden /></label>
          <input class="gen-prompt" type="text" maxlength="${S.meta.prompt_limit || 2000}"
            placeholder="${t("опиши изображение, которое хочешь создать…", "describe the image you want to create…")}" />
        </div>
        <div class="gen-composer-row gen-composer-bottom">
          ${modelChip("photo")}
          ${chip("aspect", st.aspect, m.aspects || [])}
          ${chip("resolution", st.resolution, m.resolutions || [])}
          <div class="gen-count"><button type="button" data-d="-1">−</button><span>${st.variants}</span><button type="button" data-d="1">＋</button></div>
          <span class="gen-flex"></span>
          <button type="button" class="gen-go">${t("сгенерировать", "generate")} <span>✦ ${cost("photo")}</span></button>
        </div>
        <div class="gen-note hidden"></div>
      </footer>`;
    bindSwitch(page);
    bindChips(page, {
      model: (v) => { st.model = v; syncCaps("photo"); },
      aspect: (v) => { st.aspect = v; },
      resolution: (v) => { st.resolution = v; syncCaps("photo"); },
      variants: (v) => { st.variants = parseInt(v, 10) || 1; },
    });
    bindGallery(page);
    $(".gen-attach input", page).addEventListener("change", (e) => uploadRefs(e.target.files, st, page));
    $$(".gen-ref button", page).forEach((b) => b.addEventListener("click", () => {
      st.files = st.files.filter((f) => String(f.id) !== b.dataset.rm); render();
    }));
    $$(".gen-count button", page).forEach((b) => b.addEventListener("click", () => {
      const maxV = Math.min(4, S.meta.max_variants || 4);
      st.variants = Math.max(1, Math.min(maxV, (st.variants || 1) + parseInt(b.dataset.d, 10)));
      render();
    }));
    $(".gen-go", page).addEventListener("click", () => sendPhoto(page));
    $(".gen-prompt", page).addEventListener("keydown", (e) => { if (e.key === "Enter") sendPhoto(page); });
  }

  function card(msg) {
    const busy = msg.status && msg.status !== "done" && msg.status !== "error";
    if (busy) return `<div class="gen-card gen-busy"><span class="gen-spin"></span><small>${t("генерирую…", "generating…")}</small></div>`;
    if (msg.status === "error") return `<div class="gen-card gen-err" title="${esc(msg.error)}">⚠ ${t("не получилось — токены возвращены", "failed — tokens refunded")}</div>`;
    if (!msg.url) return "";
    return msg.kind === "video"
      ? `<div class="gen-card" data-url="${esc(msg.url)}" data-kind="video"><video src="${msg.url}" muted loop playsinline preload="metadata"></video></div>`
      : `<div class="gen-card" data-url="${esc(msg.url)}" data-kind="image"><img src="${msg.thumb_url || msg.url}" loading="lazy" alt=""/></div>`;
  }
  function bindGallery(page) {
    $$(".gen-card[data-url]", page).forEach((c) => {
      const v = $("video", c);
      if (v) { c.addEventListener("mouseenter", () => v.play().catch(() => {})); c.addEventListener("mouseleave", () => v.pause()); }
      c.addEventListener("click", () => showBig(c.dataset.url, c.dataset.kind));
    });
  }
  function showBig(url, kind) {
    const w = document.createElement("div");
    w.className = "gen-big";
    w.innerHTML = `<div class="gen-big-body">${kind === "video"
      ? `<video src="${esc(url)}" controls autoplay loop playsinline></video>`
      : `<img src="${esc(url)}" alt=""/>`}
      <div class="gen-big-actions"><a href="${esc(url)}" download>⬇ ${t("скачать", "download")}</a>
      <button type="button" class="gen-big-close">✕</button></div></div>`;
    w.addEventListener("click", (e) => { if (e.target === w || e.target.closest(".gen-big-close")) w.remove(); });
    document.body.appendChild(w);
  }

  async function uploadRefs(files, st, page) {
    const note = $(".gen-note", page);
    for (const f of Array.from(files).slice(0, S.meta.max_files || 4)) {
      try {
        const fd = new FormData(); fd.append("file", f);
        const r = await api("/api/chat/upload", { method: "POST", body: fd });
        (st.files || (st.files = [])).push(r);
        if (st === S.video) st.ref = r;
      } catch (e) {
        note.textContent = t("файл не загрузился: ", "upload failed: ") + e.message;
        note.classList.remove("hidden");
      }
    }
    render();
  }

  async function sendPhoto(page) {
    const st = S.photo;
    const text = $(".gen-prompt", page).value.trim();
    const note = $(".gen-note", page);
    if (!text) { note.textContent = t("опиши, что нарисовать", "describe what to draw"); note.classList.remove("hidden"); return; }
    try {
      await api(`/api/chats/${S.chatId}/messages`, { method: "POST", body: {
        engine: st.model, text, aspect: st.aspect, resolution: st.resolution,
        variants: st.variants, file_ids: st.files.map((f) => f.id),
      } });
      st.files = [];
      await loadMsgs(); render();
    } catch (e) { note.textContent = e.message; note.classList.remove("hidden"); }
  }

  /* ─────────── ВИДЕО ─────────── */
  function renderVideo(page) {
    page.className = "gen-page gen-video-view";
    const vids = S.msgs.filter((m) => m.role !== "user" && m.kind === "video");
    const latest = vids[vids.length - 1];
    const st = S.video; const m = model(st.model) || {};
    const durs = m.durations || S.meta.durations || [];
    page.innerHTML = `
      <header class="gen-head"><h1>${t("генератор · видео", "generator · video")}</h1>${switcher()}</header>
      <div class="gen-studio">
        <aside class="gen-vpanel">
          <div class="gen-vmode">${t("создать видео", "create a video")}</div>
          <label class="gen-vref">
            ${st.ref ? `<img src="${st.ref.thumb_url || st.ref.url}" alt=""/><span class="gen-vref-swap">✎</span>`
              : `<span class="gen-vref-plus">＋</span><b>${t("добавить референс", "add a reference")}</b><small>jpg · png · webp</small>`}
            <input type="file" accept="image/*" hidden />
          </label>
          ${st.ref ? `<button type="button" class="gen-vref-rm">${t("убрать референс", "remove reference")}</button>` : ""}
          <textarea class="gen-vprompt" maxlength="${S.meta.prompt_limit || 2000}"
            placeholder="${t("опиши, что должно происходить в видео…", "describe what should happen in the video…")}"></textarea>
          <div class="gen-vrow"><small>${t("модель", "model")}</small>${modelChip("video")}</div>
          <div class="gen-vrow">${durs.length > 1 ? `<small>${t("длительность", "duration")}</small>` + chip("duration", st.duration + "s", durs.map((d) => ({ v: d, label: d + "s" }))) : ""}</div>
          <div class="gen-vrow">${(m.aspects || []).length > 1 ? `<small>${t("формат", "aspect")}</small>` + chip("aspect", st.aspect, m.aspects) : ""}</div>
          <div class="gen-vrow">${(m.resolutions || []).length > 1 ? `<small>${t("качество", "quality")}</small>` + chip("resolution", st.resolution, m.resolutions) : ""}</div>
          <button type="button" class="gen-go gen-vgo">${t("сгенерировать", "generate")} <span>✦ ${cost("video")}</span></button>
          <div class="gen-note hidden"></div>
        </aside>
        <main class="gen-vstage">
          ${latest && latest.url ? `<video src="${latest.url}" controls loop playsinline></video>`
            : latest && latest.status && latest.status !== "error" && latest.status !== "done"
              ? `<div class="gen-vempty"><span class="gen-spin"></span><b>${t("видео генерируется…", "generating the video…")}</b></div>`
              : `<div class="gen-vempty"><b>${t("здесь появится твоё видео", "your video will appear here")}</b><small>${t("загрузи фото, опиши движение и нажми «сгенерировать»", "upload a photo, describe the motion and hit generate")}</small></div>`}
        </main>
        <aside class="gen-vhistory">
          <small>${t("история", "history")}</small>
          ${vids.length ? vids.slice().reverse().map(card).join("") : `<div class="gen-vempty-mini">${t("прошлые генерации будут здесь", "past generations live here")}</div>`}
        </aside>
      </div>`;
    bindSwitch(page);
    bindChips(page, {
      model: (v) => { st.model = v; syncCaps("video"); },
      duration: (v) => { st.duration = parseInt(v, 10) || 0; },
      aspect: (v) => { st.aspect = v; },
      resolution: (v) => { st.resolution = v; },
    });
    bindGallery(page);
    $(".gen-vref input", page).addEventListener("change", (e) => uploadRefs(e.target.files, st, page));
    $(".gen-vref-rm", page)?.addEventListener("click", () => { st.ref = null; render(); });
    $(".gen-vgo", page).addEventListener("click", async () => {
      const note = $(".gen-note", page);
      const text = $(".gen-vprompt", page).value.trim();
      const mm = model(st.model) || {};
      if (mm.needs_image && !st.ref) { note.textContent = t("этой модели нужен кадр: загрузи фото-референс", "this model needs a frame: upload a photo"); note.classList.remove("hidden"); return; }
      try {
        await api(`/api/chats/${S.chatId}/messages`, { method: "POST", body: {
          engine: st.model, text, aspect: st.aspect, duration: st.duration,
          file_ids: st.ref ? [st.ref.id] : [],
        } });
        st.ref = null;
        await loadMsgs(); render();
      } catch (e) { note.textContent = e.message; note.classList.remove("hidden"); }
    });
  }

  /* ─────────── вход: перехват кнопки шапки ─────────── */
  // Перехват на фазе захвата ДОКУМЕНТА: срабатывает раньше любых
  // обработчиков самой кнопки, независимо от порядка загрузки скриптов.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("#chat-btn");
    if (!b) return;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    S.ws = ""; // кнопка шапки всегда ведёт на выбор «что создаём?»
    openGenerator();
  }, true);
  // Перерисовка генератора при смене языка — та же схема, что у трендов.
  if (typeof window.onLangChange === "function") {
    window.onLangChange(() => { if ($("#generator-page")) render(); });
  }
  // Корень «/» генератор НЕ забирает: там живут лендинг (гость, ?home) и
  // студия (вошедший) — их порядок решает app.js. Прямой вход — /generator.
  const boot = () => { if (location.pathname === "/generator") openGenerator(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(boot, 120));
  else setTimeout(boot, 120);
})();
