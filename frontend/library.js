/* ═══════════════════════════════════════════════════════════════════════════
   lolq.ai — КАТАЛОГ ПРОМТОВ: сценарии · сцены · движение · свет и цвет

   Подключается ПОСЛЕ sections.js:
       <script src="/sections.js?v=70"></script>
       <script src="/library.js?v=70"></script>

   ═══════════════ ЧЕТЫРЕ ВКЛАДКИ — ЭТО ЧЕТЫРЕ РАЗНЫЕ СУЩНОСТИ ═══════════════
   Не четыре размера одного и того же списка, а четыре ответа на разные
   вопросы, и заполняют они разные поля:

     Сценарии       — весь клип: сюжет проекта и заметка режиссёра трека;
     Сцены          — один кадр: шесть полей кадра разом;
     Движение       — motion_prompt и camera_move, ЗАМЕНЯЯ их;
     Свет и цвет    — хвост, ДОПИСЫВАЕМЫЙ в конец обоих кадров.

   Пятой вкладки «Стили» здесь нет и не будет: текст фирменных пятнадцати
   закрыт навсегда, наружу от них уходят подпись и описание — и те живут в
   пикере стиля трека, а не тут.

   ═══════════════════ ПОЧЕМУ СБОРКА, А НЕ ПРОСТО «ПРИМЕНИТЬ» ═══════════════════
   Владелец просил миксовать. Миксовать можно только то, что не спорит между
   собой, поэтому сборка (одна заготовка + одно движение + до двух световых)
   всё время держится на экране полосой внизу листа и на КАЖДОЕ изменение
   спрашивает сервер про конфликты. Конфликт не запрещает — он объясняется
   словами: «камера стоит» и «камера едет» не бывают в одном кадре, и решает
   человек. Молча собрать несобираемое значило бы продать испорченный кадр.

   ═══════════════ «ЧТО ИЗМЕНИТСЯ» ПОКАЗЫВАЕТСЯ ДО, А НЕ ПОСЛЕ ═══════════════
   Ни одна кнопка каталога не пишет сразу. Сначала POST /api/mix читает кадр и
   возвращает `before` рядом с `scene` — диалог показывает «было → станет»
   построчно и подсвечивает ровно те поля, которые реально меняются. Пишет уже
   обычный PATCH: сцены, проекта, трека. Своей копии полей кадра здесь нет —
   подстановку и склейку делает сервер, иначе первая же правка формулировки
   разъехалась бы с тем, что применяет урок.

   ═══════════════════════════ МИНИМАЛИЗМ ═══════════════════════════
   На листе ОДНО огненное действие — кнопка подтверждения в диалоге
   применения. В самом каталоге сотня карточек: .primary на каждой превратила
   бы экран в сотню одинаково «главных» кнопок, то есть ни в одну. Вкладки и
   группы показаны весом текста и волосяной рамкой, фон спокойный.
   См. docs/DESIGN_SYSTEM.md.

   Публичный API: window.QlolLibrary
     render(body, opts)   — нарисовать лист каталога (зовёт sections.js)
     forget()             — сбросить кэш (смена языка)
     pick(kind, key)      — положить карточку в сборку снаружи
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";
  if (window.QlolLibrary) return;

  /* Обвязка листа берётся у sections.js ЦЕЛИКОМ и лениво: перевод,
     экранирование, тост, список треков, диалоги приёма и набора. Своя копия
     означала бы второе поведение — одно в академии, другое здесь. */
  const UI = () => (window.QlolSections && window.QlolSections.ui) || {};
  const T = (k, f) => (UI().T ? UI().T(k, f) : f || "");
  const TF = (k, v, f) => (UI().TF ? UI().TF(k, v, f) : f || "");
  const esc = (v) => (UI().esc
    ? UI().esc(v)
    : String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c])));
  const lang = () => (UI().lang ? UI().lang() : "en");
  const toast = (s) => { if (UI().toast) UI().toast(s); };
  const errorText = (e) => (UI().errorText ? UI().errorText(e) : (e && e.message) || "");
  const planName = (t) => (UI().planName ? UI().planName(t) : String(t || "").toUpperCase());
  const tracksOf = () => (UI().tracksOf ? UI().tracksOf() : []);
  const sceneOptions = (tr) => (UI().sceneOptions ? UI().sceneOptions(tr) : "");

  const $ = (sel, r) => (r || document).querySelector(sel);
  const $$ = (sel, r) => Array.from((r || document).querySelectorAll(sel));

  /* ─────────────────────────────── состояние ─────────────────────────────── */

  let data = null;            // ответ /api/library
  let dataLang = "";
  let tab = "boards";         // scripts | boards | motions | lights
  let query = "";
  /* Группа СВОЯ У КАЖДОЙ ВКЛАДКИ. Общая обнулялась бы при каждом переключении,
     и человек, вернувшийся в «Сцены», каждый раз начинал бы с «Все». */
  const group = { scripts: "", boards: "", motions: "", lights: "" };
  /* Сборка. Потолки держит сервер (MIX_RULES), здесь только то же число, чтобы
     не отправлять заведомо обрезаемое. */
  let sel = { board: "", motion: "", lights: [] };
  let conflicts = [];
  /* Подстановка живёт НА ВЕСЬ СЕАНС каталога: человек называет персонажа один
     раз, а применяет десять карточек. Просить имя в каждом диалоге — это
     десять раз одна и та же работа. */
  let slots = {};
  let ctx = null;             // {kind:"scene", sceneId} | {kind:"make"} | null
  let root = null;            // тело листа

  const LAYERS = ["scripts", "boards", "motions", "lights"];

  function forget() { data = null; dataLang = ""; }

  async function load(force) {
    if (!force && data && dataLang === lang()) return data;
    dataLang = lang();
    data = await api(`/api/library?lang=${encodeURIComponent(lang())}`);
    return data;
  }

  /* ─────────────────────────────── лист ─────────────────────────────── */

  async function render(body, opts) {
    root = body;
    const o = opts || {};
    ctx = o.ctx || null;
    body.innerHTML = `<p class="muted">${esc(T("common.loading", "загружаю…"))}</p>`;
    try {
      // БЕЗ force. Каталог со всеми текстами — это сотни килобайт, и тянуть их
      // заново на каждое открытие листа значит платить за то, что не менялось:
      // реестр приезжает деплоем, а не правкой в базе. Сбрасывает кэш только
      // смена языка, через forget().
      await load();
    } catch (e) {
      body.innerHTML = `<p class="muted">${esc(T("library.failed", ""))}</p>`;
      return;
    }
    // Пришли с ключом — открываем сразу ту вкладку, где карточка живёт.
    if (o.key) {
      const found = locate(o.key);
      if (found) { tab = found.tab; group[tab] = ""; }
    }
    paint();
    if (o.key) {
      const card = $(`.pb-card[data-key="${cssKey(o.key)}"]`, body);
      if (card) { card.classList.add("hit"); card.scrollIntoView({ block: "center" }); }
    }
  }

  const cssKey = (k) => String(k).replace(/["\\]/g, "");

  function locate(key) {
    if ((data.scripts || []).some((x) => x.key === key)) return { tab: "scripts" };
    if ((data.boards || []).some((x) => x.key === key)) return { tab: "boards" };
    if ((data.motions || []).some((x) => x.key === key)) return { tab: "motions" };
    if ((data.lights || []).some((x) => x.key === key)) return { tab: "lights" };
    if ((data.shots || []).some((x) => x.key === key)) return { tab: "boards" };
    if ((data.packs || []).some((x) => x.key === key)) return { tab: "scripts" };
    return null;
  }

  function paint() {
    if (!root) return;
    root.innerHTML = `
      <div class="pb-top">
        <p class="ac-lead">${esc(T("promptbase.lead", ""))}</p>
        ${ctxLine()}
        <div class="pb-tabs" role="tablist">${LAYERS.map(tabBtn).join("")}</div>
        <p class="pb-tabhint muted">${esc(T("promptbase.hint." + tab, ""))}</p>
        <input class="pb-search" type="search" value="${esc(query)}"
               placeholder="${esc(T("promptbase.searchPh", ""))}" />
        <div class="pb-groups">${groupChips()}</div>
      </div>
      <div class="pb-list"></div>
      <p class="pb-styles muted">${esc(T("library.signatureNote", ""))}</p>
      <div class="pb-mix"></div>`;

    $$(".pb-tab", root).forEach((b) => b.addEventListener("click", () => {
      tab = b.dataset.tab;
      paint();
    }));
    $$(".pb-group", root).forEach((b) => b.addEventListener("click", () => {
      group[tab] = b.dataset.group;
      paint();
    }));
    const s = $(".pb-search", root);
    s.addEventListener("input", () => { query = s.value; paintList(); });
    paintList();
    paintMix();
  }

  /* ОТКУДА ПРИШЛИ. Каталог открывается из трёх мест — из ленты разделов, с
     карточки кадра и из мастерской, — и «применить» в каждом означает разное.
     Строка говорит это заранее, чтобы человек не выяснял назначение кнопки
     нажатием на неё. */
  function ctxLine() {
    if (!ctx) return "";
    if (ctx.kind === "make") {
      return `<p class="pb-ctx">${esc(T("promptbase.ctxMake", ""))}</p>`;
    }
    if (ctx.kind === "scene") {
      return `<p class="pb-ctx">${esc(TF("promptbase.ctxScene",
        { n: ctx.position || "" }, ""))}</p>`;
    }
    return "";
  }

  function tabBtn(id) {
    const n = countOf(id);
    return `<button type="button" role="tab" class="pb-tab${tab === id ? " on" : ""}"
      data-tab="${id}" aria-selected="${tab === id}"
      >${esc(T("promptbase.tab." + id, id))}<span class="pb-n">${n}</span></button>`;
  }

  /* Всё, что живёт на вкладке, одним списком — включая хвостовой прежний слой.
     Нужен и цифре у вкладки, и подсказке «искомое лежит на соседней». */
  function poolOf(id) {
    if (id === "scripts") return (data.scripts || []).concat(data.packs || []);
    if (id === "boards") return (data.boards || []).concat(data.shots || []);
    return data[id] || [];
  }

  function countOf(id) { return poolOf(id).length; }

  /* Группы вкладки. У «Сценариев» это темп монтажа (по нему и выбирают трек),
     у остальных — группы из реестра. Хвостовой чип уводит в прежние слои:
     наборы приёмов применяются на весь трек, поэтому стоят рядом со
     сценариями, а одиночные приёмы — рядом со сценами. */
  function groupsOf() {
    if (tab === "scripts") {
      return [
        { key: "cut:slow", label: T("promptbase.cut.slow", "медленный монтаж") },
        { key: "cut:mid", label: T("promptbase.cut.mid", "средний") },
        { key: "cut:fast", label: T("promptbase.cut.fast", "быстрый") },
        { key: "packs", label: T("library.packs", "Наборы"), tail: true },
      ];
    }
    if (tab === "boards") {
      return (data.groups.boards || []).concat(
        [{ key: "shots", label: T("promptbase.legacyShots", "Приёмы"), tail: true }]);
    }
    return data.groups[tab] || [];
  }

  function groupChips() {
    const rows = groupsOf();
    const all = `<button type="button" class="pb-group${group[tab] ? "" : " on"}"
      data-group="">${esc(T("library.all", "Все"))}</button>`;
    return all + rows.map((g) => `<button type="button" class="pb-group${
      group[tab] === g.key ? " on" : ""}${g.tail ? " tail" : ""}"
      data-group="${esc(g.key)}"${g.hint ? ` title="${esc(g.hint)}"` : ""}
      >${esc(g.label)}</button>`).join("");
  }

  /* ─────────────────────────────── список ─────────────────────────────── */

  function paintList() {
    const list = $(".pb-list", root);
    if (!list) return;
    const rows = visible();
    list.innerHTML = rows.length
      ? rows.map((r) => r.html).join("")
      : `<p class="muted">${esc(T("library.empty", "Ничего не нашлось."))}</p>${elsewhere()}`;
    bind(list);
    $$(".pb-else-go", list).forEach((b) => b.addEventListener("click", () => {
      // Поиск НЕ сбрасываем: человек переходит именно за тем, что искал.
      tab = b.dataset.tab;
      paint();
    }));
  }

  /* «Ничего не нашлось» — почти всегда неправда: карточка есть, просто на
     соседней вкладке. Контровой свет ищут в сценах, наезд — в свете, и молчать
     об этом значит учить человека, что в каталоге этого нет. Считаем по всем
     остальным вкладкам и показываем, куда идти. */
  function elsewhere() {
    if (!query.trim()) return "";
    const hits = LAYERS.filter((id) => id !== tab)
      .map((id) => ({ id: id, n: poolOf(id).filter(matches).length }))
      .filter((x) => x.n);
    if (!hits.length) return "";
    return `<p class="pb-else">${esc(T("promptbase.foundElsewhere", ""))} ${
      hits.map((h) => `<button type="button" class="pb-else-go" data-tab="${h.id}"
        >${esc(T("promptbase.tab." + h.id, h.id))} · ${h.n}</button>`).join(" ")}</p>`;
  }

  /* Поиск идёт по ВСЕМ читаемым полям карточки, включая ключ: ключи владелец
     знает наизусть и ищет ими чаще, чем подписями. */
  function hay(c) {
    return [c.key, c.label, c.desc, c.logline, c.hero, c.motif, c.note, c.gain,
      c.music, c.camera, c.shot, c.physics,
      (c.tags || []).join(" "),
      (c.traits || []).map((t) => t.label || t).join(" ")]
      .filter(Boolean).join(" ").toLowerCase();
  }

  function matches(c) {
    const q = query.trim().toLowerCase();
    return !q || hay(c).includes(q);
  }

  function visible() {
    const g = group[tab];
    const wrap = (items, fn) => items.filter(matches).map((c) => ({ card: c, html: fn(c) }));
    if (tab === "scripts") {
      if (g === "packs") return wrap(data.packs || [], packCard);
      let rows = data.scripts || [];
      if (g.startsWith("cut:")) rows = rows.filter((x) => x.cut === g.slice(4));
      return wrap(rows, scriptCard);
    }
    if (tab === "boards") {
      if (g === "shots") return wrap(data.shots || [], shotCard);
      const rows = (data.boards || []).filter((b) => !g || b.group === g);
      return wrap(rows, boardCard);
    }
    const rows = (data[tab] || []).filter((c) => !g || c.group === g);
    return wrap(rows, tab === "motions" ? motionCard : lightCard);
  }

  /* ─────────────────────────────── карточки ─────────────────────────────── */

  function head(c, kind) {
    const picked = isPicked(kind, c.key);
    return `<header>
      <h4>${esc(c.label)}</h4>
      ${picked ? `<span class="pb-picked">${esc(T("promptbase.inMix", "в сборке"))}</span>` : ""}
      ${c.locked ? `<span class="lb-tier">${esc(planName(c.tier))}</span>` : ""}
    </header>`;
  }

  function lockedNote(c) {
    return `<p class="lb-locked">${esc(TF("library.locked", { plan: planName(c.tier) }, ""))}</p>`;
  }

  function prompts(rows) {
    const body = rows.filter((r) => r[1]).map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    return body ? `<dl class="lb-prompts">${body}</dl>` : "";
  }

  function slotLine(c) {
    const s = (c.slots || []).map((x) =>
      `<code title="${esc(x.hint || "")}">{${esc(x.key)}}</code>`).join(" ");
    return s ? `<p class="lb-slots">${esc(T("library.slotsTitle", "Слоты"))}: ${s}</p>` : "";
  }

  function traitLine(c) {
    const s = (c.traits || []).map((t) =>
      `<span class="pb-trait">${esc(t.label || t)}</span>`).join("");
    return s ? `<p class="pb-traits">${s}</p>` : "";
  }

  /* Кнопки карточки. Огня здесь нет намеренно — он один на лист и стоит на
     подтверждении применения. */
  function actions(c, kind) {
    if (c.locked) {
      return `<div class="row lb-actions">
        <button type="button" class="pb-plans ghost">${esc(T("library.seePlans", ""))}</button>
      </div>`;
    }
    const inMix = isPicked(kind, c.key);
    return `<div class="row lb-actions">
      <button type="button" class="pb-apply">${esc(T("library.apply", "Применить"))}</button>
      <button type="button" class="pb-pick${inMix ? " on" : ""}">${
        esc(inMix ? T("promptbase.unpick", "− из сборки")
                  : T("promptbase.pick", "+ в сборку"))}</button>
      <button type="button" class="pb-copy ghost">${esc(T("library.copy", "Скопировать"))}</button>
    </div>`;
  }

  function boardCard(c) {
    return `<article class="pb-card lb-card" data-kind="board" data-key="${esc(c.key)}">
      ${head(c, "board")}
      <p class="lb-desc">${esc(c.desc)}</p>
      <p class="pb-meta">${esc(c.shot)} · ${esc(c.camera)}${
        c.needs_last ? " · " + esc(T("promptbase.needsLast", "нужен последний кадр")) : ""}</p>
      ${c.locked ? lockedNote(c) : prompts([
        [T("library.first", "Первый кадр"), c.first],
        [T("library.last", "Последний кадр"), c.last],
        [T("library.motion", "Движение"), c.motion],
        [T("promptbase.solo", "Grok: один кадр"), c.solo],
        [T("promptbase.negative", "Запреты"), c.negative],
      ])}
      ${slotLine(c)}${traitLine(c)}
      ${c.note ? `<p class="lb-result muted">${esc(c.note)}</p>` : ""}
      ${actions(c, "board")}
    </article>`;
  }

  function motionCard(c) {
    return `<article class="pb-card lb-card" data-kind="motion" data-key="${esc(c.key)}">
      ${head(c, "motion")}
      <p class="lb-desc">${esc(c.desc)}</p>
      <p class="pb-meta">${esc(c.camera || T("promptbase.noCamera", "камера не меняется"))}${
        c.bracket ? " · MiniMax " + esc(c.bracket) : ""}</p>
      ${c.locked ? lockedNote(c) : prompts([
        [T("library.motion", "Движение"), c.text],
        [T("promptbase.solo", "Grok: один кадр"), c.solo],
      ])}
      ${c.physics ? `<p class="lb-gain"><b>${esc(T("promptbase.physics", "Физика"))}:</b> ${
        esc(c.physics)}</p>` : ""}
      ${slotLine(c)}${traitLine(c)}
      ${actions(c, "motion")}
    </article>`;
  }

  function lightCard(c) {
    // Уровень карточки — не украшение: «палитра» и «грейд» спорят со стилем
    // трека, и таких в сборке разрешена только одна.
    const lvl = c.level === "style" ? T("promptbase.levelStyle", "палитра")
                                    : T("promptbase.levelScene", "свет сцены");
    return `<article class="pb-card lb-card" data-kind="light" data-key="${esc(c.key)}">
      ${head(c, "light")}
      <p class="lb-desc">${esc(c.desc)}</p>
      <p class="pb-meta">${esc(lvl)}</p>
      ${c.locked ? lockedNote(c) : prompts([[T("promptbase.add", "Дописывается"), c.add]])}
      ${slotLine(c)}${traitLine(c)}
      ${c.note ? `<p class="lb-result muted">${esc(c.note)}</p>` : ""}
      ${actions(c, "light")}
    </article>`;
  }

  function scriptCard(c) {
    const acts = (c.acts || []).map((a) => `<li>
      <span class="pb-act-share">${Math.round((a.share || 0) * 100)}%</span>
      <span class="pb-act-t"><b>${esc(a.label)}</b> ${esc(a.text)}</span></li>`).join("");
    const sc = c.scenes || {};
    return `<article class="pb-card lb-card pb-script" data-kind="script" data-key="${esc(c.key)}">
      ${head(c, "script")}
      <p class="lb-desc">${esc(c.logline)}</p>
      <p class="pb-meta">${esc(c.music)}</p>
      <p class="pb-meta">${esc(TF("promptbase.scenesRange",
        { min: sc.min, typ: sc.typ, max: sc.max }, ""))}${
        c.needs_lyrics ? " · " + esc(T("promptbase.needsLyrics", "нужен текст трека")) : ""}</p>
      <dl class="lb-prompts pb-script-facts">
        <dt>${esc(T("promptbase.hero", "Герой"))}</dt><dd>${esc(c.hero)}</dd>
        <dt>${esc(T("promptbase.motif", "Сквозной мотив"))}</dt><dd>${esc(c.motif)}</dd>
        <dt>${esc(T("promptbase.opens", "Открывается"))}</dt><dd>${esc(c.opens)}</dd>
        <dt>${esc(T("promptbase.closes", "Закрывается"))}</dt><dd>${esc(c.closes)}</dd>
      </dl>
      <ol class="pb-acts">${acts}</ol>
      <p class="lb-slots">${esc(T("promptbase.boardsN", "Раскадровка"))}: ${
        (c.boards || []).map((b) => `<code>${esc(b)}</code>`).join(" ")}</p>
      ${c.locked ? lockedNote(c) : ""}
      <div class="row lb-actions">
        ${c.locked
          ? `<button type="button" class="pb-plans ghost">${esc(T("library.seePlans", ""))}</button>`
          : `<button type="button" class="pb-apply">${
              esc(T("promptbase.applyScript", "Применить к треку"))}</button>`}
      </div>
    </article>`;
  }

  /* Прежние слои. Их карточки рисуются здесь же, но применяются диалогами
     sections.js: те же диалоги зовёт артефакт урока, и вторая копия
     разъехалась бы с ним. */
  function shotCard(c) {
    return `<article class="pb-card lb-card" data-kind="shot" data-key="${esc(c.key)}">
      ${head(c, "shot")}
      <p class="lb-desc">${esc(c.desc)}</p>
      <p class="lb-gain"><b>${esc(T("library.gain", "Что даёт"))}:</b> ${esc(c.gain)}</p>
      ${c.locked ? lockedNote(c) : prompts([
        [T("library.first", "Первый кадр"), c.first],
        [T("library.last", "Последний кадр"), c.last],
        [T("library.motion", "Движение"), c.motion],
      ])}
      ${slotLine(c)}
      <div class="row lb-actions">
        ${c.locked
          ? `<button type="button" class="pb-plans ghost">${esc(T("library.seePlans", ""))}</button>`
          : `<button type="button" class="pb-apply">${esc(T("library.apply", "Применить"))}</button>
             <button type="button" class="pb-copy ghost">${esc(T("library.copy", "Скопировать"))}</button>`}
      </div>
    </article>`;
  }

  function packCard(c) {
    return `<article class="pb-card lb-card" data-kind="pack" data-key="${esc(c.key)}">
      ${head(c, "pack")}
      <p class="lb-desc">${esc(c.desc)}</p>
      <ul class="ac-art-shots">${(c.shots || []).map((s) =>
        `<li>${esc(s.label)}</li>`).join("")}</ul>
      <div class="row lb-actions">
        ${c.locked
          ? `<button type="button" class="pb-plans ghost">${esc(T("library.seePlans", ""))}</button>`
          : `<button type="button" class="pb-apply">${
              esc(T("academy.apply", "Применить в проект"))}</button>`}
      </div>
    </article>`;
  }

  /* ─────────────────────────── обработчики списка ─────────────────────────── */

  function cardOf(node) {
    const art = node.closest(".pb-card");
    const kind = art.dataset.kind;
    const key = art.dataset.key;
    const pool = { board: "boards", motion: "motions", light: "lights",
                   script: "scripts", shot: "shots", pack: "packs" }[kind];
    return { kind: kind, card: (data[pool] || []).find((x) => x.key === key) };
  }

  function bind(list) {
    $$(".pb-apply", list).forEach((b) => b.addEventListener("click", () => {
      const { kind, card } = cardOf(b);
      if (!card) return;
      if (kind === "script") { applyScript(card); return; }
      if (kind === "pack") { if (UI().applyPack) UI().applyPack(card); return; }
      if (kind === "shot") { if (UI().applyShot) UI().applyShot(card); return; }
      // Одиночная карточка — это сборка из одной карточки. Отдельного пути
      // «применить без сборки» нет намеренно: он был бы вторым местом, которое
      // знает про поля кадра и про подстановку слотов.
      sel = { board: "", motion: "", lights: [] };
      pick(kind, card.key);
      openApply();
    }));
    $$(".pb-pick", list).forEach((b) => b.addEventListener("click", () => {
      const { kind, card } = cardOf(b);
      if (card) pick(kind, card.key);
    }));
    $$(".pb-copy", list).forEach((b) => b.addEventListener("click", async () => {
      const { card } = cardOf(b);
      if (!card) return;
      const text = [card.first, card.last, card.motion, card.text, card.solo,
                    card.add, card.negative].filter(Boolean).join("\n\n");
      try {
        await navigator.clipboard.writeText(text);
        b.textContent = T("library.copied", "скопировано");
        setTimeout(() => { b.textContent = T("library.copy", "Скопировать"); }, 1400);
      } catch (e) { toast(T("common.copyManual", "")); }
    }));
    $$(".pb-plans", list).forEach((b) => b.addEventListener("click", () => {
      if (typeof window.openAccountModal === "function") window.openAccountModal("plan");
    }));
  }

  /* ─────────────────────────────── сборка ─────────────────────────────── */

  function isPicked(kind, key) {
    if (kind === "board") return sel.board === key;
    if (kind === "motion") return sel.motion === key;
    if (kind === "light") return sel.lights.includes(key);
    return false;
  }

  /* Потолки MIX_RULES соблюдаем ЗДЕСЬ ЖЕ, а не сообщением об ошибке после
     нажатия: заготовка и движение вытесняют предыдущие (их по одному на сцену),
     свет копится до потолка и дальше выталкивает самый старый. Так «положить в
     сборку» никогда не отвечает «нельзя» — оно всегда что-то делает. */
  function pick(kind, key) {
    const max = (data.mix_rules && data.mix_rules.max_lights) || 2;
    if (kind === "board") sel.board = sel.board === key ? "" : key;
    else if (kind === "motion") sel.motion = sel.motion === key ? "" : key;
    else if (kind === "light") {
      const i = sel.lights.indexOf(key);
      if (i >= 0) sel.lights.splice(i, 1);
      else {
        sel.lights.push(key);
        while (sel.lights.length > max) sel.lights.shift();
      }
    } else return;
    paintList();
    paintMix();
    checkMix();
  }

  function empty() { return !sel.board && !sel.motion && !sel.lights.length; }

  function cardBy(kind, key) {
    const pool = { board: "boards", motion: "motions", light: "lights" }[kind];
    return (data[pool] || []).find((x) => x.key === key) || null;
  }

  function chip(kind, key) {
    const c = cardBy(kind, key);
    return `<button type="button" class="pb-chip" data-kind="${kind}" data-key="${esc(key)}"
      title="${esc(T("promptbase.unpick", "убрать"))}"
      ><span class="pb-chip-k">${esc(T("promptbase.chip." + kind, kind))}</span>${
      esc((c && c.label) || key)}<span class="pb-chip-x">✕</span></button>`;
  }

  function paintMix() {
    const box = $(".pb-mix", root);
    if (!box) return;
    if (empty()) { box.innerHTML = ""; box.classList.remove("on"); return; }
    box.classList.add("on");
    const chips = [
      sel.board ? chip("board", sel.board) : "",
      sel.motion ? chip("motion", sel.motion) : "",
      ...sel.lights.map((k) => chip("light", k)),
    ].filter(Boolean).join("");
    // Конфликт объясняется СЛОВАМИ и не запрещает применить. Мы говорим, чем
    // человек платит, а не решаем за него.
    const warn = conflicts.length
      ? `<ul class="pb-conf">${conflicts.map((c) =>
          `<li>${esc(c[lang()] || c.en)}</li>`).join("")}</ul>`
      : "";
    box.innerHTML = `
      <div class="pb-mix-head">
        <b>${esc(T("promptbase.mix", "Сборка"))}</b>
        <span class="muted">${esc((data.mix_rules && data.mix_rules.note) || "")}</span>
      </div>
      <div class="pb-chips">${chips}</div>
      ${warn}
      <div class="row pb-mix-actions">
        <button type="button" class="primary pb-mix-go">${
          esc(ctx && ctx.kind === "make"
            ? T("promptbase.toMake", "Вставить в генератор")
            : T("promptbase.applyMix", "Применить сборку"))}</button>
        <button type="button" class="ghost pb-mix-clear">${
          esc(T("promptbase.clear", "Очистить"))}</button>
      </div>`;
    $$(".pb-chip", box).forEach((b) => b.addEventListener("click", () =>
      pick(b.dataset.kind, b.dataset.key)));
    $(".pb-mix-go", box).addEventListener("click", openApply);
    $(".pb-mix-clear", box).addEventListener("click", () => {
      sel = { board: "", motion: "", lights: [] };
      conflicts = [];
      paintList();
      paintMix();
    });
  }

  /* Конфликты считает СЕРВЕР: правило «камера стоит и камера едет» живёт рядом
     с карточками, и вторая его копия в браузере устарела бы на первой новой
     черте. Роут нарочно без замка по тарифу — объяснение, почему не стоит,
     это не товар. */
  async function checkMix() {
    if (empty()) { conflicts = []; return; }
    const want = JSON.stringify(sel);
    try {
      const res = await api("/api/mix/check", { method: "POST", body: sel });
      if (want !== JSON.stringify(sel)) return;   // выбор успел смениться
      conflicts = res.conflicts || [];
    } catch (e) {
      conflicts = [];
    }
    paintMix();
  }

  /* ────────────────────── диалог применения сборки ────────────────────── */

  function fieldLabel(f) {
    return T("promptbase.field." + f, f);
  }

  /* Слоты берём у ВСЕХ выбранных карточек и показываем по одному разу: два
     поля «персонаж» в одном диалоге — это два места, где можно ошибиться. */
  function neededSlots() {
    const out = [];
    const seen = new Set();
    [cardBy("board", sel.board), cardBy("motion", sel.motion),
      ...sel.lights.map((k) => cardBy("light", k))].forEach((c) => {
      (c && c.slots || []).forEach((s) => {
        if (!seen.has(s.key)) { seen.add(s.key); out.push(s); }
      });
    });
    return out;
  }

  function openApply() {
    if (empty()) return;
    if (typeof window.openModal !== "function") return;
    const toMake = ctx && ctx.kind === "make";
    const tracks = tracksOf();
    if (!toMake && !tracks.length) { toast(T("academy.noTracks", "")); return; }

    window.openModal(T("promptbase.applyTitle", "Применить сборку"), (box) => {
      const preTrack = preferredTrack(tracks);
      box.innerHTML = `
        ${toMake ? `<p class="muted">${esc(T("promptbase.toMakeHint", ""))}</p>` : `
        <label class="ac-slot"><span>${esc(T("academy.chooseTrack", "Куда применить"))}</span>
          <select class="ac-track">${tracks.map((tr) => `
            <option value="${tr.id}"${tr.id === preTrack ? " selected" : ""}>${
              esc(tr.title || ("#" + tr.id))}</option>`).join("")}</select></label>
        <label class="ac-slot"><span>${esc(T("library.chooseScene", "На какую сцену"))}</span>
          <select class="ac-scene">${sceneOptions(
            tracks.find((x) => x.id === preTrack) || tracks[0])}</select></label>`}
        <div class="ac-slots">${neededSlots().map((s) => `
          <label class="ac-slot">
            <span>${esc(s.label)}</span>
            <input data-slot="${esc(s.key)}" value="${esc(slots[s.key] || "")}"
                   placeholder="${esc(s.example || "")}" title="${esc(s.hint || "")}" />
          </label>`).join("")}</div>
        <p class="muted ac-slot-note">${esc(T("academy.slotsNote", ""))}</p>
        <div class="pb-diff"></div>
        <div class="row"><button type="button" class="primary pb-go">${
          esc(toMake ? T("promptbase.toMake", "Вставить в генератор")
                     : T("library.apply", "Применить"))}</button>
          <span class="status pb-status"></span></div>`;

      const sceneSel = $(".ac-scene", box);
      const trackSel = $(".ac-track", box);
      if (trackSel) {
        trackSel.addEventListener("change", () => {
          const tr = tracks.find((x) => x.id === Number(trackSel.value));
          sceneSel.innerHTML = sceneOptions(tr);
          refresh();
        });
        if (ctx && ctx.kind === "scene") preselectScene(tracks, trackSel, sceneSel);
      }
      if (sceneSel) sceneSel.addEventListener("change", refresh);
      let timer = 0;
      $$("[data-slot]", box).forEach((i) => i.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(refresh, 350);
      }));

      let last = null;

      function readSlots() {
        const out = {};
        $$("[data-slot]", box).forEach((i) => {
          const v = String(i.value || "").trim();
          if (v) out[i.dataset.slot] = v;
        });
        return out;
      }

      /* Предпросмотр НЕ рисуется в браузере: он приходит с сервера тем же
         вызовом, который потом и запишут. Собери его здесь — и однажды человек
         увидит одно, а сохранит другое. */
      async function refresh() {
        const diff = $(".pb-diff", box);
        diff.innerHTML = `<p class="muted">${esc(T("common.loading", "…"))}</p>`;
        const body = { ...sel, slots: readSlots() };
        const sid = sceneSel ? Number(sceneSel.value) || 0 : 0;
        if (sid) body.scene_id = sid;
        try {
          last = await api("/api/mix", { method: "POST", body: body });
        } catch (e) {
          last = null;
          diff.innerHTML = `<p class="mk-error">${esc(errorText(e))}</p>`;
          return;
        }
        diff.innerHTML = diffHtml(last, Boolean(sid));
        const cp = $(".pb-neg-copy", diff);
        if (cp) cp.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(last.negative || "");
            cp.textContent = T("library.copied", "скопировано");
          } catch (e) { toast(T("common.copyManual", "")); }
        });
      }

      $(".pb-go", box).addEventListener("click", async () => {
        const st = $(".pb-status", box);
        if (!last) { st.textContent = T("promptbase.notReady", ""); return; }
        slots = { ...slots, ...readSlots() };
        if (toMake) {
          // Свет в одиночку кадра не описывает — тогда вставляем его хвост как
          // есть. Пустая строка ввода после нажатия «вставить» была бы худшим
          // из ответов: кнопка сработала, а результата нет.
          const text = [last.scene.image_prompt, last.scene.motion_prompt]
            .filter(Boolean).join("\n\n") || last.add || "";
          if (typeof window.mkInsertPrompt === "function") window.mkInsertPrompt(text);
          else { toast(T("promptbase.noMake", "")); return; }
          window.closeModal();
          toast(T("promptbase.inserted", ""));
          return;
        }
        const sid = Number(sceneSel.value) || 0;
        if (!sid) { st.textContent = T("academy.needScenes", ""); return; }
        st.textContent = T("common.saving", "…");
        try {
          // Пишет обычный PATCH сцены, и только теми полями, которые реально
          // меняются: отправлять все шесть значило бы «сохранить» пять
          // нетронутых и затереть чужую правку, сделанную между показом и
          // нажатием.
          const patch = {};
          (last.changed || Object.keys(last.scene)).forEach((f) => {
            patch[f] = last.scene[f];
          });
          await api(`/api/scenes/${sid}`, { method: "PATCH", body: patch });
          if (typeof window.loadProject === "function") await window.loadProject();
          toast(TF("promptbase.appliedScene", { n: (last.changed || []).length }, ""));
          window.closeModal();
        } catch (e) {
          st.textContent = errorText(e);
        }
      });

      refresh();
    }, { wide: true });
  }

  /* Кадр, из которого пришли, должен быть выбран сразу: человек нажал «каталог»
     НА КАДРЕ, и предлагать ему найти этот кадр в списке заново — значит сделать
     вид, что мы не знаем, откуда он. */
  function preselectScene(tracks, trackSel, sceneSel) {
    const sid = ctx.sceneId;
    const tr = tracks.find((x) => (x.scenes || []).some((s) => s.id === sid));
    if (!tr) return;
    trackSel.value = String(tr.id);
    sceneSel.innerHTML = sceneOptions(tr);
    sceneSel.value = String(sid);
  }

  function preferredTrack(tracks) {
    if (ctx && ctx.kind === "scene") {
      const tr = tracks.find((x) => (x.scenes || []).some((s) => s.id === ctx.sceneId));
      if (tr) return tr.id;
    }
    return tracks.length ? tracks[0].id : 0;
  }

  function short(v) {
    const s = String(v == null ? "" : v);
    return s.length > 320 ? s.slice(0, 320) + "…" : s;
  }

  function diffHtml(res, hasScene) {
    const changed = res.changed || Object.keys(res.scene || {});
    const rows = Object.keys(res.scene || {}).map((f) => {
      const was = res.before ? String(res.before[f] || "") : "";
      const now = String(res.scene[f] || "");
      const same = hasScene && was === now;
      return `<div class="pb-row${same ? " same" : ""}">
        <span class="pb-row-f">${esc(fieldLabel(f))}</span>
        ${same
          ? `<span class="pb-row-same muted">${esc(T("promptbase.unchanged", "не меняется"))}</span>`
          : `${was ? `<span class="pb-was">${esc(short(was))}</span>` : ""}
             <span class="pb-now">${esc(short(now))}</span>`}
      </div>`;
    }).join("");
    const conf = (res.conflicts || []).length
      ? `<ul class="pb-conf">${res.conflicts.map((c) =>
          `<li>${esc(c[lang()] || c.en)}</li>`).join("")}</ul>` : "";
    // Негатив НЕ пишется в кадр, потому что поля под него в базе нет: его
    // читает отдельный канал движка. Говорим это прямо и даём скопировать —
    // молча положить его в промпт значило бы отправить запреты как описание.
    const neg = res.negative
      ? `<div class="pb-neg">
          <b>${esc(T("promptbase.negative", "Запреты"))}</b>
          <p>${esc(res.negative)}</p>
          <p class="muted">${esc(T("promptbase.negativeNote", ""))}</p>
          <button type="button" class="ghost pb-neg-copy">${
            esc(T("library.copy", "Скопировать"))}</button>
        </div>` : "";
    // Ни одного поля — значит выбран только свет и накладывать его не на что.
    // Показываем сам хвост: «ничего не изменится» здесь было бы неправдой, а
    // пустая рамка — молчанием.
    const bare = !rows
      ? `<div class="pb-row"><span class="pb-row-f">${esc(T("promptbase.add", ""))}</span>
         <span class="pb-now">${esc(short(res.add))}</span></div>`
      : "";
    return `<div class="pb-diff-in">
      <p class="pb-diff-head">${esc(hasScene
        ? TF("promptbase.willChange", { n: changed.length }, "")
        : T("promptbase.willWrite", ""))}</p>
      ${conf}${rows || bare}${neg}</div>`;
  }

  /* ────────────────────── диалог применения сценария ────────────────────── */

  /* Сценарий кладётся в ТРИ разных места, и все три показываются заранее:
     сюжет — свойство проекта, заметка — свойство трека, раскадровка — кадры.
     Сюжет проекта общий на все треки, поэтому перезапись существующего
     спрашивается отдельной галочкой, а не делается молча. */
  function applyScript(card) {
    const tracks = tracksOf();
    if (!tracks.length) { toast(T("academy.noTracks", "")); return; }
    if (typeof window.openModal !== "function") return;
    window.openModal(card.label, (box) => {
      box.innerHTML = `<p class="muted">${esc(T("common.loading", "…"))}</p>`;
      api(`/api/scripts/${encodeURIComponent(card.key)}/apply`,
          { method: "POST", body: { slots: slots } })
        .then((res) => build(res))
        .catch((e) => { box.innerHTML = `<p class="mk-error">${esc(errorText(e))}</p>`; });

      function build(res) {
        const p = (typeof project !== "undefined" && project) || {};
        const curStory = String(p.story || "").trim();
        box.innerHTML = `
          <label class="ac-slot"><span>${esc(T("academy.chooseTrack", "Куда применить"))}</span>
            <select class="ac-track">${tracks.map((tr) => `
              <option value="${tr.id}">${esc(tr.title || ("#" + tr.id))} · ${
                esc(TF("promptbase.scenesHave", { n: (tr.scenes || []).length }, ""))}</option>`
              ).join("")}</select></label>
          <div class="pb-diff-in">
            <p class="pb-diff-head">${esc(T("promptbase.scriptWill", ""))}</p>
            <div class="pb-row">
              <span class="pb-row-f">${esc(T("promptbase.field.story", "Сюжет проекта"))}</span>
              ${curStory ? `<span class="pb-was">${esc(short(curStory))}</span>` : ""}
              <span class="pb-now">${esc(short(res.story))}</span>
            </div>
            <div class="pb-row">
              <span class="pb-row-f">${esc(T("promptbase.field.note", "Заметка режиссёра"))}</span>
              <span class="pb-now">${esc(short(res.note))}</span>
            </div>
            <div class="pb-row">
              <span class="pb-row-f">${esc(T("promptbase.field.boards", "Раскадровка"))}</span>
              <span class="pb-now">${esc(TF("promptbase.boardsWill",
                { n: (res.scenes || []).length }, ""))}</span>
            </div>
          </div>
          ${curStory ? `<label class="pb-check">
            <input type="checkbox" class="pb-over" />
            <span>${esc(T("promptbase.overwriteStory", ""))}</span></label>` : ""}
          <label class="pb-check">
            <input type="checkbox" class="pb-lay" checked />
            <span>${esc(T("promptbase.layBoards", ""))}</span></label>
          <div class="row"><button type="button" class="primary pb-go">${
            esc(T("library.apply", "Применить"))}</button>
            <span class="status pb-status"></span></div>`;

        $(".pb-go", box).addEventListener("click", async () => {
          const st = $(".pb-status", box);
          const trackId = Number($(".ac-track", box).value) || 0;
          const track = tracks.find((x) => x.id === trackId);
          const over = $(".pb-over", box);
          const lay = $(".pb-lay", box);
          st.textContent = T("common.saving", "…");
          try {
            if (!curStory || (over && over.checked)) {
              await api("/api/project", { method: "PATCH", body: { story: res.story } });
            }
            await api(`/api/tracks/${trackId}`,
                      { method: "PATCH", body: { director_note: res.note } });
            let n = 0;
            if (lay && lay.checked) {
              const scenes = (track && track.scenes) || [];
              const patches = res.scenes || [];
              // Заготовок в сценарии может быть больше, чем кадров в треке, —
              // применяем сколько влезло и ГОВОРИМ ОБ ЭТОМ вслух. Молча
              // обрезать значит отчитаться «применилось» о половине работы.
              n = Math.min(scenes.length, patches.length);
              for (let i = 0; i < n; i += 1) {
                await api(`/api/scenes/${scenes[i].id}`,
                          { method: "PATCH", body: patches[i] });
              }
              if (n < patches.length) {
                toast(TF("promptbase.laidPart",
                  { n: n, total: patches.length }, ""));
              }
            }
            if (typeof window.loadProject === "function") await window.loadProject();
            toast(TF("promptbase.scriptApplied", { n: n }, ""));
            window.closeModal();
          } catch (e) {
            st.textContent = errorText(e);
          }
        });
      }
    }, { wide: true });
  }

  window.QlolLibrary = { render, forget, pick };
})();
