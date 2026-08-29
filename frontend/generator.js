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
    photo: { model: "", aspect: "", resolution: "", variants: 1, files: [], prompt: "", sel: null },
    video: { model: "", aspect: "", resolution: "", duration: 0, ref: null, prompt: "", sel: null },
    poll: null,
    sel: null,           // текущее большое превью {url, kind}
    d3: { files: [], kind: "3d", views: "full", layout: "row", busy: false,
          url: "", err: "", info: null, n: 4, frame: 0, sheet: false,
          vbusy: "", vurl: "", trends: null },
    // Предмет — тот же экран, что 3D-персонаж, но сущность — трек мокап-
    // проекта: чистая моделька + 8-ракурсный облёт (turnaround).
    it: { files: [], busy: "", err: "", items: null, sel: null, frame: 0,
          vbusy: "", vurl: "", projectId: 0 },
    blog: { chars: null, sel: null },
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
    // Выбор «что создаём?» не требует данных — рисуем мгновенно и гостю.
    // Модели и личная лента грузятся при входе в супергенератор (render).
    render();
    window.QlolSections?.paint(); // подсветка «Генератор» в общей шапке
  }
  window.qlolOpenGenerator = openGenerator;

  function closeGenerator() {
    const page = $("#generator-page"); if (!page) return;
    if (S.poll) { clearInterval(S.poll); S.poll = null; }
    page.remove();
    if (location.pathname === "/generator") history.replaceState({}, "", "/studio");
    document.body.classList.remove("gen-open");
    $("#app")?.classList.remove("trends-view");
    window.QlolSections?.paint();
  }
  // Уход в другой раздел: чужие кнопки ленты просто убирают нашу страницу.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest(".tb-sec");
    if (b && b.id !== "chat-btn" && $("#generator-page")) closeGenerator();
  }, true);

  function render() {
    const page = $("#generator-page"); if (!page) return;
    // Супергенератору нужны модели и личная лента — грузим ПРИ ВХОДЕ в него,
    // а не на открытии генератора: выбор и витрина персонажей открываются
    // мгновенно, гость видит экраны, а не «Unauthorized» вместо страницы.
    if ((S.ws === "photo" || S.ws === "video") && !S.meta) {
      page.className = "gen-page";
      page.innerHTML = `<p class="muted" style="text-align:center;padding:60px 0">${t("загружаю…", "loading…")}</p>`;
      ensureData().then(render).catch((e) => renderLocked(page, e));
      return;
    }
    if (S.ws === "photo") return renderPhoto(page);
    if (S.ws === "video") return renderVideo(page);
    if (S.ws === "model3d") return render3d(page);
    if (S.ws === "item3d") return renderItem(page);
    if (S.ws === "blogger") return renderChars(page);
    renderEntry(page);
  }

  /* Гость упирается в супергенератор 401-м. Это дверь, а не ошибка:
     говорим про вход человеческим текстом, огонь — на главном действии. */
  function renderLocked(page, e) {
    const auth = /авториз|unauthor|401/i.test(String((e && e.message) || ""));
    page.className = "gen-page gen-entry-view";
    page.innerHTML = `<section class="gen-entry gen-locked">
      <h1>${auth ? t("нужен вход", "sign in required") : esc((e && e.message) || "")}</h1>
      ${auth ? `<p class="gen-locked-sub">${t("войди в аккаунт — и генератор откроется", "sign in and the generator opens up")}</p>` : ""}
      <div class="gen-locked-row">
        ${auth ? `<button type="button" class="gen-go gen-login">${t("войти", "sign in")}</button>` : ""}
        <button type="button" class="gen-back ghosty">← ${t("к выбору", "back")}</button>
      </div></section>`;
    $(".gen-login", page)?.addEventListener("click", () => { closeGenerator(); if (typeof window.showLogin === "function") window.showLogin(); else location.hash = "#/login"; });
    $(".gen-back", page).addEventListener("click", () => { S.ws = ""; render(); });
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

  /* ═══════════ ЕДИНЫЙ МАКЕТ ВСЕХ ЭКРАНОВ ГЕНЕРАТОРА ═══════════
     Эталон — экран «видео»: слева панель настроек (референс, промпт,
     параметры, цена и огненная кнопка), в центре стейдж результата,
     справа история генераций. Один макет на фото, видео, персонажа,
     предмет и 3D — иначе каждый экран учат заново.
     История везде живёт НА СЕРВЕРЕ (лента «генератор», /api/model-sheet,
     /api/items/all, /api/characters/all), поэтому переживает перезагрузку. */
  function shell(page, o) {
    page.className = "gen-page gen-studio-view " + (o.cls || "");
    page.innerHTML = `
      <header class="gen-head"><h1>${o.title}</h1>
        <div class="gen-head-tools">${o.tools || ""}
          <button type="button" class="gen-back ghosty">← ${t("к выбору", "back")}</button></div></header>
      <div class="gen-studio">
        <aside class="gen-vpanel">${o.panel || ""}</aside>
        <main class="gen-vstage">${o.stage || ""}</main>
        <aside class="gen-vhistory">
          <small>${t("история", "history")}</small>
          ${o.history || `<div class="gen-vempty-mini">${t("прошлые генерации будут здесь", "past generations live here")}</div>`}
        </aside>
      </div>`;
    $(".gen-back", page).addEventListener("click", () => { S.ws = ""; render(); });
  }

  /* Карточка истории: миниатюра + подпись. data-h — индекс в своём списке. */
  const hcard = (i, o) => `<button type="button" class="gen-hcard ${o.on ? "on" : ""}" data-h="${i}"
      title="${esc(o.cap || "")}">
      ${o.url
        ? (o.kind === "video"
            ? `<video src="${esc(o.url)}" muted loop playsinline preload="metadata"></video>`
            : `<img src="${esc(o.url)}" alt="" loading="lazy"/>`)
        : `<span class="gen-hcard-ph">${o.ph || "•"}</span>`}
      ${o.cap ? `<i>${esc(o.cap)}</i>` : ""}
      ${o.busy ? `<u><span class="gen-spin"></span></u>` : ""}
    </button>`;

  /* Промпт генерации лежит на ПРЕДЫДУЩЕМ сообщении роли user: у ответа
     движка text пустой (см. backend/chat.py). Отсюда «повторить». */
  function promptOf(msg) {
    let out = "";
    for (const m of S.msgs) {
      if (m.id >= msg.id) break;
      if (m.role === "user" && m.text) out = m.text;
    }
    return out;
  }

  /* ─────────── 3D-моделька с твоим лицом: РАБОЧИЙ флоу ───────────
     Та же механика, что разворот в досье персонажа (generate-model,
     kind=model): создаём персонажа, грузим фото, просим лист ракурсов.
     Результат уже СОХРАНЁН в персонажах — «сохранить» не нужен. */
  function render3d(page) {
    page.className = "gen-page gen-3d-view";
    const d = S.d3;
    // Модели нужны кнопке «видео-облёт» (цена, движок). Тянем фоном и молча:
    // гостю страница обязана открыться без сессии — это витрина флоу.
    if (!S.meta) ensureData().then(() => { if (S.ws === "model3d") render(); }).catch(() => {});
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
    const hist = info.history || [];
    shell(page, {
      cls: "gen-3d-view",
      title: t("генератор · 3d-модель", "generator · 3d model"),
      panel: `
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
        <button type="button" class="gen-go gen-vgo gen-3d-go" ${d.files.length && !d.busy ? "" : "disabled"}>
          ${d.busy ? t("делаю модельку…", "building the model…")
                   : t("сделать 3D-модель", "build the 3D model")
                     + (info.cost ? ` <span>⚡ ${info.cost}</span>` : "")}</button>
        <div class="gen-note ${d.err ? "" : "hidden"}">${esc(d.err)}</div>`,
      stage: d.busy
        ? `<div class="gen-vempty"><span class="gen-spin"></span>
             <b>${t("генерирую разворот — до минуты", "generating the turnaround — up to a minute")}</b></div>`
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
        : `<div class="gen-vempty">
             <b>${t("здесь появится твой разворот", "your turnaround appears here")}</b>
             <small>${t("лист ракурсов соберётся в вертушку, а моделька станет персонажем для клипов",
               "the sheet turns into a spinner and the model becomes a clip character")}</small></div>`,
      // История разворотов приезжает с сервера (/api/model-sheet) — она на
      // месте и после перезагрузки страницы, и в другом браузере.
      history: hist.length
        ? hist.slice(0, 30).map((h, i) => hcard(i, { url: h.url, on: h.url === d.url })).join("")
        : "",
    });
    $$(".gen-vhistory .gen-hcard", page).forEach((b) => b.addEventListener("click", () => {
      const h = hist[Number(b.dataset.h)]; if (!h) return;
      d.url = h.url; d.sheet = false; d.vurl = ""; d.err = "";
      d.n = ((d.info?.views || []).find((v) => v.id === d.views) || { n: 4 }).n;
      render();
    }));
    $(".gen-drop input", page).addEventListener("change", (e) => {
      d.files = Array.from(e.target.files || []).slice(0, 4);
      d.url = ""; d.err = ""; d.vurl = "";
      render();
    });
    $$(".k-kind button", page).forEach((b) => b.addEventListener("click", () => { d.kind = b.dataset.k; render(); }));
    $$(".k-views button", page).forEach((b) => b.addEventListener("click", () => { d.views = b.dataset.k; render(); }));
    $$(".k-layout button", page).forEach((b) => b.addEventListener("click", () => { d.layout = b.dataset.k; render(); }));
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

  /* Вертушка: лист режется canvas'ом на N кадров ТЕМИ ЖЕ ДОЛЯМИ, что и
     серверная резка листа (CELL_INSET в main.py): наивная width/N цепляла
     края соседних панелей — по бокам кадра торчали руки соседних ракурсов.
     Сетка режется в ДВА измерения (и ширина, и высота своей долей). Драг
     листает кадры, отпускание снапит к ближайшему ракурсу; все кадры — из
     одного уже загруженного листа, мигать нечему. */
  const CELL_INSET = 0.012; // доля ячейки, зеркалит бэкенд-резку листа
  function bindSpinDrag(box, st, n, draw) {
    let sx = null, sf = 0;
    box.onpointerdown = (e) => { sx = e.clientX; sf = st.frame; box.setPointerCapture(e.pointerId); };
    box.onpointermove = (e) => { if (sx !== null) { st.frame = sf + (e.clientX - sx) / 45; draw(); } };
    const drop = () => {
      if (sx === null) return;
      sx = null;
      st.frame = ((Math.round(st.frame) % n) + n) % n; // снап без дрожи
      draw();
    };
    box.onpointerup = drop;
    box.onpointercancel = drop;
  }
  function mountSpin3d(page) {
    const d = S.d3;
    const box = $(".gen-3d-spin3d", page);
    const cv = $("canvas", box);
    const img = new Image();
    img.onload = () => {
      const n = d.n || 4;
      const rows = d.layout === "grid" ? 2 : 1;
      const cols = Math.ceil(n / rows);
      const cw = img.naturalWidth / cols;
      const ch = img.naturalHeight / rows;
      const ix = cw * CELL_INSET, iy = ch * CELL_INSET;
      cv.width = Math.round(cw - 2 * ix);
      cv.height = Math.round(ch - 2 * iy);
      const draw = () => {
        const k = ((Math.round(d.frame) % n) + n) % n;
        cv.getContext("2d").drawImage(img,
          (k % cols) * cw + ix, Math.floor(k / cols) * ch + iy,
          cw - 2 * ix, ch - 2 * iy, 0, 0, cv.width, cv.height);
      };
      draw();
      bindSpinDrag(box, d, n, draw);
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
        const cw = img.naturalWidth / cols, ch = img.naturalHeight / rows;
        const ix = cw * CELL_INSET, iy = ch * CELL_INSET; // та же резка, что у вертушки
        const cv = document.createElement("canvas");
        cv.width = Math.round(cw - 2 * ix); cv.height = Math.round(ch - 2 * iy);
        cv.getContext("2d").drawImage(img, ix, iy, cw - 2 * ix, ch - 2 * iy, 0, 0, cv.width, cv.height);
        cv.toBlob((b) => (b ? resolve(b) : reject(new Error("crop failed"))), "image/jpeg", .92);
      };
      img.onerror = () => reject(new Error("sheet not readable"));
      img.src = d.url;
    });
  }

  /* Общий видео-облёт: кадр → чат-конвейер видео. Работает и персонажу,
     и предмету — различается только промпт и объект состояния (vbusy/vurl). */
  async function orbitFromBlob(blob, st, prompt) {
    const fd = new FormData(); fd.append("file", blob, "model.jpg");
    const up = await api("/api/chat/upload", { method: "POST", body: fd });
    const beforeIds = new Set(S.msgs.filter((m) => m.kind === "video" && m.url).map((m) => m.id));
    await api(`/api/chats/${S.chatId}/messages`, { method: "POST", body: {
      engine: S.video.model, text: prompt,
      aspect: "9:16", duration: S.video.duration, file_ids: [up.id],
    } });
    st.vbusy = t("снимаю облёт…", "shooting the orbit…"); render();
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      await loadMsgs();
      const vid = S.msgs.filter((m) => m.role !== "user" && m.kind === "video"
        && m.url && !beforeIds.has(m.id)).pop();
      const busyOne = S.msgs.some((m) => m.role !== "user" && m.status
        && m.status !== "done" && m.status !== "error");
      if (vid && !busyOne) { st.vurl = vid.url; break; }
      if (!busyOne && !vid && i > 2) throw new Error(t("видео не вышло — токены возвращены", "video failed — tokens refunded"));
    }
  }

  async function video3d(trendId) {
    const d = S.d3;
    if (d.vbusy) return;
    d.vbusy = t("готовлю кадр…", "preparing the frame…"); d.vurl = ""; render();
    try {
      if (!S.meta) await ensureData(); // цена и движок видео — из моделей
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
        await orbitFromBlob(blob, d,
          "Slow smooth turntable rotation: the character stays in place "
          + "and the camera orbits a full circle around them. Neutral grey "
          + "studio background, even light, no zoom.");
      }
      if (!d.vurl) throw new Error(t("не дождался видео — загляни в супергенератор позже", "timed out — check the supergenerator later"));
    } catch (e) { d.err = e.message; }
    d.vbusy = ""; render();
  }

  /* ─────────── Предмет: 3D-вертушка товара ───────────
     Тот же вид, что экран персонажа, но сущность — ПРЕДМЕТ (трек мокап-
     проекта): фото товара → чистая моделька (/api/items/from-photo) →
     8-ракурсный облёт (/api/tracks/{id}/turnaround) → вертушка из кадров.
     Предмет ложится в ОБЩУЮ базу — дальше он слот «Продукт» в маркетинге
     и референс везде. Ракурсов ровно 8 и стиль решает конвейер: контролов
     «стиль/раскладка» на экране нет, потому что бэкенд их не принимает —
     чего конвейер не умеет, того на экране нет. */
  function renderItem(page) {
    const st = S.it;
    if (!S.meta) ensureData().then(() => { if (S.ws === "item3d") render(); }).catch(() => {});
    if (st.items === null) {
      st.items = false; // «грузится» — от повторных запросов
      fetch("/api/items/all", { credentials: "same-origin" })
        .then(async (r) => {
          if (r.status === 401 || r.status === 403) { st.items = "guest"; return; }
          if (!r.ok) { st.items = "fail"; return; }
          const d = await r.json();
          st.items = (d.items || []).filter((x) => x.url).slice(0, 30);
        })
        .catch(() => { st.items = "fail"; })
        .then(render);
    }
    const sel = st.sel;
    const frames = (sel && sel.turnaround_urls) || [];
    const list = Array.isArray(st.items) ? st.items : [];
    shell(page, {
      cls: "gen-3d-view gen-item-view",
      title: t("генератор · предмет", "generator · product"),
      panel: `
        <label class="gen-drop ${st.files.length ? "has" : ""}">
          ${st.files.length
            ? st.files.map((f) => `<img src="${URL.createObjectURL(f)}" alt=""/>`).join("")
            : `<span class="gen-drop-plus">＋</span>
               <b>${t("загрузи 1–4 фото товара", "upload 1–4 product photos")}</b>
               <small>${t("этикетка сохранится точь-в-точь; первое фото — главное", "the label is preserved exactly; the first photo leads")}</small>`}
          <input type="file" accept="image/*" multiple hidden />
        </label>
        <div class="gen-3d-row"><small>${t("ракурсы", "views")}</small>
          <span class="gen-3d-eng">8 · 360°</span></div>
        <button type="button" class="gen-go gen-vgo gen-it-go" ${st.files.length && !st.busy ? "" : "disabled"}>
          ${st.busy ? esc(st.busy) : t("сделать 3D-предмет", "build the 3D product")}</button>
        <div class="gen-note ${st.err ? "" : "hidden"}">${esc(st.err)}</div>`,
      stage: st.busy
        ? `<div class="gen-vempty"><span class="gen-spin"></span><b>${esc(st.busy)}</b></div>`
        : sel && frames.length ? `
          <div class="gen-3d-spin3d gen-it-spin" data-n="${frames.length}">
            <canvas></canvas>
            <span class="gen-3d-draghint">⟲ ${t("тяни, чтобы крутить", "drag to spin")}</span>
          </div>
          <div class="gen-3d-acts">
            <a href="${esc(frames[0])}" download>⬇ ${t("скачать", "download")}</a>
            <span class="gen-3d-saved">✓ ${t("сохранено в предметах", "saved to products")}</span>
            <button type="button" class="gen-3d-again">${t("сделать ещё", "make another")}</button>
          </div>
          <div class="gen-3d-video">
            ${st.vurl ? `<video src="${esc(st.vurl)}" controls autoplay loop muted playsinline></video>` : ""}
            ${st.vbusy ? `<div class="gen-3d-vbusy"><span class="gen-spin"></span> ${esc(st.vbusy)}</div>` : ""}
            <div class="gen-3d-vrow">
              <button type="button" class="gen-go gen-it-orbit" ${st.vbusy ? "disabled" : ""}>
                ${t("видео-облёт в один клик", "one-click orbit video")} <span>⚡ ${videoCost3d()}</span></button>
            </div>
          </div>`
        : sel ? `
          <img class="gen-3d-sheetimg" src="${esc(sel.url)}" alt=""/>
          <div class="gen-3d-acts">
            <button type="button" class="gen-go gen-it-turn">${t("вертушка · 8 ракурсов", "turnaround · 8 angles")}</button>
            <button type="button" class="gen-3d-again">${t("сделать ещё", "make another")}</button>
          </div>
          <div class="gen-note ${st.err ? "" : "hidden"}">${esc(st.err)}</div>`
        : `<div class="gen-vempty">
             <b>${t("здесь закрутится твой предмет", "your product spins here")}</b>
             <small>${t("фото превратится в чистую модельку и 8 ракурсов по кругу — предмет сохранится в общей базе и станет слотом «Продукт» в маркетинге",
               "the photo becomes a clean model and 8 angles around — the product lands in the shared base and powers the Product slot in marketing")}</small></div>`,
      // Предметы — общая база владельца: история переживает перезагрузку.
      history: list.length
        ? list.map((x, i) => hcard(i, { url: x.url, cap: x.title,
            on: Boolean(sel && sel.track_id === x.track_id) })).join("")
        : st.items === "fail"
          ? `<div class="gen-vempty-mini">${t("база предметов не ответила", "the product base did not answer")}</div>`
          : "",
    });
    $$(".gen-vhistory .gen-hcard", page).forEach((b) => b.addEventListener("click", () => {
      const x = list[Number(b.dataset.h)]; if (!x) return;
      st.sel = x; st.frame = 0; st.vurl = ""; st.err = "";
      render();
    }));
    $(".gen-drop input", page).addEventListener("change", (e) => {
      st.files = Array.from(e.target.files || []).slice(0, 4);
      st.err = ""; st.vurl = ""; st.sel = null;
      render();
    });
    $(".gen-it-go", page)?.addEventListener("click", () => makeItem());
    $(".gen-3d-again", page)?.addEventListener("click", () => {
      st.files = []; st.sel = null; st.vurl = ""; st.err = ""; render();
    });
    $(".gen-it-turn", page)?.addEventListener("click", () => spinItem());
    $(".gen-it-orbit", page)?.addEventListener("click", () => itemOrbit());
    if (sel && frames.length && !st.busy) mountSpinFrames(page, st, frames);
  }

  /* Вертушка предмета: кадры облёта — ОТДЕЛЬНЫЕ файлы, резать нечего.
     Прегружаем все 8, чтобы драг не мигал белым, дальше та же механика. */
  function mountSpinFrames(page, st, urls) {
    const box = $(".gen-it-spin", page);
    const cv = $("canvas", box);
    let loaded = 0;
    const imgs = urls.map((u) => {
      const im = new Image();
      im.onload = () => { loaded += 1; if (loaded === urls.length) start(); };
      im.onerror = () => { loaded += 1; if (loaded === urls.length) start(); };
      im.src = u;
      return im;
    });
    const start = () => {
      const first = imgs.find((im) => im.naturalWidth);
      if (!first || !box.isConnected) return;
      cv.width = first.naturalWidth; cv.height = first.naturalHeight;
      const n = imgs.length;
      const draw = () => {
        const k = ((Math.round(st.frame) % n) + n) % n;
        const im = imgs[k].naturalWidth ? imgs[k] : first;
        const ctx = cv.getContext("2d");
        ctx.clearRect(0, 0, cv.width, cv.height);
        ctx.drawImage(im, 0, 0, cv.width, cv.height);
      };
      draw();
      bindSpinDrag(box, st, n, draw);
    };
  }

  async function makeItem() {
    const st = S.it;
    if (!st.files.length || st.busy) return;
    st.err = ""; st.vurl = ""; st.sel = null;
    st.busy = t("создаю предмет…", "creating the product…"); render();
    try {
      // Предметы живут в мокап-проекте: берём существующий или заводим один.
      if (!st.projectId) {
        const ps = await api("/api/projects");
        const mk = (Array.isArray(ps) ? ps : []).find((p) => p.mode === "mockup");
        st.projectId = mk ? mk.id
          : (await api("/api/projects", { method: "POST",
              body: { name: t("Предметы", "Products"), kind: "mockup" } })).id;
      }
      const fd = new FormData(); fd.append("photo", st.files[0]);
      const made = await api(`/api/items/from-photo?project_id=${st.projectId}`,
        { method: "POST", body: fd });
      // Остальные фото — референсами того же предмета.
      for (const f of st.files.slice(1)) {
        const fd2 = new FormData(); fd2.append("photo", f);
        await api(`/api/tracks/${made.track_id}/photos`, { method: "POST", body: fd2 }).catch(() => {});
      }
      st.busy = t("чищу фон — рисую модельку предмета…", "cleaning up — drawing the product model…"); render();
      let tr = await waitItem(made.track_id, st);
      st.busy = t("снимаю 8 ракурсов по кругу…", "shooting 8 angles around…"); render();
      await api(`/api/tracks/${made.track_id}/turnaround`, { method: "POST" });
      tr = await waitItem(made.track_id, st);
      const frames = tr.turnaround_urls || [];
      if (!frames.length) throw new Error(t("облёт не вышел — токены возвращены", "turnaround failed — tokens refunded"));
      st.sel = { track_id: made.track_id, title: tr.title || "", url: frames[0], turnaround_urls: frames };
      st.frame = 0;
      st.files = [];
      st.items = null; // база пополнилась — перечитается при отрисовке
    } catch (e) { st.err = e.message; }
    st.busy = ""; render();
  }

  /* Вертушка для предмета из истории, у которого облёта ещё нет. */
  async function spinItem() {
    const st = S.it;
    const sel = st.sel;
    if (!sel || st.busy) return;
    st.err = "";
    st.busy = t("снимаю 8 ракурсов по кругу…", "shooting 8 angles around…"); render();
    try {
      if (!st.projectId && sel.project_id) st.projectId = sel.project_id;
      if (!st.projectId) {
        const ps = await api("/api/projects");
        const mk = (Array.isArray(ps) ? ps : []).find((p) => p.mode === "mockup");
        st.projectId = mk ? mk.id : 0;
      }
      await api(`/api/tracks/${sel.track_id}/turnaround`, { method: "POST" });
      const tr = await waitItem(sel.track_id, st, sel.project_id);
      const frames = tr.turnaround_urls || [];
      if (!frames.length) throw new Error(t("облёт не вышел — токены возвращены", "turnaround failed — tokens refunded"));
      st.sel = { ...sel, turnaround_urls: frames };
      st.frame = 0;
      st.items = null;
    } catch (e) { st.err = e.message; }
    st.busy = ""; render();
  }

  async function waitItem(tid, st, projectId) {
    const pid = projectId || st.projectId;
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 4000));
      const p = await api(`/api/project?project_id=${pid}`);
      const tr = (p.tracks || []).find((x) => x.id === tid);
      if (!tr) throw new Error(t("предмет пропал из проекта", "the item vanished from the project"));
      const s = tr.turnaround_status || "";
      if (s === "error") throw new Error(tr.turnaround_note || t("не получилось — токены возвращены", "failed — tokens refunded"));
      if (s !== "queued" && s !== "running") return tr;
      // Прогресс облёта приходит заметкой «i/8» — показываем живой счёт.
      if (/^\d+\/\d+$/.test(tr.turnaround_note || "")) {
        st.busy = t("ракурс ", "angle ") + tr.turnaround_note + "…"; render();
      }
    }
    throw new Error(t("не дождался генерации — загляни позже", "timed out — check back later"));
  }

  /* Видео-облёт предмета: текущий кадр вертушки → тот же чат-конвейер. */
  async function itemOrbit() {
    const st = S.it;
    const sel = st.sel;
    const frames = (sel && sel.turnaround_urls) || [];
    if (!frames.length || st.vbusy) return;
    st.vbusy = t("готовлю кадр…", "preparing the frame…"); st.vurl = ""; render();
    try {
      if (!S.meta) await ensureData(); // движок и цена видео — из моделей
      const k = ((Math.round(st.frame) % frames.length) + frames.length) % frames.length;
      const blob = await (await fetch(frames[k], { credentials: "same-origin" })).blob();
      await orbitFromBlob(blob, st,
        "Slow smooth turntable rotation: the exact product stays in place and "
        + "the camera orbits a full circle around it. Clean neutral studio "
        + "background, soft even light, every label letter identical to the "
        + "reference, no zoom.");
      if (!st.vurl) throw new Error(t("не дождался видео — загляни в супергенератор позже", "timed out — check the supergenerator later"));
    } catch (e) { st.err = e.message; }
    st.vbusy = ""; render();
  }

  /* ─────────── Персонаж: общая база героев ───────────
     Персонаж — лицо, которое держится во всех генерациях; ИИ-блогер — его
     работа. Залогиненному — герои из общей базы (/api/characters/all),
     «использовать» уводит в UGC-режим студии: там персона, формат и весь
     конвейер ролика, дублировать его здесь нельзя. Гостю — пример-карточки
     и вход: пустая сетка ничего не продаёт. */
  function renderChars(page) {
    const chars = S.blog.chars;
    const list = Array.isArray(chars) ? chars : [];
    const sel = S.blog.sel && list.find((c) => c.id === S.blog.sel.id) ? S.blog.sel : list[0] || null;
    S.blog.sel = sel;
    const demo = [
      { img: "/img/shots/feat-chars.jpg", cap: t("персонаж для клипа", "clip character") },
      { img: "/img/shots/frame-5.jpg", cap: t("ИИ-блогер", "AI blogger") },
      { img: "/img/shots/frame-6.jpg", cap: t("герой сериала", "series hero") },
    ];
    shell(page, {
      cls: "gen-blog-view",
      title: t("генератор · персонаж", "generator · character"),
      panel: `
        <p class="gen-blog-lead">${t("персонаж — это лицо, которое держится во всех кадрах: фото, видео, обзоры и распаковки. заведи героя один раз — и снимай с ним что угодно.",
          "a character is a face that holds across every shot: photos, videos, reviews. create one once — and shoot anything with them.")}</p>
        ${chars === "guest"
          ? `<button type="button" class="gen-go gen-vgo gen-login">${t("войти и завести своего", "sign in to create yours")}</button>`
          : `<button type="button" class="gen-go gen-vgo gen-blog-make">${t("новый персонаж из фото", "new character from photos")}</button>`}
        ${chars === "fail" ? `<div class="gen-note">${t("база персонажей не ответила — обнови страницу", "the character base did not answer — reload the page")}
            <button type="button" class="gen-blog-retry ghosty">${t("повторить", "retry")}</button></div>` : ""}`,
      stage: chars === null
        ? `<div class="gen-vempty"><span class="gen-spin"></span></div>`
        : chars === "guest" ? `
          <div class="gen-blog-grid gen-blog-demo-grid">${demo.map((x) => `
            <div class="gen-blog-card gen-blog-demo">
              <img src="${x.img}" alt="" loading="lazy"/>
              <b>${x.cap}</b><small>${t("пример", "sample")}</small>
            </div>`).join("")}</div>`
        : sel ? `
          ${sel.photo_url ? `<img class="gen-3d-sheetimg" src="${esc(sel.photo_url)}" alt=""/>`
            : `<div class="gen-vempty"><span class="gen-blog-ph">👤</span></div>`}
          <div class="gen-3d-acts">
            <b class="gen-blog-name">${esc(sel.name || "—")}</b>
            <button type="button" class="gen-go gen-blog-use">${t("использовать", "use")}</button>
          </div>`
        : `<div class="gen-vempty">
             <b>${t("пока пусто", "empty for now")}</b>
             <small>${t("сделай героя из своих фото — 3D-модель сохранится в персонажах и будет держать лицо во всех генерациях",
               "build a hero from your photos — the 3D model lands in characters and keeps the face everywhere")}</small></div>`,
      // Персонажи приезжают из общей базы владельца — это и есть история.
      history: list.length
        ? list.slice(0, 30).map((c, i) => hcard(i, { url: c.photo_url, cap: c.name || "—",
            ph: "👤", on: Boolean(sel && sel.id === c.id) })).join("")
        : "",
    });
    $(".gen-login", page)?.addEventListener("click", () => { closeGenerator(); if (typeof window.showLogin === "function") window.showLogin(); else location.hash = "#/login"; });
    // «Новый персонаж» ведёт в 3D-флоу: он создаёт персонажа из фото и сам
    // сохраняет его в общую базу.
    $(".gen-blog-make", page)?.addEventListener("click", () => { S.ws = "model3d"; render(); });
    $(".gen-blog-retry", page)?.addEventListener("click", () => { S.blog.chars = null; render(); });
    $(".gen-blog-use", page)?.addEventListener("click", () => goStudioMode("ugc"));
    $$(".gen-vhistory .gen-hcard", page).forEach((b) => b.addEventListener("click", () => {
      const c = list[Number(b.dataset.h)]; if (!c) return;
      S.blog.sel = c; render();
    }));
    if (chars === null) loadChars();
  }

  /* Список героев — ВСЕ проекты владельца (/api/characters/all). Раньше сбой
     сервера и пустая база выглядели одинаково («пока пусто»), и настоящая
     ошибка читалась как «у меня нет персонажей». Теперь состояния разные:
     guest — гость, fail — сервер не ответил, [] — база правда пуста. */
  function loadChars() {
    fetch("/api/characters/all", { credentials: "same-origin" })
      .then(async (r) => {
        if (r.status === 401 || r.status === 403) { S.blog.chars = "guest"; return; }
        if (!r.ok) { S.blog.chars = "fail"; return; }
        const d = await r.json();
        S.blog.chars = d.characters || [];
      })
      .catch(() => { S.blog.chars = "fail"; })
      .then(render);
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
          <small>${t("моделька и 3D-вертушка товара по фото", "a clean model and 3D spin from a photo")}</small>
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
      // «Предметы» открывают РАБОЧИЙ экран сразу — как 3D-модель, только
      // для товара; полный мокап-конвейер по-прежнему в студии.
      if (go === "mockup") { S.ws = "item3d"; render(); return; }
      if (go === "clip") return goStudioMode(go);
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
  // Пустая галерея — не одинокая подсказка по центру, а примеры-карточки:
  // клик кладёт готовый промт в композер, человек стартует с образца.
  const photoIdeas = () => [
    { cap: t("обложка трека", "track cover"),
      txt: t("Обложка трека: крупный портрет в контровом свете, зерно плёнки, глубокие тени, место под крупную типографику",
             "Track cover: close portrait in rim light, film grain, deep shadows, room for bold typography") },
    { cap: t("неоновый кадр", "neon frame"),
      txt: t("Ночная улица в неоне, лёгкий дождь, отражения в лужах, кинематографичный кадр 35мм",
             "Neon-lit night street, light rain, puddle reflections, cinematic 35mm frame") },
    { cap: t("предметка", "product shot"),
      txt: t("Предметная съёмка товара на чистом фоне: мягкие студийные тени, лёгкий отблеск, этикетка читается",
             "Product shot on a clean background: soft studio shadows, subtle highlight, readable label") },
    { cap: t("3D-персонаж", "3D character"),
      txt: t("Стилизованный 3D-персонаж в полный рост, студийный свет, нейтральный фон, детальная фактура",
             "Stylized full-body 3D character, studio light, neutral background, detailed texture") },
  ];
  /* ФОТО в том же макете, что видео: панель слева, стейдж по центру,
     история справа. Плавающая капсула-композер снизу убрана — она была
     единственным местом сервиса, где настройки жили не в панели. */
  function renderPhoto(page) {
    const done = S.msgs.filter((m) => m.role !== "user" && m.kind === "image");
    const st = S.photo; const m = model(st.model) || {};
    const hist = done.slice().reverse().slice(0, 30);
    // Стейдж: выбранное из истории, иначе последняя удачная генерация.
    const cur = (st.sel && hist.find((x) => x.id === st.sel.id)) || hist.find((x) => x.url) || null;
    st.sel = cur;
    const busyOne = done.find((x) => x.status && x.status !== "done" && x.status !== "error");
    shell(page, {
      cls: "gen-photo-view",
      title: t("генератор · фото", "generator · photo"),
      tools: switcher(),
      panel: `
        <div class="gen-vmode">${t("создать изображение", "create an image")}</div>
        <label class="gen-vref gen-vref-multi">
          ${st.files.length
            ? st.files.map((f) => `<img src="${f.thumb_url || f.url}" alt=""/>`).join("")
            : `<span class="gen-vref-plus">＋</span><b>${t("добавить референс", "add a reference")}</b><small>jpg · png · webp</small>`}
          <input type="file" accept="image/*" multiple hidden />
        </label>
        ${st.files.length ? `<div class="gen-refs">${st.files.map((f) =>
          `<span class="gen-ref"><img src="${f.thumb_url || f.url}" alt=""/><button data-rm="${f.id}">×</button></span>`).join("")}</div>` : ""}
        <textarea class="gen-vprompt gen-prompt" maxlength="${S.meta.prompt_limit || 2000}"
          placeholder="${t("опиши изображение, которое хочешь создать…", "describe the image you want to create…")}">${esc(st.prompt || "")}</textarea>
        <div class="gen-vrow"><small>${t("модель", "model")}</small>${modelChip("photo")}</div>
        <div class="gen-vrow">${(m.aspects || []).length > 1 ? `<small>${t("формат", "aspect")}</small>` + chip("aspect", st.aspect, m.aspects) : ""}</div>
        <div class="gen-vrow">${(m.resolutions || []).length > 1 ? `<small>${t("качество", "quality")}</small>` + chip("resolution", st.resolution, m.resolutions) : ""}</div>
        <div class="gen-vrow"><small>${t("вариантов", "variants")}</small>
          <div class="gen-count"><button type="button" data-d="-1">−</button><span>${st.variants}</span><button type="button" data-d="1">＋</button></div></div>
        <button type="button" class="gen-go gen-vgo">${t("сгенерировать", "generate")} <span>✦ ${cost("photo")}</span></button>
        <div class="gen-note hidden"></div>`,
      stage: busyOne
        ? `<div class="gen-vempty"><span class="gen-spin"></span><b>${t("генерирую…", "generating…")}</b></div>`
        : cur && cur.url ? `
          <img class="gen-3d-sheetimg gen-stage-img" src="${esc(cur.url)}" alt=""/>
          <div class="gen-3d-acts">
            <a href="${esc(cur.url)}" download>⬇ ${t("скачать", "download")}</a>
            <button type="button" class="gen-repeat">${t("повторить", "repeat")}</button>
          </div>`
        : `<div class="gen-ideas">
            <b>${t("с чего начать — выбери образец и поправь под себя", "pick a sample and make it yours")}</b>
            <div class="gen-ideas-row">${photoIdeas().map((x, i) => `
              <button type="button" class="gen-idea" data-i="${i}">
                <b>${x.cap}</b><small>${x.txt}</small>
              </button>`).join("")}</div>
          </div>`,
      // История — сообщения ленты «генератор» с сервера: остаётся после
      // перезагрузки страницы и видна с любого устройства.
      history: hist.length
        ? hist.map((msg, i) => hcard(i, {
            url: msg.thumb_url || msg.url, kind: "image",
            busy: Boolean(msg.status && msg.status !== "done" && msg.status !== "error"),
            on: Boolean(cur && cur.id === msg.id) })).join("")
        : "",
    });
    bindSwitch(page);
    bindChips(page, {
      model: (v) => { st.model = v; syncCaps("photo"); },
      aspect: (v) => { st.aspect = v; },
      resolution: (v) => { st.resolution = v; syncCaps("photo"); },
      variants: (v) => { st.variants = parseInt(v, 10) || 1; },
    });
    const ta = $(".gen-prompt", page);
    ta.addEventListener("input", () => { st.prompt = ta.value; });
    $(".gen-vref input", page).addEventListener("change", (e) => uploadRefs(e.target.files, st, page));
    $$(".gen-ref button", page).forEach((b) => b.addEventListener("click", () => {
      st.files = st.files.filter((f) => String(f.id) !== b.dataset.rm); render();
    }));
    $$(".gen-count button", page).forEach((b) => b.addEventListener("click", () => {
      const maxV = Math.min(4, S.meta.max_variants || 4);
      st.variants = Math.max(1, Math.min(maxV, (st.variants || 1) + parseInt(b.dataset.d, 10)));
      render();
    }));
    $(".gen-vgo", page).addEventListener("click", () => sendPhoto(page));
    $(".gen-stage-img", page)?.addEventListener("click", () => showBig(cur.url, "image"));
    // «Повторить» — промпт той генерации обратно в поле: он лежит на
    // предыдущем сообщении роли user, у ответа движка text пустой.
    $(".gen-repeat", page)?.addEventListener("click", () => {
      st.prompt = promptOf(cur) || st.prompt || "";
      render();
      $(".gen-prompt", page)?.focus();
    });
    $$(".gen-vhistory .gen-hcard", page).forEach((b) => b.addEventListener("click", () => {
      const msg = hist[Number(b.dataset.h)]; if (!msg) return;
      st.sel = msg; render();
    }));
    const ideas = photoIdeas();
    $$(".gen-idea", page).forEach((b) => b.addEventListener("click", () => {
      st.prompt = ideas[Number(b.dataset.i)].txt;
      render();
      $(".gen-prompt", page)?.focus();
    }));
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
      st.sel = null;  // стейдж показывает свежую генерацию, а не старый выбор
      await loadMsgs(); render();
    } catch (e) { note.textContent = e.message; note.classList.remove("hidden"); }
  }

  /* ─────────── ВИДЕО ─────────── */
  function renderVideo(page) {
    const vids = S.msgs.filter((m) => m.role !== "user" && m.kind === "video");
    const st = S.video; const m = model(st.model) || {};
    const durs = m.durations || S.meta.durations || [];
    const hist = vids.slice().reverse().slice(0, 30);
    const cur = (st.sel && hist.find((x) => x.id === st.sel.id)) || hist.find((x) => x.url) || null;
    st.sel = cur;
    const busyOne = vids.find((x) => x.status && x.status !== "done" && x.status !== "error");
    shell(page, {
      cls: "gen-video-view",
      title: t("генератор · видео", "generator · video"),
      tools: switcher(),
      panel: `
        <div class="gen-vmode">${t("создать видео", "create a video")}</div>
        <label class="gen-vref">
          ${st.ref ? `<img src="${st.ref.thumb_url || st.ref.url}" alt=""/><span class="gen-vref-swap">✎</span>`
            : `<span class="gen-vref-plus">＋</span><b>${t("добавить референс", "add a reference")}</b><small>jpg · png · webp</small>`}
          <input type="file" accept="image/*" hidden />
        </label>
        ${st.ref ? `<button type="button" class="gen-vref-rm">${t("убрать референс", "remove reference")}</button>` : ""}
        <textarea class="gen-vprompt" maxlength="${S.meta.prompt_limit || 2000}"
          placeholder="${t("опиши, что должно происходить в видео…", "describe what should happen in the video…")}">${esc(st.prompt || "")}</textarea>
        <div class="gen-vrow"><small>${t("модель", "model")}</small>${modelChip("video")}</div>
        <div class="gen-vrow">${durs.length > 1 ? `<small>${t("длительность", "duration")}</small>` + chip("duration", st.duration + "s", durs.map((d) => ({ v: d, label: d + "s" }))) : ""}</div>
        <div class="gen-vrow">${(m.aspects || []).length > 1 ? `<small>${t("формат", "aspect")}</small>` + chip("aspect", st.aspect, m.aspects) : ""}</div>
        <div class="gen-vrow">${(m.resolutions || []).length > 1 ? `<small>${t("качество", "quality")}</small>` + chip("resolution", st.resolution, m.resolutions) : ""}</div>
        <button type="button" class="gen-go gen-vgo">${t("сгенерировать", "generate")} <span>✦ ${cost("video")}</span></button>
        <div class="gen-note hidden"></div>`,
      stage: busyOne && !(cur && cur.url)
        ? `<div class="gen-vempty"><span class="gen-spin"></span><b>${t("видео генерируется…", "generating the video…")}</b></div>`
        : cur && cur.url ? `
          <video src="${esc(cur.url)}" controls loop playsinline></video>
          <div class="gen-3d-acts">
            <a href="${esc(cur.url)}" download>⬇ ${t("скачать", "download")}</a>
            <button type="button" class="gen-repeat">${t("повторить", "repeat")}</button>
          </div>`
        : `<div class="gen-vempty"><b>${t("здесь появится твоё видео", "your video will appear here")}</b><small>${t("загрузи фото, опиши движение и нажми «сгенерировать»", "upload a photo, describe the motion and hit generate")}</small></div>`,
      // История видео — те же серверные сообщения ленты «генератор».
      history: hist.length
        ? hist.map((msg, i) => hcard(i, {
            url: msg.url, kind: "video",
            busy: Boolean(msg.status && msg.status !== "done" && msg.status !== "error"),
            on: Boolean(cur && cur.id === msg.id) })).join("")
        : "",
    });
    bindSwitch(page);
    bindChips(page, {
      model: (v) => { st.model = v; syncCaps("video"); },
      duration: (v) => { st.duration = parseInt(v, 10) || 0; },
      aspect: (v) => { st.aspect = v; },
      resolution: (v) => { st.resolution = v; },
    });
    const ta = $(".gen-vprompt", page);
    ta.addEventListener("input", () => { st.prompt = ta.value; });
    $(".gen-vref input", page).addEventListener("change", (e) => uploadRefs(e.target.files, st, page));
    $(".gen-vref-rm", page)?.addEventListener("click", () => { st.ref = null; render(); });
    $(".gen-repeat", page)?.addEventListener("click", () => {
      st.prompt = promptOf(cur) || st.prompt || "";
      render();
      $(".gen-vprompt", page)?.focus();
    });
    $$(".gen-vhistory .gen-hcard", page).forEach((b) => b.addEventListener("click", () => {
      const msg = hist[Number(b.dataset.h)]; if (!msg) return;
      st.sel = msg; render();
    }));
    $(".gen-vgo", page).addEventListener("click", async () => {
      const note = $(".gen-note", page);
      const text = ($(".gen-vprompt", page).value || "").trim();
      const mm = model(st.model) || {};
      if (mm.needs_image && !st.ref) { note.textContent = t("этой модели нужен кадр: загрузи фото-референс", "this model needs a frame: upload a photo"); note.classList.remove("hidden"); return; }
      try {
        await api(`/api/chats/${S.chatId}/messages`, { method: "POST", body: {
          engine: st.model, text, aspect: st.aspect, duration: st.duration,
          file_ids: st.ref ? [st.ref.id] : [],
        } });
        st.ref = null;
        st.sel = null;
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
