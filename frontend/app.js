// ═════════════════════════ КАТАЛОГ СТИЛЕЙ (КЛИЕНТ) ═════════════════════════
// ЗДЕСЬ БОЛЬШЕ НЕТ НИ ОДНОГО ПРОМПТА. Раньше в этом файле лежал массив
// STYLE_PRESETS с полными текстами пятнадцати пресетов — и уезжал каждому
// гостю вместе с бандлом. Шесть из них сняты покадровым разбором виральных
// аккаунтов и являются единственным рвом сервиса; конкурент вставлял их себе
// за минуту, просто открыв исходник страницы.
//
// Теперь реестр живёт на сервере (backend/prompts_catalog.py):
//   GET  /api/styles                    — карточки БЕЗ текста промпта
//   POST /api/tracks/{id}/style         — клиент шлёт КЛЮЧИ, текст собирает сервер
// Вторая половина той же дыры закрыта в track_dict: /api/tracks отдаёт
// style_keys и подпись, а не сам промпт.

let stylesCatalog = null;          // ответ /api/styles, грузится один раз
let stylesCatalogLang = "";        // на каком языке загружен — при смене перегружаем
let stylesCatalogPromise = null;   // склейка параллельных запросов

async function loadStyles(force) {
  if (!force && stylesCatalog && stylesCatalogLang === LANG) return stylesCatalog;
  if (stylesCatalogPromise && stylesCatalogLang === LANG && !force) return stylesCatalogPromise;
  stylesCatalogLang = LANG;
  stylesCatalogPromise = (async () => {
    try {
      stylesCatalog = await api(`/api/styles?lang=${encodeURIComponent(LANG)}`);
    } catch (e) {
      // Каталог не приехал — пикер обязан сказать об этом, а не молча
      // показать пустоту: без стилей раскадровка не запускается вообще.
      stylesCatalog = { styles: [], groups: [], collections: [], presets: [], max_mix: 3, failed: true };
    }
    return stylesCatalog;
  })();
  return stylesCatalogPromise;
}

function styleByKey(key) {
  return (stylesCatalog && stylesCatalog.styles || []).find((s) => s.key === key) || null;
}

// Подписи приезжают с сервера уже на нужном языке (в них живёт и «★ основа»),
// но словарь фронта остаётся запасным вариантом для старых ключей.
function styleLabel(s) {
  return (s && s.label) || t(`styles.${s && s.key}.label`) || (s && s.key) || "";
}
function styleDesc(s) {
  return (s && s.desc) || t(`styles.${s && s.key}.desc`) || "";
}

// Микс стилей: 1–3 пресета чекбокс-чипами, ПЕРВЫЙ выбранный — основа.
// Порядок решает всё (основа идёт в промпт целиком, остальные выжимками),
// поэтому основа помечена звёздочкой и в чипах, и в подписи.
//
// onChange(keys) отдаёт МАССИВ КЛЮЧЕЙ, а не текст: сохранением занимается
// вызывающий через POST /api/tracks/{id}/style либо через поле формы.
function buildStylePicker(container, currentKeys, onChange) {
  container.innerHTML = "";
  const order = Array.isArray(currentKeys)
    ? currentKeys.slice()
    : String(currentKeys || "").split(",").filter(Boolean);

  const chipsBox = document.createElement("div");
  chipsBox.className = "style-chips";
  const desc = document.createElement("details");
  desc.className = "style-desc";
  const descSummary = document.createElement("summary");
  descSummary.textContent = t("stylePicker.descSummary");
  const descBody = document.createElement("div");
  descBody.className = "style-desc-body muted";
  desc.append(descSummary, descBody);

  const render = (list, maxMix) => {
    chipsBox.innerHTML = "";
    const sync = (fireChange) => {
      $$(".style-chip", chipsBox).forEach((el) => {
        const on = order.includes(el.dataset.key);
        el.classList.toggle("on", on);
        el.classList.toggle("base", order[0] === el.dataset.key);
        const cb = el.querySelector("input");
        if (cb) cb.checked = on;
      });
      const chosen = order.map(styleByKey).filter(Boolean);
      descBody.textContent = chosen.length
        ? chosen.map((s, i) => `${i ? "＋" : "★"} ${styleLabel(s)} — ${styleDesc(s)}`).join("\n")
        : t("stylePicker.none");
      desc.classList.toggle("hidden", !chosen.length);
      if (fireChange) onChange(order.slice());
    };

    for (const s of list) {
      const label = document.createElement("label");
      label.className = "style-chip" + (s.locked ? " locked" : "");
      label.dataset.key = s.key;
      label.title = s.locked ? t("stylePicker.locked") : styleDesc(s);
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.disabled = Boolean(s.locked);
      cb.addEventListener("change", () => {
        if (cb.checked) {
          if (order.length >= maxMix) { cb.checked = false; return; }
          order.push(s.key);
        } else {
          const i = order.indexOf(s.key);
          if (i >= 0) order.splice(i, 1);
        }
        sync(true);
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(styleLabel(s)));
      // Замок — честная метка тарифа, а не выключенная кнопка без объяснения.
      if (s.locked) {
        const lock = document.createElement("span");
        lock.className = "style-chip-lock";
        lock.textContent = "🔒";
        label.appendChild(lock);
      }
      chipsBox.appendChild(label);
    }
    sync(false);
  };

  container.appendChild(chipsBox);
  container.appendChild(desc);

  loadStyles().then((cat) => {
    if (cat.failed) {
      chipsBox.innerHTML = `<span class="muted">${escHtml(t("stylePicker.failed"))}</span>`;
      return;
    }
    render(cat.styles || [], Number(cat.max_mix) || 3);
  });
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Ошибка API целиком: структурный код ({"error":"not_enough_points", need, have}),
// человеческий detail и всё тело ответа — из них errText() собирает сообщение
// на языке интерфейса.
class ApiError extends Error {
  constructor(body, status) {
    const detail = (body && (body.detail || body.message)) || `HTTP ${status}`;
    super(String(detail));
    this.status = status;
    this.code = (body && body.error) || "";
    this.data = body || {};
  }
}

// Текст ошибки на языке интерфейса. Порядок: известный код бэкенда → карта
// русских фраз (бэкенд отвечает по-русски) → как пришло. Незнакомый код НЕ
// превращается в пустоту и не роняет экран.
function errText(e) {
  if (!e) return t("errors.generic");
  const code = e.code || "";
  if (code && tHas(`errors.codes.${code}`)) {
    const d = e.data || {};
    const need = Number(d.need) || 0, have = Number(d.have) || 0;
    return t(`errors.codes.${code}`, {
      need: tNum(need), have: tNum(have),
      short: tNum(d.short != null ? Number(d.short) : Math.max(0, need - have)),
      plan: d.plan || "",
    });
  }
  const raw = String((e && e.message) || "").trim();
  if (!raw) return t("errors.generic");
  if (LANG !== "ru" && ERR_RU_TO_EN[raw]) return ERR_RU_TO_EN[raw];
  return raw;
}

// Единая точка «не получилось»: alert с переведённым текстом.
function fail(e) { alert(errText(e)); }

// Внутри мини-аппа Telegram сессия едет ещё и заголовком: в Desktop и Web
// мини-апп живёт в iframe, запросы кросс-сайтовые, и кука с SameSite=Lax до
// нас не доезжает — человек оставался бы вечно неавторизованным.
function apiHeaders(opts) {
  const h = {};
  if (opts.body && !(opts.body instanceof FormData)) h["content-type"] = "application/json";
  const tg = window.TGA && TGA.active ? TGA.authHeader() : null;
  if (tg) Object.assign(h, tg);
  return Object.keys(h).length ? h : undefined;
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: apiHeaders(opts),
      body: opts.body instanceof FormData ? opts.body : opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new ApiError({ error: "network" }, 0);
  }
  if (res.status === 401) {
    // В Telegram формы входа нет и быть не может: человека уже опознала
    // платформа. Протухшая сессия чинится молча — переспрашиваем Telegram и
    // повторяем запрос ОДИН раз, чтобы не уйти в бесконечный круг.
    if (window.TGA && TGA.active && !opts._retry) {
      try {
        await TGA.signIn(true);
        return await api(path, { ...opts, _retry: true });
      } catch (e) { /* не опознал — падаем ниже общим путём */ }
    }
    if (!(window.TGA && TGA.active)) showLogin();
    throw new ApiError({ error: "unauthorized" }, 401);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // Блокировку ловим ЗДЕСЬ, а не в каждой кнопке: заблокировать могут
    // посреди сессии, и тогда человек увидит не экран с причиной, а
    // случайную кнопку, которая «почему-то не работает».
    if (res.status === 403 && body && body.error === "blocked") {
      showBlocked(body.detail);
    }
    throw new ApiError(body, res.status);
  }
  return res.status === 204 ? null : res.json();
}

// me — текущий пользователь: бейдж очков и кнопка «Сохранить аккаунт» живут от него.
let me = { authed: false, user: null };

// ────────── реферальная метка ?ref=КОД ──────────
// По ссылке амбассадора человек попадает на лендинг и жмёт «Старт» уже без
// параметра в адресе, поэтому код сразу кладём в sessionStorage: иначе привязка
// к амбассадору терялась бы на первом же клике.
const REF_KEY = "qv_ref";

function stripQueryParam(name) {
  try {
    const url = new URL(location.href);
    if (!url.searchParams.has(name)) return;
    url.searchParams.delete(name);
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  } catch (e) { /* старый браузер — адрес останется как есть */ }
}

function pickRefCode() {
  try {
    // В Telegram ссылки с ?ref= не бывает: ссылка амбассадора выглядит как
    // t.me/<bot>/app?startapp=ref_КОД, и код приезжает в start_param. Без
    // этой ветки реферал терялся бы на входе в мини-апп.
    const fromTg = window.TGA && TGA.active ? (TGA.refFromStart() || "") : "";
    const fromUrl = fromTg || (new URLSearchParams(location.search).get("ref") || "").trim();
    if (fromUrl) {
      sessionStorage.setItem(REF_KEY, fromUrl.slice(0, 32));
      // Адрес чистим: код уже сохранён, светить его в строке незачем.
      stripQueryParam("ref");
    }
    return sessionStorage.getItem(REF_KEY) || "";
  } catch (e) {
    return "";  // приватный режим без sessionStorage — просто работаем без реферала
  }
}

const refCode = pickRefCode();

// Плашка на главной: пришедший по приглашению должен видеть, что скидка его
// ждёт. Текст — из словаря (landing.hero.refBanner), код промокода экранируется.
function renderRefBanner() {
  const el = $("#welcome-ref");
  if (!el) return;
  el.classList.toggle("hidden", !refCode);
  if (refCode) {
    el.innerHTML = LTX("hero.refBanner",
      { code: `<b>${escHtml(refCode)}</b>`, discount: LD_REF.discount });
  }
}

// Экранов теперь четыре: лендинг, вход, студия и чат. Гасим все разом, чтобы
// добавление пятого не требовало править каждый переход по отдельности.
function hideScreens() {
  ["#welcome", "#login", "#app", "#chat", "#blocked"].forEach((sel) => {
    const el = $(sel);
    if (el) el.classList.add("hidden");
  });
}

// Экран блокировки. Заблокированному отвечают 403 ВСЕ рабочие роуты, а
// /api/me намеренно идёт мимо гварда (иначе вместе с приложением падал бы и
// вход) — поэтому показать блокировку должен фронт, и показать ПРИЧИНУ:
// «аккаунт заблокирован» без причины — это та же тишина, только вежливая.
function showBlocked(reason) {
  hideScreens();
  const box = $("#blocked");
  if (!box) return;
  const why = $("#blocked-reason");
  if (why) {
    const text = String(reason || "").trim();
    why.textContent = text;
    why.classList.toggle("hidden", !text);
  }
  box.classList.remove("hidden");
}

function showWelcome() {
  renderRefBanner();
  hideScreens();
  $("#welcome").classList.remove("hidden");
  // Главная — полноценная витрина: тексты, шаги, тарифы и шкала очков
  // собираются в renderLanding() (низ файла) из словаря I18N (i18n.js).
  renderLanding();
}
function showLogin() {
  hideScreens();
  $("#login").classList.remove("hidden");
  // Кнопки Telegram/Яндекс/Google. Заголовок «или войти через» показываем
  // только если хоть один вход настроен — иначе он висел бы над пустотой.
  renderAuthButtons($("#login-auth"), { mode: "login", column: true }).then((n) => {
    const label = $("#login-auth-label");
    if (label) label.classList.toggle("hidden", !n);
  });
}
function showApp() {
  // Блокировку проверяем ДО показа студии, а не по первому упавшему запросу:
  // иначе заблокированный успевает увидеть свои проекты и понажимать кнопки.
  const who = me && me.user;
  if (who && who.is_blocked) {
    showBlocked(who.blocked_reason);
    return;
  }
  hideScreens();
  $("#app").classList.remove("hidden");
  renderUserBar();
  loadProject();
  // Прямая ссылка /#/chat открывает чат сразу. Проект при этом всё равно
  // грузится фоном: «Сохранить в проект» нужен список персонажей.
  if (location.hash === "#/chat") showChat();
}

// Бейдж «⚡ N» (не-админу), «Кабинет» и «Сохранить аккаунт» (гостю без логина) в топбаре.
function renderUserBar() {
  const u = me && me.user;
  const badge = $("#points-badge");
  const saveBtn = $("#save-account-btn");
  const accBtn = $("#account-btn");
  if (!u) {
    badge.classList.add("hidden");
    saveBtn.classList.add("hidden");
    if (accBtn) accBtn.classList.add("hidden");
    return;
  }
  // Кабинет открыт всем, включая гостя: тариф и партнёрка живут на его id.
  if (accBtn) accBtn.classList.remove("hidden");
  badge.classList.toggle("hidden", Boolean(u.is_admin));
  badge.textContent = `${tNum(u.gen_points)} ${t("top.pointsUnit")}`;
  // Тариф видно сразу: на free видео рисует Grok, Seedance открыт на pro.
  let planBadge = $("#plan-badge");
  if (!planBadge) {
    planBadge = document.createElement("span");
    planBadge.id = "plan-badge";
    planBadge.className = "kind-badge";
    badge.after(planBadge);
  }
  const pro = Boolean(u.is_admin) || u.plan === "pro" || u.plan === "pro_max";
  planBadge.textContent = pro ? t("top.planPro", { plan: u.plan_title || "PRO" }) : t("top.planFree");
  planBadge.title = pro ? t("top.planProTitle") : t("top.planFreeTitle");
  planBadge.style.opacity = pro ? "1" : ".7";
  saveBtn.classList.toggle("hidden", Boolean(u.is_admin) || Boolean(u.login));
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = $("#login-password").value;
  const loginName = $("#login-login").value.trim();
  try {
    // Пусто в логине = легаси-вход владельца одним паролем, как раньше.
    await api("/api/login", { method: "POST", body: loginName ? { login: loginName, password } : { password } });
    me = await api("/api/me");
    showApp();
  } catch (err) {
    $("#login-error").textContent = t("auth.fail");
    $("#login-error").classList.remove("hidden");
  }
});

// «Старт» = гостевой аккаунт сразу: без формы, регистрация — потом, по желанию.
// ?ref= с реферальной ссылки уезжает тем же запросом — гость закрепляется
// за амбассадором ещё до первой оплаты.
// Вернувшемуся с живой сессией новый аккаунт НЕ заводим: /api/start всегда
// создаёт свежего гостя и перезаписывает cookie — человек потерял бы проекты.
async function ldStart() {
  if (!(me && me.authed)) {
    await api("/api/start" + (refCode ? `?ref=${encodeURIComponent(refCode)}` : ""), { method: "POST" });
    me = await api("/api/me");
  }
  showApp();
  // Раздел «Промты» заканчивается действием: выбранный стиль или каркас
  // применяется к треку сразу, а не остаётся картинкой на витрине.
  await ldApplyPending();
}

// Применить выбор с карточки промта к первому треку проекта. Трека нет —
// запоминаем и применим, когда он появится (loadProject зовёт нас снова).
async function ldApplyPending() {
  if (!ldPending) return;
  try {
    const p = project || await api("/api/project");
    const tr = (p.tracks || [])[0];
    if (!tr) return;                     // трека ещё нет — ждём загрузки аудио
    const body = ldPending.style
      ? { style_keys: [ldPending.style] }
      : { preset: ldPending.preset };
    const want = ldPending.style || "";
    ldPending = null;
    const res = await api(`/api/tracks/${tr.id}/style`, { method: "POST", body });
    if (want && !(res.style_keys || []).includes(want)) {
      // Сервер выбросил ключ — он закрыт тарифом. Говорим об этом вслух.
      fail(new Error(LTX("prompts.useLockedNote")));
    }
    await loadProject();
  } catch (e) {
    ldPending = null;
    fail(e);
  }
}

$("#welcome-start").addEventListener("click", ldStart);

$("#welcome-login").addEventListener("click", showLogin);

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  me = { authed: false, user: null };
  showWelcome();
});

$("#account-btn").addEventListener("click", () => openAccountModal("account"));

// Гость превращается в постоянный аккаунт: тот же user id, проекты остаются.
$("#save-account-btn").addEventListener("click", () => {
  openModal(t("modal.saveAccount.title"), (body) => {
    body.innerHTML = `
      <p class="muted" style="margin:0 0 10px">${escHtml(t("modal.saveAccount.lead"))}</p>
      <label>${escHtml(t("modal.saveAccount.loginLabel"))}</label>
      <input class="ra-login" autocomplete="username" placeholder="${escHtml(t("modal.saveAccount.loginPh"))}" />
      <label>${escHtml(t("modal.saveAccount.passLabel"))}</label>
      <input class="ra-pass" type="password" autocomplete="new-password" placeholder="${escHtml(t("modal.saveAccount.passPh"))}" />
      <label>${escHtml(t("modal.saveAccount.nameLabel"))}</label>
      <input class="ra-name" placeholder="${escHtml(t("modal.saveAccount.namePh"))}" />
      <div class="row">
        <button type="button" class="primary ra-save">${escHtml(t("common.save"))}</button>
        <span class="ra-error error hidden"></span>
      </div>`;
    const errEl = $(".ra-error", body);
    const saveBtn = $(".ra-save", body);
    const save = async () => {
      saveBtn.disabled = true;
      errEl.classList.add("hidden");
      try {
        await api("/api/register", { method: "POST", body: {
          login: $(".ra-login", body).value.trim(),
          password: $(".ra-pass", body).value,
          name: $(".ra-name", body).value.trim(),
        }});
        me = await api("/api/me");
        renderUserBar();
        closeModal();
      } catch (e) {
        errEl.textContent = errText(e);
        errEl.classList.remove("hidden");
        saveBtn.disabled = false;
      }
    };
    saveBtn.addEventListener("click", save);
    $(".ra-pass", body).addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    $(".ra-login", body).focus();
  });
});

// ────────── общая модалка: оверлей + карточка, закрытие по ✕ / фону / Esc ──────────
// opts.wide — карточка во весь экран (крупный просмотр листа раскадровки).
function openModal(title, buildBody, opts = {}) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  body.innerHTML = "";
  body.removeAttribute("data-char-id");
  const cardEl = $("#modal-overlay .modal-card");
  cardEl.classList.toggle("wide", Boolean(opts.wide));
  // medium — просто шире обычного (720px). Кабинету нужен график по дням,
  // а в 600px он превращается в частокол из чёрточек.
  cardEl.classList.toggle("modal-wide", Boolean(opts.medium));
  buildBody(body);
  $("#modal-overlay").classList.remove("hidden");
  // В Telegram «назад» — системная кнопка клиента, а не наш крестик: свайп
  // и аппаратная кнопка Android ведут именно к ней. Пока модалка открыта,
  // она закрывает модалку, а не всё приложение.
  if (window.TGA && TGA.active) TGA.back(closeModal);
}

function closeModal() {
  $("#modal-overlay").classList.add("hidden");
  $("#modal-overlay .modal-card").classList.remove("wide", "modal-wide");
  const body = $("#modal-body");
  body.innerHTML = "";
  body.removeAttribute("data-char-id");
  if (window.TGA && TGA.active) {
    TGA.back(null);
    TGA.main("");
    TGA.swipeGuard(false);
  }
}

$("#modal-close").addEventListener("click", closeModal);
$("#modal-overlay").addEventListener("click", (e) => {
  if (e.target === $("#modal-overlay")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal-overlay").classList.contains("hidden")) closeModal();
});

// ═══════════ личный кабинет: «Аккаунт», «Тариф», «Амбассадор», «Выплаты» ═══════════
// Кабинет живёт вкладками внутри общей модалки, а не отдельной страницей:
// открытый проект, поллинг статусов и несохранённые поля остаются на месте.

function escHtml(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Деньги на бэке везде в копейках — рубли собираем в одном месте,
// чтобы округления не разъезжались по разным экранам.
function fmtRub(kopeks) {
  const total = Math.round(Number(kopeks) || 0);
  const cop = Math.abs(total) % 100;
  try {
    // Валюта выплат амбассадора — рубли, поэтому символ остаётся при любом
    // языке; меняются разделители и позиция знака (₽ 1,200 против 1 200 ₽).
    return new Intl.NumberFormat(tLocale(), {
      style: "currency", currency: "RUB",
      minimumFractionDigits: cop ? 2 : 0, maximumFractionDigits: cop ? 2 : 0,
    }).format(total / 100);
  } catch (e) { /* без Intl — ручная сборка ниже */ }
  const abs = Math.abs(total);
  const head = String(Math.trunc(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `${total < 0 ? "−" : ""}${head}${cop ? "," + String(cop).padStart(2, "0") : ""} ₽`;
}

function fmtDate(iso, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const p = (n) => String(n).padStart(2, "0");
  try {
    const date = new Intl.DateTimeFormat(tLocale(), {
      day: "2-digit", month: "short", year: "numeric",
    }).format(d);
    return withTime ? `${date}, ${p(d.getHours())}:${p(d.getMinutes())}` : date;
  } catch (e) { /* без Intl — числовой формат ниже */ }
  const base = `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
  return withTime ? `${base} ${p(d.getHours())}:${p(d.getMinutes())}` : base;
}

// navigator.clipboard есть только на https/localhost. Реферальную ссылку копируют
// в первую очередь с телефона, поэтому запасной путь обязателен.
async function copyToClipboard(text, btn) {
  const ok = () => {
    const was = btn.textContent;
    btn.textContent = t("common.copied");
    btn.disabled = true;
    setTimeout(() => { btn.textContent = was; btn.disabled = false; }, 1400);
  };
  try {
    await navigator.clipboard.writeText(text);
    ok();
    return;
  } catch (e) { /* ниже запасной путь */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); ok(); } catch (e) { window.prompt(t("common.copyManual"), text); }
  ta.remove();
}

function accFail(pane, e) {
  pane.innerHTML = `<p class="error">${escHtml(errText(e) || t("common.loadFail"))}</p>`;
}

function accMsg(pane, text, kind = "") {
  const el = $(".acc-msg", pane);
  if (!el) return;
  el.textContent = text || "";
  el.className = "acc-msg status" + (kind ? " " + kind : "");
}

// Кнопка «скопировать» рядом с кодом и ссылкой — одна на оба места.
function bindCopy(pane) {
  $$(".acc-copy", pane).forEach((btn) => {
    btn.addEventListener("click", () => copyToClipboard(btn.dataset.copy || "", btn));
  });
}

const ACC_TABS = [
  { key: "account" },
  { key: "plan" },
  // «Файлы» — архив всего, что сгенерировано: сетка миниатюр с фильтрами по
  // дате, виду и проекту. Не отдельная страница, а вкладка кабинета: открытый
  // проект, поллинг и несохранённые поля остаются на месте.
  { key: "files" },
  { key: "ref" },
  { key: "payouts", admin: true },
  { key: "crm", admin: true },
  { key: "broadcast", admin: true },
];

const PAYOUT_STATUS = { new: "new", paid: "paid", rejected: "rejected" };

// Статус заявки: подпись из словаря, класс — из ключа. Неизвестный статус
// показываем как есть, чтобы новый статус с бэка не превращался в пустоту.
function payoutStatus(status) {
  const cls = PAYOUT_STATUS[status] || "";
  return {
    label: cls ? t("payouts.status" + cls.charAt(0).toUpperCase() + cls.slice(1)) : String(status || ""),
    cls,
  };
}

// ═════════════ внешние входы: Telegram, Яндекс ID, Google ═════════════
//
// ДЫРА, КОТОРУЮ ЭТО ЗАКРЫВАЕТ. Роут /api/auth/config существовал с самого
// начала, но фронт его НИ РАЗУ не дёргал: кнопок входа на сайте не было
// вообще, экран #login знал только логин с паролем. Человек, заведённый через
// Google или Яндекс, вернуться в свой аккаунт не мог — его данные были
// фактически заперты. Здесь одна функция рисует кнопки в трёх местах.
//
// Если ключей на сервере нет, /api/auth/config отдаёт false — и кнопок просто
// не будет. Это правильная деградация, а не поломка.

let authCfgCache = null;

async function authConfig() {
  if (authCfgCache) return authCfgCache;
  try {
    authCfgCache = await api("/api/auth/config");
  } catch (e) {
    authCfgCache = { telegram: false, yandex: false, google: false };
  }
  return authCfgCache;
}

// Иконки инлайном: внешних библиотек и CDN у проекта нет и заводить их ради
// трёх значков незачем.
const AUTH_ICO = {
  telegram: '<svg class="auth-ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="#29a9eb" d="M12 0a12 12 0 1 0 0 24 12 12 0 0 0 0-24Z"/><path fill="#fff" d="M5.5 11.8 17 7.3c.5-.2 1 .1.8.9l-2 9.2c-.1.6-.5.7-1 .5l-2.8-2-1.3 1.3c-.2.2-.3.3-.6.3l.2-3 5.4-4.9c.2-.2 0-.3-.3-.1l-6.7 4.2-2.9-.9c-.6-.2-.6-.6.1-.9Z"/></svg>',
  yandex: '<svg class="auth-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#fc3f1d"/><path fill="#fff" d="M13.2 19h2.1V5h-3c-3 0-4.8 1.6-4.8 4 0 1.9.9 3 2.5 4.1L7.2 19h2.3l2.5-5-1-.7c-1.3-.9-1.9-1.6-1.9-3 0-1.3.9-2.2 2.4-2.2h1.7V19Z"/></svg>',
  google: '<svg class="auth-ico" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285f4" d="M23 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.2a5.3 5.3 0 0 1-2.3 3.5v2.9h3.7c2.2-2 3.4-5 3.4-8.6Z"/><path fill="#34a853" d="M12 23.5c3.1 0 5.7-1 7.6-2.8l-3.7-2.9c-1 .7-2.3 1.1-3.9 1.1-3 0-5.5-2-6.4-4.7H1.8v3A11.5 11.5 0 0 0 12 23.5Z"/><path fill="#fbbc05" d="M5.6 14.2a6.9 6.9 0 0 1 0-4.4v-3H1.8a11.5 11.5 0 0 0 0 10.4l3.8-3Z"/><path fill="#ea4335" d="M12 4.7c1.7 0 3.2.6 4.4 1.7l3.3-3.3C17.7 1.2 15.1 0 12 0 7.5 0 3.6 2.6 1.8 6.4l3.8 3c.9-2.8 3.4-4.7 6.4-4.7Z"/></svg>',
};

// Login Widget Telegram — единственный способ входа через Telegram на обычном
// сайте: он требует домена, прописанного боту через /setdomain в BotFather.
// Внутри мини-аппа его НЕ показываем — там вход уже произошёл.
window.onTelegramAuth = async function (user) {
  try {
    await api("/api/auth/telegram" + (refCode ? `?ref=${encodeURIComponent(refCode)}` : ""),
              { method: "POST", body: user });
    me = await api("/api/me");
    closeModal();
    showApp();
  } catch (e) {
    fail(e);
  }
};

function tgWidget(botName) {
  const holder = document.createElement("div");
  holder.className = "auth-widget";
  const s = document.createElement("script");
  s.async = true;
  s.src = "https://telegram.org/js/telegram-widget.js?22";
  s.setAttribute("data-telegram-login", botName);
  s.setAttribute("data-size", "medium");
  s.setAttribute("data-radius", "14");
  s.setAttribute("data-userpic", "false");
  s.setAttribute("data-onauth", "onTelegramAuth(user)");
  holder.appendChild(s);
  return holder;
}

function authBtn(kind, label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "auth-btn";
  b.innerHTML = `${AUTH_ICO[kind] || ""}<span></span>`;
  b.querySelector("span").textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

/* Кнопки входа. mode:
     "login"  — экран входа и лендинг: кнопка ВХОДИТ;
     "link"   — кабинет: кнопка ПРИВЯЗЫВАЕТ (показываем только непривязанное). */
async function renderAuthButtons(container, opts = {}) {
  if (!container) return;
  const cfg = await authConfig();
  const linked = opts.linked || {};
  const inTg = Boolean(window.TGA && TGA.active);
  container.innerHTML = "";
  container.className = "auth-buttons" + (opts.column ? " auth-column" : "");

  const go = (provider) => {
    // В мини-аппе OAuth уходит во ВНЕШНИЙ браузер: редирект внутри webview
    // убивает window.Telegram.WebApp, и вернуться потом некуда.
    if (inTg) { tgExternalLogin(provider, container); return; }
    const q = refCode ? `?ref=${encodeURIComponent(refCode)}` : "";
    window.location.href = `/api/auth/${provider}/start${q}`;
  };

  let shown = 0;
  if (cfg.telegram && cfg.telegram_bot && !inTg && !linked.telegram) {
    container.appendChild(tgWidget(cfg.telegram_bot));
    shown += 1;
  }
  for (const p of ["yandex", "google"]) {
    if (!cfg[p] || linked[p]) continue;
    const label = opts.mode === "link"
      ? t("auth.link", { name: t("auth.name." + p) })
      : t("auth.with", { name: t("auth.name." + p) });
    container.appendChild(authBtn(p, label, () => go(p)));
    shown += 1;
  }
  // «Продолжить в Telegram» для того, кто уже сидит на сайте: код привязки
  // живёт 15 минут и сгорает при использовании.
  if (opts.mode === "link" && !inTg && cfg.telegram_bot && !linked.telegram) {
    container.appendChild(authBtn("telegram", t("auth.openInTg"), async (e) => {
      const btn = e ? e.currentTarget : null;
      if (btn) btn.disabled = true;
      try {
        const r = await api("/api/bot/link-code");
        if (r && r.url) window.open(r.url, "_blank", "noopener");
        else throw new Error(t("auth.noBot"));
      } catch (err) { fail(err); }
      if (btn) btn.disabled = false;
    }));
    shown += 1;
  }
  if (!shown && opts.emptyNote) {
    const p = document.createElement("p");
    p.className = "auth-note";
    p.textContent = opts.emptyNote;
    container.appendChild(p);
  }
  return shown;
}

// Вход через Яндекс/Google изнутри мини-аппа: браузер снаружи + ожидание.
async function tgExternalLogin(provider, container) {
  const note = document.createElement("p");
  note.className = "auth-note";
  note.textContent = t("tg.waitingBrowser");
  container.appendChild(note);
  try {
    const st = await TGA.externalLogin(provider, (state) => {
      if (state === "timeout") note.textContent = t("tg.linkTimeout");
    });
    if (st.status === "done") {
      me = await api("/api/me");
      TGA.done();
      closeModal();
      showApp();
      return;
    }
    if (st.status === "conflict") { note.textContent = t("tg.conflictShort"); return; }
    if (st.status === "expired") note.textContent = t("tg.linkExpired");
  } catch (e) {
    note.textContent = errText(e);
  }
}

// ═════════════════════ мини-апп: экраны и состояния ═════════════════════

// Telegram не опознал — единственный тупик, из которого мини-апп сам не
// выберется. Говорим что случилось, а не показываем пустой экран.
function showTgFail(e) {
  hideScreens();
  const box = $("#login");
  if (!box) return;
  box.classList.remove("hidden");
  const form = $("#login-form");
  if (form) {
    form.innerHTML = `
      <h1 class="login-word">lolq<span class="brand-dot">.</span>ai</h1>
      <p class="error">${escHtml(t("tg.authFail"))}</p>
      <p class="auth-note">${escHtml(errText(e) || "")}</p>`;
  }
}

// После входа в мини-апп: подхватываем мост (привязка аккаунта сайта) и
// отпускаем свайп-закрытие на стартовом экране.
function tgAfterSignIn() {
  if (!(window.TGA && TGA.active)) return;
  TGA.swipeGuard(false);
  if (TGA.bridge === "conflict" && TGA.other) {
    const o = TGA.other;
    TGA.alert(t("tg.conflict", {
      name: o.name || "—", plan: o.plan || "free", points: tNum(o.points || 0),
    }));
  } else if (TGA.bridge === "expired") {
    TGA.alert(t("tg.linkExpired"));
  } else if (TGA.merged) {
    TGA.done();
  }
}

function openAccountModal(initial = "account") {
  const isAdmin = Boolean(me && me.user && me.user.is_admin);
  const tabs = ACC_TABS.filter((tab) => !tab.admin || isAdmin);
  const start = tabs.some((tab) => tab.key === initial) ? initial : "account";
  openModal(t("account.title"), (body) => {
    body.innerHTML = `
      <div class="modal-tabs acc-tabs">
        ${tabs.map((tab) => `<button type="button" class="modal-tab" data-tab="${tab.key}"
          >${escHtml(t("account.tabs." + tab.key))}</button>`).join("")}
      </div>
      ${tabs.map((tab) => `<div class="acc-pane hidden" data-pane="${tab.key}">
        <div class="muted acc-loading">${escHtml(t("common.loading"))}</div></div>`).join("")}`;

    const loaders = {
      account: renderAccountPane, plan: renderPlanPane,
      files: renderFilesPane, ref: renderRefPane, payouts: renderPayoutsPane,
      crm: renderCrmPane, broadcast: renderBroadcastPane,
    };
    // Вкладка грузится один раз при первом открытии: лишних запросов нет,
    // а перерисовку после действий делают сами обработчики.
    const loaded = new Set();
    const open = (key) => {
      $$(".modal-tab", body).forEach((el) => el.classList.toggle("on", el.dataset.tab === key));
      $$(".acc-pane", body).forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== key));
      if (loaded.has(key)) return;
      loaded.add(key);
      const pane = $(`.acc-pane[data-pane="${key}"]`, body);
      if (pane && loaders[key]) loaders[key](pane);
    };
    $$(".modal-tab", body).forEach((el) => el.addEventListener("click", () => open(el.dataset.tab)));
    open(start);
  }, { medium: true });
}

// Одна строка правды о подписке: кто списывает, когда следующий раз и, для
// звёзд, где её можно отменить помимо нас. Раньше кабинет вообще не знал о
// Stars и писал звёздному подписчику «автопродление выключено».
function payLine(a) {
  const src = a.pay_provider || "";
  if (!src || a.plan === "free") return "";
  const when = a.next_charge ? fmtDate(a.next_charge) : (a.plan_until ? fmtDate(a.plan_until) : "");
  const via = t("account.via." + (src === "stars" ? "stars" : src === "stripe" ? "card" : "card"));
  if (!a.autopay) return t("account.payUntil", { via: via, date: when });
  return t("account.payNext", { via: via, date: when })
    + (src === "stars" ? " · " + t("account.starsWhere") : "");
}

// ────────── вкладка «Аккаунт»: дашборд ──────────
// Главный вопрос кабинета — «сколько я ещё сделаю», а не «какой у меня
// тариф». Поэтому герой один и широкий (остаток + прогноз при нынешнем
// темпе), а тариф уезжает во второй ярус к расходу и партнёрке.
//
// Раньше здесь лежали четыре одинаковые плашки «Тариф / До / Очки / Проекты».
// Из них выжила одна: «Проекты: 3» не отвечает ни на один вопрос, а «До»
// дублирует строку оплаты. График строится по journalу очков (PointEvent) —
// до него история расхода существовала только в логах контейнера.

// ────────── графики: своя отрисовка, без библиотек ──────────
// SVG, а не <div> с высотой в пикселях: столбик из вложенных div'ов не умеет
// ни подписей осей, ни аккуратного стека, ни тултипа, а весь график — это
// один узел вместо тридцати с сегментами внутри.

/* Цвет вида работы. Берём из тех же CSS-переменных, что и легенда: второй
   палитры в проекте быть не должно. */
const KIND_FALLBACK = {
  frames: "--gold", video: "--accent", chat: "", audio: "--ok",
  story: "--accent-2", sheet: "", model: "", assemble: "", other: "",
};
const KIND_PLAIN = {
  chat: "#7a9bd4", sheet: "#c58bd8", model: "#8fd0c0",
  assemble: "#b4a08c", other: "rgba(45,33,26,.35)",
};

function kindColor(kind) {
  const varName = KIND_FALLBACK[kind];
  if (varName) {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(varName).trim();
    if (v) return v;
  }
  return KIND_PLAIN[kind] || "rgba(45,33,26,.35)";
}

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, String(v)));
  return el;
}

/* Столбики расхода по дням со стеком по видам работы. */
function dashSpark(usage) {
  const days = usage.daily || [];
  const kinds = usage.kinds || [];
  const W = 640, H = 140, gap = 2;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${W} ${H}`, class: "dash-svg", preserveAspectRatio: "none",
    role: "img", "aria-label": t("dash.chart"),
  });
  const max = Math.max(1, ...days.map((d) => d.spent || 0));
  const bw = Math.max(1, W / Math.max(1, days.length) - gap);
  days.forEach((d, i) => {
    const x = i * (bw + gap);
    if (!d.spent) {
      svg.appendChild(svgEl("rect", {
        x, y: H - 2, width: bw, height: 2, rx: 1, fill: "rgba(45,33,26,.10)",
      }));
      return;
    }
    let y = H;
    kinds.forEach((k) => {
      if (!d[k]) return;
      const h = Math.max(1, (d[k] / max) * (H - 6));
      y -= h;
      const r = svgEl("rect", { x, y, width: bw, height: h, fill: kindColor(k) });
      r.appendChild(svgEl("title", {})).textContent =
        `${d.date} · ${t("dash.kinds." + k) || k}: ${tNum(d[k])}`;
      svg.appendChild(r);
    });
  });
  const box = document.createElement("div");
  box.className = "dash-bars";
  box.appendChild(svg);
  return box;
}

/* Линии «выручка против себестоимости» — только для админской сводки. */
function svgLines(rows, series, opts = {}) {
  const W = 640, H = 150, pad = 4;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "dash-svg" });
  const max = Math.max(1, ...rows.flatMap((r) => series.map((sr) => r[sr.key] || 0)));
  const step = rows.length > 1 ? (W - pad * 2) / (rows.length - 1) : 0;
  series.forEach((sr) => {
    const pts = rows.map((r, i) => {
      const x = pad + i * step;
      const y = H - pad - ((r[sr.key] || 0) / max) * (H - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    svg.appendChild(svgEl("polyline", {
      points: pts, fill: "none", stroke: sr.color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));
  });
  const box = document.createElement("div");
  box.className = "dash-bars";
  if (opts.height) box.style.height = opts.height;
  box.appendChild(svg);
  return box;
}

function dashLegend(usage) {
  const box = document.createElement("div");
  box.className = "dash-legend";
  const total = {};
  (usage.daily || []).forEach((d) => {
    (usage.kinds || []).forEach((k) => { total[k] = (total[k] || 0) + (d[k] || 0); });
  });
  Object.entries(total)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .forEach(([k, v]) => {
      const item = document.createElement("span");
      const dot = document.createElement("i");
      dot.className = "dash-seg-" + k;
      item.append(dot, document.createTextNode(`${t("dash.kinds." + k) || k} · ${tNum(v)}`));
      box.appendChild(item);
    });
  return box;
}

function dashRecent(usage) {
  const box = document.createElement("div");
  box.className = "dash-recent";
  const rows = usage.recent || [];
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("dash.recentEmpty");
    box.appendChild(empty);
    return box;
  }
  rows.forEach((e) => {
    const row = document.createElement("div");
    row.className = "dash-ev";
    const kind = document.createElement("span");
    kind.className = "dash-ev-kind";
    kind.textContent = t("dash.kinds." + e.kind) || e.kind;
    const what = document.createElement("span");
    what.className = "dash-ev-what";
    // Упавшая генерация с возвратом видна отдельной строкой — иначе «молча
    // съеденные очки» остаются молча съеденными.
    what.textContent = (e.what || "") + (e.engine ? ` · ${engineTitle(e.engine)}` : "");
    const when = document.createElement("span");
    when.className = "dash-ev-when";
    when.textContent = fmtDate(e.at, true);
    const delta = document.createElement("span");
    delta.className = "dash-ev-delta " + (e.delta < 0 ? "minus" : "plus");
    delta.textContent = (e.delta > 0 ? "+" : "") + tNum(e.delta);
    row.append(kind, what, when, delta);
    box.appendChild(row);
  });
  return box;
}

async function renderAccountPane(pane) {
  let a;
  try { a = await api("/api/account"); } catch (e) { return accFail(pane, e); }
  // Расход — отдельный запрос и НЕ обязательный: журнал мог не завестись на
  // старой базе, и кабинет обязан открыться всё равно.
  const usage = await api("/api/account/usage?days=30").catch(() => null);
  const linked = a.linked || {};
  const chips = [["telegram", "Telegram"], ["google", "Google"],
                 ["yandex", t("account.yandex")], ["password", t("account.password")]];
  const initial = (a.name || "?").trim().charAt(0).toUpperCase() || "?";
  const sceneCost = (providers.frames_cost || 0)
    + ((videoEngineById(effVideoEngine(null)) || {}).video_cost || 0);
  const scenesLeft = sceneCost > 0 ? Math.floor((a.points || 0) / sceneCost) : 0;
  const burn = usage ? usage.burn_day : 0;

  pane.innerHTML = `
    <div class="acc-head">
      ${a.avatar_url
        ? `<img class="acc-avatar" src="${escHtml(a.avatar_url)}" alt="" />`
        : `<span class="acc-avatar acc-avatar-ph">${escHtml(initial)}</span>`}
      <div class="acc-who">
        <b>${escHtml(a.name || t("account.guest"))}</b>
        <span class="muted">${escHtml(a.email || a.login || t("account.noContacts"))}</span>
      </div>
    </div>

    <div class="dash-hero">
      <div class="dash-hero-top">
        <span class="dash-big">${escHtml(tNum(a.points))}</span>
        <span class="dash-unit">${escHtml(t("top.pointsUnit"))}</span>
      </div>
      <div class="dash-hero-lines">
        <span class="dash-line">${sceneCost > 0
          ? escHtml(t("dash.enough", { n: tNum(scenesLeft),
                                       word: tPlural(scenesLeft, tRaw("dash.sceneWord")) }))
            + (a.movies_left ? " · " + escHtml(t("dash.enoughClips", {
                n: tNum(a.movies_left),
                word: tPlural(a.movies_left, tRaw("dash.clipWord")) })) : "")
          : escHtml(t("dash.leftOf", { n: tNum(a.points) }))}</span>
        <span class="dash-line">${escHtml(
          burn > 0 ? t("dash.burn", { n: tNum(burn) }) : t("dash.burnNone"))}</span>
        <span class="dash-line">${escHtml(
          usage && usage.forecast_date
            ? t("dash.forecast", { date: fmtDate(usage.forecast_date) })
            : t("dash.forecastNever"))}</span>
      </div>
      <div class="row dash-hero-acts"></div>
    </div>

    <div class="dash-cards">
      <div class="dash-card">
        <span>${escHtml(t("dash.plan"))}</span>
        <b>${escHtml(a.plan_title || t("dash.planFree"))}</b>
        <span class="dash-sub">${escHtml(payLine(a) || a.plan_note || "")}</span>
      </div>
      <div class="dash-card">
        <span>${escHtml(t("dash.spent30", { n: usage ? usage.days : 30 }))}</span>
        <b>${escHtml(tNum(usage ? usage.spent : 0))}</b>
        <!-- Со словом, а не голым «+4 754»: рядом с «потрачено» знак плюса без
             подписи читается как «потрачено ещё столько же». -->
        <span class="dash-sub">${escHtml(
          usage && usage.granted ? t("dash.granted", { n: tNum(usage.granted) }) : "")}</span>
      </div>
      <div class="dash-card">
        <span>${escHtml(t("dash.ref"))}</span>
        <b>${escHtml(fmtRub((a.ambassador && a.ambassador.balance_kopeks) || 0))}</b>
        <span class="dash-sub">${escHtml(
          t("dash.refInvited", { n: tNum((a.ambassador && a.ambassador.referrals) || 0) }))}</span>
      </div>
    </div>

    <div class="dash-chart">
      <div class="dash-chart-head">
        <label style="margin:0">${escHtml(t("dash.chart"))}</label>
      </div>
    </div>

    <label>${escHtml(t("dash.recent"))}</label>
    <div class="dash-recent-box"></div>

    <label>${escHtml(t("account.logins"))}</label>
    <div class="acc-chips">
      ${chips.map(([k, label]) => `<span class="acc-chip${linked[k] ? " on" : ""}">${linked[k] ? "✓" : "○"} ${escHtml(label)}</span>`).join("")}
    </div>
    <!-- Кнопки привязать вход: без них человек с одним внешним входом
         навсегда оставался бы заперт в одном способе входа. -->
    <div class="auth-buttons acc-link"></div>
    <div class="row acc-actions"></div>
    <span class="acc-msg status"></span>`;

  // ── Рамка тарифа. Про потолок накопления (две месячные нормы) человек до
  // сих пор не знал вообще, и это прямой источник обиды: накопил, оплатил,
  // часть сгорела. Теперь он видит её заранее, а не постфактум.
  const lim = usage && usage.limits;
  if (lim) {
    const box = document.createElement("div");
    box.className = "lim-grid";
    const pct = lim.norm ? Math.min(100, Math.round((lim.used / lim.norm) * 100)) : 0;
    const cell = (cap, value, sub, extra = "") => `
      <div class="lim-card"><span>${escHtml(cap)}</span><b>${escHtml(value)}</b>
      <span class="lim-sub">${escHtml(sub)}</span>${extra}</div>`;
    const parts = [
      cell(t("lim.norm"), tNum(lim.norm),
           t("lim.usedOf", { n: tNum(lim.used) }),
           `<span class="lim-bar"><i class="${pct >= 100 ? "over" : ""}"
             style="width:${pct}%"></i></span>`),
      cell(t("lim.cap"), tNum(lim.cap), t("lim.capNote")),
    ];
    if (lim.drip_left) {
      // Годовая подписка: очки капают помесячно. Без этой плашки годовой
      // тариф выглядит обманом — заплатил за год, на счету одна норма.
      parts.push(cell(t("lim.drip"), tNum(lim.drip_left * lim.drip_size),
                      t("lim.dripNote", { n: lim.drip_left,
                                          date: fmtDate(lim.drip_at) })));
    }
    if (lim.plan_until) {
      parts.push(cell(t("lim.until"), fmtDate(lim.plan_until),
                      lim.tier_next ? t("lim.tierNext", { tier: lim.tier_next })
                                    : t("lim.period." + (lim.period || "month"))));
    }
    box.innerHTML = parts.join("");
    $(".dash-cards", pane).insertAdjacentElement("afterend", box);

    // Движки тарифа: «открыт тарифом» и «жив по ключам» — разные вещи, и
    // кабинет обязан их различать. Раньше этого не было нигде, кроме
    // /api/providers, который кабинет не спрашивал.
    const eng = document.createElement("div");
    eng.className = "lim-engines";
    (lim.engines || []).forEach((e) => {
      const chip = document.createElement("span");
      chip.className = "lim-eng" + (e.live ? "" : " off");
      chip.textContent = `${e.title} · ${tNum(e.scene_cost)}`;
      chip.title = e.live ? t("lim.engLive") : t("lim.engDead");
      eng.appendChild(chip);
    });
    if (eng.children.length) box.insertAdjacentElement("afterend", eng);
  }

  const chart = $(".dash-chart", pane);
  if (usage && usage.spent) {
    chart.append(dashSpark(usage), dashLegend(usage));
    // Вторая легенда — ПО ДВИЖКАМ. «Сколько куда потратилось» без неё
    // отвечено наполовину: Kling 3.0 Pro и Seedance 2 Mini отличаются в
    // цене на порядок, а в разбивке по видам работы оба просто «Видео».
    if ((usage.by_engine || []).length) {
      const head = document.createElement("p");
      head.className = "dash-approx muted";
      head.style.marginBottom = "4px";
      head.textContent = t("dash.byEngine");
      const legend = document.createElement("div");
      legend.className = "dash-legend";
      usage.by_engine.forEach((row) => {
        const item = document.createElement("span");
        item.textContent = `${engineTitle(row.engine)} · ${tNum(row.spent)}`
          + ` (${tNum(row.ops)})`;
        legend.appendChild(item);
      });
      chart.append(head, legend);
    }
    if (usage.approx_before) {
      const note = document.createElement("p");
      note.className = "dash-approx muted";
      note.textContent = t("dash.approx", { date: fmtDate(usage.approx_before) });
      chart.appendChild(note);
    }
  } else {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("dash.chartEmpty");
    chart.appendChild(empty);
  }
  const recentBox = $(".dash-recent-box", pane);
  recentBox.appendChild(dashRecent(usage || {}));
  // Двенадцати строк хватает на «что я сделал только что» и не хватает на
  // «за что списали 154 очка в прошлый вторник». Полная лента — с фильтрами.
  const allBtn = document.createElement("button");
  allBtn.type = "button";
  allBtn.className = "ghost ev-more";
  allBtn.textContent = t("dash.allEvents");
  allBtn.addEventListener("click", () => openEventsModal());
  recentBox.appendChild(allBtn);

  // Герой ведёт в кассу: это единственное действие, ради которого сюда
  // приходят, когда очки кончаются. Огонь — только на нём.
  const heroActs = $(".dash-hero-acts", pane);
  const topup = document.createElement("button");
  topup.type = "button";
  topup.className = "primary";
  topup.textContent = t("dash.topup");
  topup.addEventListener("click", () => openAccountModal("plan"));
  heroActs.appendChild(topup);

  renderAuthButtons($(".acc-link", pane), { mode: "link", linked });

  const actions = $(".acc-actions", pane);
  if (a.autopay) {
    // Автопродление выключается только когда оно есть — иначе кнопка врала бы.
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "danger";
    btn.textContent = t("account.autopayOff");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      accMsg(pane, t("account.autopayOffBusy"));
      try {
        await api("/api/billing/cancel", { method: "POST" });
        await renderAccountPane(pane);
        accMsg(pane, t("account.autopayOffDone"), "done");
      } catch (e) {
        btn.disabled = false;
        accMsg(pane, errText(e), "error");
      }
    });
    const note = document.createElement("span");
    note.className = "muted";
    // У звёзд отмена не возвращает неиспользованные дни и работает на стороне
    // Telegram — говорим это прямо, а не общей фразой про автопродление.
    note.textContent = a.pay_provider === "stars"
      ? t("account.starsCancelNote") : t("account.autopayOffNote");
    actions.append(btn, note);
  } else {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = a.plan === "free" ? t("account.choosePlan") : t("account.renewPlan");
    btn.addEventListener("click", () => openAccountModal("plan"));
    actions.appendChild(btn);
    if (a.plan !== "free") {
      const note = document.createElement("span");
      note.className = "muted";
      note.textContent = t("account.autopayNote");
      actions.appendChild(note);
    }
  }
}

// ────────── полная лента операций: фильтры и подгрузка ──────────
// Курсор, а не «страница N»: пока человек листает, генерации продолжают
// писать новые строки, и OFFSET начал бы их повторять и пропускать.

const EV_FILTERS = ["", "spent", "granted", "refund"];

function openEventsModal(userId = 0) {
  openModal(t("dash.allEvents"), (body) => {
    body.innerHTML = `
      <div class="ev-filters"></div>
      <div class="dash-recent ev-list"></div>
      <span class="status ev-msg"></span>`;
    const list = $(".ev-list", body);
    const state = { only: "", kind: "", cursor: 0 };

    const load = async (reset) => {
      if (reset) { list.innerHTML = ""; state.cursor = 0; }
      const qs = new URLSearchParams({ limit: "50" });
      if (state.only) qs.set("only", state.only);
      if (state.kind) qs.set("kind", state.kind);
      if (state.cursor) qs.set("cursor", String(state.cursor));
      if (userId) qs.set("user_id", String(userId));
      let data;
      try {
        data = await api(`/api/account/events?${qs}`);
      } catch (e) {
        $(".ev-msg", body).textContent = errText(e);
        return;
      }
      $$(".ev-more", body).forEach((b) => b.remove());
      if (!data.items.length && !state.cursor) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = t("dash.recentEmpty");
        list.appendChild(empty);
        return;
      }
      data.items.forEach((e) => list.appendChild(eventRow(e)));
      state.cursor = data.next_cursor || 0;
      if (state.cursor) {
        const more = document.createElement("button");
        more.type = "button";
        more.className = "ghost ev-more";
        more.textContent = t("common.loadMore");
        more.addEventListener("click", () => load(false));
        list.after(more);
      }
    };

    const chips = $(".ev-filters", body);
    EV_FILTERS.forEach((key) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ev-chip" + (key === state.only ? " on" : "");
      chip.textContent = t("dash.filter." + (key || "all"));
      chip.addEventListener("click", () => {
        state.only = key;
        $$(".ev-chip", chips).forEach((x) => x.classList.toggle("on", x === chip));
        load(true);
      });
      chips.appendChild(chip);
    });
    load(true);
  }, { medium: true });
}

function eventRow(e) {
  const row = document.createElement("div");
  row.className = "dash-ev";
  const kind = document.createElement("span");
  kind.className = "dash-ev-kind";
  kind.textContent = t("dash.kinds." + e.kind) || e.kind;
  const what = document.createElement("span");
  what.className = "dash-ev-what";
  what.textContent = (e.what || "") + (e.engine ? ` · ${engineTitle(e.engine)}` : "");
  // task_id приходит только админу: по нему разбирается спорная генерация
  // («списали 154 → задача kie abc123 → упала → возврат строкой ниже»).
  if (e.task_id) what.title = `task ${e.task_id}`;
  const when = document.createElement("span");
  when.className = "dash-ev-when";
  when.textContent = fmtDate(e.at, true);
  const delta = document.createElement("span");
  delta.className = "dash-ev-delta " + (e.delta < 0 ? "minus" : "plus");
  delta.textContent = (e.delta > 0 ? "+" : "") + tNum(e.delta);
  row.append(kind, what, when, delta);
  return row;
}

// ══════════════════════ ВКЛАДКА «ФАЙЛЫ»: архив ══════════════════════
// «Папка со всеми файлами, рассортированная по датам, видам и проектам».
//
// ГЛАВНОЕ ОГРАНИЧЕНИЕ, из которого выросла вся раскладка: клип весит до
// 1.5 ГБ, кадр — 15 МБ в 4К. Поэтому здесь НЕТ ни одного <video> и ни одного
// оригинала: сетка живёт на /api/thumb (ffmpeg, 640px, кэш), а видео
// показано постером первого кадра со значком ▶. Проигрывание и скачивание —
// по клику, одним элементом на всю страницу.

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v >= 1024 ** 3) return (v / 1024 ** 3).toFixed(1) + " GB";
  if (v >= 1024 ** 2) return Math.round(v / 1024 ** 2) + " MB";
  if (v >= 1024) return Math.round(v / 1024) + " KB";
  return v + " B";
}

const ARC_STATE = { kind: "", project_id: 0, days: 0, sort: "date", cursor: "", user_id: 0 };

async function renderFilesPane(pane) {
  pane.innerHTML = `
    <div class="arc-head">
      <span class="arc-total"></span>
      <span class="muted arc-sub"></span>
    </div>
    <div class="ev-filters arc-kinds"></div>
    <div class="ev-filters arc-scope"></div>
    <div class="arc-grid"></div>
    <span class="status arc-msg"></span>`;
  ARC_STATE.kind = "";
  ARC_STATE.project_id = 0;
  ARC_STATE.days = 0;
  ARC_STATE.cursor = "";
  await loadFiles(pane, true);
}

async function loadFiles(pane, reset) {
  const grid = $(".arc-grid", pane);
  if (reset) { grid.innerHTML = ""; ARC_STATE.cursor = ""; }
  const qs = new URLSearchParams({ limit: "60", sort: ARC_STATE.sort });
  if (ARC_STATE.kind) qs.set("kind", ARC_STATE.kind);
  if (ARC_STATE.project_id) qs.set("project_id", String(ARC_STATE.project_id));
  if (ARC_STATE.days) qs.set("days", String(ARC_STATE.days));
  if (ARC_STATE.cursor) qs.set("cursor", ARC_STATE.cursor);
  // Админ может смотреть архив конкретного клиента из его карточки в CRM.
  if (ARC_STATE.user_id) qs.set("user_id", String(ARC_STATE.user_id));
  let data;
  try {
    data = await api(`/api/files?${qs}`);
  } catch (e) {
    const msg = $(".arc-msg", pane);
    if (msg) { msg.textContent = errText(e); msg.className = "status arc-msg error"; }
    else accFail(pane, e);
    return;
  }
  if (reset) {
    // Сколько всего занято и чем: человек должен видеть, что клипы съели
    // 40 ГБ, а кадры 300 МБ, — иначе непонятно, что вообще чистить.
    $(".arc-total", pane).textContent =
      t("files.total", { n: tNum(data.totals.count),
                         word: tPlural(data.totals.count, tRaw("files.word")),
                         size: fmtBytes(data.totals.bytes) });
    $(".arc-sub", pane).textContent = (data.totals.by_kind || [])
      .slice(0, 4)
      .map((r) => `${t("files.kinds." + r.kind) || r.kind} ${fmtBytes(r.bytes)}`)
      .join(" · ");
    buildArcFilters(pane, data);
  }
  $$(".ev-more", pane).forEach((b) => b.remove());
  if (!data.items.length && reset) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("files.empty");
    grid.appendChild(empty);
    return;
  }
  let lastDay = grid.dataset.lastDay || "";
  data.items.forEach((f) => {
    const day = (f.at || "").slice(0, 10);
    if (day !== lastDay) {
      lastDay = day;
      const head = document.createElement("div");
      head.className = "arc-day";
      head.textContent = fmtDate(f.at);
      grid.appendChild(head);
    }
    grid.appendChild(arcCell(f, pane));
  });
  grid.dataset.lastDay = lastDay;
  ARC_STATE.cursor = data.next_cursor || "";
  if (ARC_STATE.cursor) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "ghost ev-more";
    more.textContent = t("common.loadMore");
    more.addEventListener("click", () => loadFiles(pane, false));
    grid.after(more);
  }
}

function buildArcFilters(pane, data) {
  const kinds = $(".arc-kinds", pane);
  kinds.innerHTML = "";
  const present = new Set((data.totals.by_kind || []).map((r) => r.kind));
  [["", t("files.all")]].concat(
    (data.kinds || []).filter((k) => present.has(k))
      .map((k) => [k, t("files.kinds." + k) || k]),
  ).forEach(([key, label]) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ev-chip" + (key === ARC_STATE.kind ? " on" : "");
    chip.textContent = label;
    chip.addEventListener("click", () => {
      ARC_STATE.kind = key;
      $$(".ev-chip", kinds).forEach((x) => x.classList.toggle("on", x === chip));
      $(".arc-grid", pane).dataset.lastDay = "";
      loadFiles(pane, true);
    });
    kinds.appendChild(chip);
  });

  const scope = $(".arc-scope", pane);
  scope.innerHTML = "";
  const addChip = (label, on, apply) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ev-chip" + (on ? " on" : "");
    chip.textContent = label;
    chip.addEventListener("click", () => {
      apply();
      $(".arc-grid", pane).dataset.lastDay = "";
      loadFiles(pane, true).then(() => buildArcFilters(pane, data));
    });
    scope.appendChild(chip);
  };
  addChip(t("files.allTime"), !ARC_STATE.days, () => { ARC_STATE.days = 0; });
  addChip(t("files.days7"), ARC_STATE.days === 7, () => { ARC_STATE.days = 7; });
  addChip(t("files.days30"), ARC_STATE.days === 30, () => { ARC_STATE.days = 30; });
  addChip(t("files.bySize"), ARC_STATE.sort === "size",
          () => { ARC_STATE.sort = ARC_STATE.sort === "size" ? "date" : "size"; });
  (data.projects || []).forEach((pr) => {
    addChip(pr.name || `#${pr.id}`, ARC_STATE.project_id === pr.id,
            () => { ARC_STATE.project_id = ARC_STATE.project_id === pr.id ? 0 : pr.id; });
  });
}

function arcCell(f, pane) {
  const cell = document.createElement("button");
  cell.type = "button";
  cell.className = "arc-cell";
  if (f.thumb_url) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.src = f.thumb_url;
    // Битая миниатюра не должна оставлять дыру в сетке.
    img.addEventListener("error", () => { img.remove(); });
    cell.appendChild(img);
  }
  const ph = document.createElement("span");
  ph.className = "arc-ph";
  ph.textContent = f.is_audio ? "♪" : f.is_video ? "▶" : "▦";
  cell.appendChild(ph);
  const tag = document.createElement("span");
  tag.className = "arc-tag";
  tag.textContent = t("files.kinds." + f.kind) || f.kind;
  cell.appendChild(tag);
  if (f.is_video && f.thumb_url) {
    const play = document.createElement("span");
    play.className = "arc-play";
    play.textContent = "▶";
    cell.appendChild(play);
  }
  const size = document.createElement("span");
  size.className = "arc-size";
  size.textContent = fmtBytes(f.size_bytes);
  cell.appendChild(size);
  cell.addEventListener("click", () => openFileModal(f, pane));
  return cell;
}

function openFileModal(f, pane) {
  openModal(t("files.kinds." + f.kind) || f.kind, (body) => {
    // Оригинал грузится ТОЛЬКО здесь и только один — на весь экран, а не
    // тридцать штук в сетке.
    if (f.is_video) {
      const v = document.createElement("video");
      v.className = "arc-view";
      v.src = f.url;
      v.controls = true;
      v.preload = "metadata";
      v.poster = f.thumb_url || "";
      body.appendChild(v);
    } else if (f.is_audio) {
      const a = document.createElement("audio");
      a.src = f.url;
      a.controls = true;
      a.style.width = "100%";
      body.appendChild(a);
    } else {
      const img = document.createElement("img");
      img.className = "arc-view";
      img.src = f.url;
      img.alt = "";
      img.decoding = "async";
      body.appendChild(img);
    }
    const meta = document.createElement("p");
    meta.className = "arc-meta";
    meta.textContent = [
      fmtDate(f.at, true), fmtBytes(f.size_bytes),
      f.project_id ? t("files.inProject", { n: f.project_id }) : "",
      f.scene_id ? t("files.inScene", { n: f.scene_id }) : "",
    ].filter(Boolean).join(" · ");
    body.appendChild(meta);

    const row = document.createElement("div");
    row.className = "row";
    const dl = document.createElement("button");
    dl.type = "button";
    dl.className = "primary";
    dl.textContent = t("files.download");
    dl.addEventListener("click", async () => {
      dl.disabled = true;
      try {
        // Подписанная ссылка, а не /api/media: там нужна кука, и «скачать по
        // ссылке с телефона» ломается.
        const r = await api(`/api/files/link/${encodeURIComponent(f.filename)}`);
        window.open(r.url, "_blank");
      } catch (e) { fail(e); }
      dl.disabled = false;
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = t("common.del");
    del.addEventListener("click", async () => {
      if (!confirm(t("files.delConfirm"))) return;
      try {
        await api(`/api/files/${encodeURIComponent(f.filename)}`, { method: "DELETE" });
      } catch (e) {
        // 409 «файл используется» — это не ошибка, а ответ: говорим, где он
        // занят, вместо того чтобы ломать сцену пустой ссылкой.
        return fail(e);
      }
      closeModal();
      $(".arc-grid", pane).dataset.lastDay = "";
      loadFiles(pane, true);
    });
    row.append(dl, del);
    body.appendChild(row);
  }, { medium: true });
}

// ══════════════════════ ВКЛАДКА «КЛИЕНТЫ» (только админ) ══════════════════════
// «Видеть клиентов и тех, кто зарегался, включать-отключать им подписки или
// начислять токены». Каждое действие пишется дважды: движение очков — в
// журнал очков (та же дверь, что у генераций), сам факт «кто и когда» — в
// admin_actions. Иначе «кто включил человеку ULTRA руками» остаётся загадкой.

const CRM_STATE = { q: "", plan: "", state: "", has: "", sort: "new", cursor: 0 };

async function renderCrmPane(pane) {
  pane.innerHTML = `
    <div class="crm-stats"></div>
    <div class="crm-search">
      <input class="crm-q" type="search" />
      <button type="button" class="crm-go primary"></button>
    </div>
    <div class="ev-filters crm-filters"></div>
    <div class="crm-list"></div>
    <span class="status crm-msg"></span>`;
  $(".crm-q", pane).placeholder = t("crm.searchPh");
  $(".crm-go", pane).textContent = t("crm.search");

  let stats = null;
  try { stats = await api("/api/admin/stats?days=30"); } catch (e) { return accFail(pane, e); }
  const u = stats.users;
  $(".crm-stats", pane).innerHTML = [
    ["crm.total", tNum(u.total), t("crm.active7", { n: tNum(u.active7) })],
    ["crm.paying", tNum(u.paying), t("crm.blockedN", { n: tNum(u.blocked) })],
    ["crm.revenue", fmtUsdCents(stats.revenue_cents),
     t("crm.cost", { v: fmtUsdCents(stats.cost_cents) })],
    ["crm.storage", fmtBytes(stats.storage.bytes),
     t("crm.files", { n: tNum(stats.storage.files),
                      word: tPlural(stats.storage.files, tRaw("files.word")) })],
    ["crm.contacts", `${tNum(u.with_email)} / ${tNum(u.with_tg)}`, t("crm.contactsNote")],
  ].map(([key, val, sub]) => `
    <div class="lim-card"><span>${escHtml(t(key))}</span><b>${escHtml(val)}</b>
    <span class="lim-sub">${escHtml(sub)}</span></div>`).join("");

  // Выручка против себестоимости по дням: до появления cost_cents маржа
  // сервиса была неизвестна в принципе — мы знали, сколько нам заплатили, и
  // не знали, сколько за это отдали kie.ai.
  if ((stats.daily || []).some((d) => d.revenue_cents || d.cost_cents)) {
    const chart = svgLines(stats.daily, [
      { key: "revenue_cents", color: kindColor("audio") },
      { key: "cost_cents", color: kindColor("video") },
    ]);
    const cap = document.createElement("p");
    cap.className = "dash-approx muted";
    cap.textContent = t("crm.chartNote");
    $(".crm-stats", pane).after(chart);
    chart.after(cap);
  }

  const filters = [
    ["", "", t("crm.f.all")], ["plan", "paid", t("crm.f.paid")],
    ["state", "active", t("crm.f.active")], ["state", "sleeping", t("crm.f.sleeping")],
    ["state", "blocked", t("crm.f.blocked")], ["has", "email", t("crm.f.email")],
    ["has", "tg", t("crm.f.tg")], ["has", "ambassador", t("crm.f.amb")],
  ];
  const fbox = $(".crm-filters", pane);
  filters.forEach(([key, val, label]) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ev-chip" + ((!key && !CRM_STATE.plan && !CRM_STATE.state
                                   && !CRM_STATE.has) ? " on" : "");
    chip.textContent = label;
    chip.addEventListener("click", () => {
      CRM_STATE.plan = CRM_STATE.state = CRM_STATE.has = "";
      if (key) CRM_STATE[key] = val;
      $$(".ev-chip", fbox).forEach((x) => x.classList.toggle("on", x === chip));
      loadClients(pane, true);
    });
    fbox.appendChild(chip);
  });

  const go = () => { CRM_STATE.q = $(".crm-q", pane).value.trim(); loadClients(pane, true); };
  $(".crm-go", pane).addEventListener("click", go);
  $(".crm-q", pane).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  await loadClients(pane, true);
}

function fmtUsdCents(cents) {
  const v = (Number(cents) || 0) / 100;
  return "$" + (v >= 100 ? Math.round(v) : v.toFixed(2));
}

async function loadClients(pane, reset) {
  const list = $(".crm-list", pane);
  if (reset) { list.innerHTML = ""; CRM_STATE.cursor = 0; }
  const qs = new URLSearchParams({ limit: "40", sort: CRM_STATE.sort });
  if (CRM_STATE.q) qs.set("q", CRM_STATE.q);
  if (CRM_STATE.plan) qs.set("plan", CRM_STATE.plan);
  if (CRM_STATE.state) qs.set("state", CRM_STATE.state);
  if (CRM_STATE.has) qs.set("has", CRM_STATE.has);
  if (CRM_STATE.cursor) qs.set("cursor", String(CRM_STATE.cursor));
  let data;
  try { data = await api(`/api/admin/users?${qs}`); } catch (e) { return accFail(pane, e); }
  $$(".ev-more", pane).forEach((b) => b.remove());
  if (!data.items.length && reset) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("crm.none");
    list.appendChild(empty);
    return;
  }
  data.items.forEach((row) => list.appendChild(clientRow(row, pane)));
  CRM_STATE.cursor = data.next_cursor || 0;
  if (CRM_STATE.cursor) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "ghost ev-more";
    more.textContent = t("common.loadMore");
    more.addEventListener("click", () => loadClients(pane, false));
    list.after(more);
  }
}

function clientRow(u, pane) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "crm-row" + (u.is_blocked ? " blocked" : "");
  const who = document.createElement("span");
  who.className = "crm-who";
  const name = document.createElement("b");
  name.textContent = (u.name || "").trim() || `#${u.id}`;
  const contact = document.createElement("span");
  contact.textContent = [u.email, u.tg && "@" + u.tg, u.login,
                         u.last_seen_at ? t("crm.seen", { d: fmtDate(u.last_seen_at) })
                                        : t("crm.neverSeen")]
    .filter(Boolean).join(" · ");
  who.append(name, contact);
  const plan = document.createElement("span");
  plan.className = "crm-plan" + (u.plan !== "free" ? " paid" : "");
  plan.textContent = u.plan_title + (u.plan_tier ? " " + u.plan_tier.toUpperCase() : "");
  const pts = document.createElement("span");
  pts.className = "crm-pts";
  pts.textContent = tNum(u.points) + " ⚡";
  row.append(who, plan, pts);
  row.addEventListener("click", () => openClientModal(u.id, pane));
  return row;
}

async function openClientModal(uid, pane) {
  let c;
  try { c = await api(`/api/admin/users/${uid}`); } catch (e) { return fail(e); }
  openModal((c.name || "").trim() || `#${c.id}`, (body) => {
    const money = c.money || {};
    body.innerHTML = `
      <div class="crm-stats">
        <div class="lim-card"><span>${escHtml(t("crm.plan"))}</span>
          <b>${escHtml(c.plan_title + (c.plan_tier ? " " + c.plan_tier.toUpperCase() : ""))}</b>
          <span class="lim-sub">${escHtml(c.plan_until ? fmtDate(c.plan_until) : t("crm.noUntil"))}</span></div>
        <div class="lim-card"><span>${escHtml(t("crm.points"))}</span>
          <b>${escHtml(tNum(c.points))}</b>
          <span class="lim-sub">${escHtml(t("crm.spent", { n: tNum(money.spent_points || 0) }))}</span></div>
        <div class="lim-card"><span>${escHtml(t("crm.paid"))}</span>
          <b>${escHtml(fmtUsdCents(money.paid_cents || 0))}</b>
          <span class="lim-sub">${escHtml(t("crm.margin", {
            v: fmtUsdCents(money.margin_cents || 0) }))}</span></div>
        <div class="lim-card"><span>${escHtml(t("crm.work"))}</span>
          <b>${escHtml(tNum((c.work || {}).clips || 0))}</b>
          <span class="lim-sub">${escHtml(t("crm.workNote", {
            p: tNum((c.work || {}).projects || 0),
            s: tNum((c.work || {}).scenes || 0),
            f: fmtBytes((c.work || {}).bytes || 0) }))}</span></div>
      </div>
      <p class="crm-note"></p>
      <label>${escHtml(t("crm.points"))}</label>
      <div class="crm-acts">
        <input class="cl-delta" type="number" value="500" />
        <input class="cl-reason" type="text" />
        <button type="button" class="cl-give primary"></button>
      </div>
      <label>${escHtml(t("crm.plan"))}</label>
      <div class="crm-acts">
        <select class="cl-plan"></select>
        <select class="cl-tier"></select>
        <input class="cl-days" type="number" value="30" />
        <label class="approve-check"><input type="checkbox" class="cl-grant" />
          <span>${escHtml(t("crm.grantNorm"))}</span></label>
        <button type="button" class="cl-save primary"></button>
      </div>
      <div class="crm-acts">
        <button type="button" class="cl-block danger"></button>
        <button type="button" class="cl-events ghost"></button>
        <button type="button" class="cl-files ghost"></button>
      </div>
      <span class="status cl-msg"></span>
      <label>${escHtml(t("crm.history"))}</label>
      <div class="dash-recent cl-actions"></div>`;

    // Что кнопка «отключить» реально сделает. Без этой строки интерфейс
    // обещал бы отменить списание там, где мы его не делаем.
    // Текст берём по КОДУ источника оплаты, а не готовой фразой с сервера:
    // сервер отвечает кодом, язык — дело интерфейса.
    $(".crm-note", body).textContent = t("crm.cancelNote." + (c.pay_source || "none"));
    $(".cl-reason", body).placeholder = t("crm.reasonPh");
    $(".cl-give", body).textContent = t("crm.give");
    $(".cl-save", body).textContent = t("crm.savePlan");
    $(".cl-block", body).textContent = c.is_blocked ? t("crm.unblock") : t("crm.block");
    $(".cl-events", body).textContent = t("crm.ledger");
    $(".cl-files", body).textContent = t("crm.filesOf");

    const planSel = $(".cl-plan", body);
    (c.plans || []).forEach((p) => {
      const o = document.createElement("option");
      o.value = p.id;
      o.textContent = `${p.title} · ${tNum(p.points)}`;
      planSel.appendChild(o);
    });
    planSel.value = c.plan;
    const tierSel = $(".cl-tier", body);
    const fillTiers = () => {
      tierSel.innerHTML = "";
      const list = planSel.value === "studio" ? (c.tiers || []) : [];
      tierSel.classList.toggle("hidden", !list.length);
      list.forEach((tr) => {
        const o = document.createElement("option");
        o.value = tr.id;
        o.textContent = `${tr.id.toUpperCase()} · ${tNum(tr.points)}`;
        tierSel.appendChild(o);
      });
      if (c.plan_tier) tierSel.value = c.plan_tier;
    };
    fillTiers();
    planSel.addEventListener("change", fillTiers);

    const msg = (text, cls = "") => {
      const el = $(".cl-msg", body);
      el.textContent = text;
      el.className = "status cl-msg " + cls;
    };
    const reopen = () => { closeModal(); openClientModal(uid, pane); };

    $(".cl-give", body).addEventListener("click", async () => {
      const delta = Number($(".cl-delta", body).value) || 0;
      if (!delta) return msg(t("crm.needDelta"), "error");
      try {
        const r = await api(`/api/admin/users/${uid}/points`, {
          method: "POST",
          body: { delta, reason: $(".cl-reason", body).value.trim() },
        });
        msg(t("crm.gaveDone", { n: tNum(r.points) }), "done");
        if (pane) loadClients(pane, true);
      } catch (e) { msg(errText(e), "error"); }
    });

    $(".cl-save", body).addEventListener("click", async () => {
      try {
        await api(`/api/admin/users/${uid}/plan`, {
          method: "POST",
          body: {
            plan: planSel.value,
            tier: tierSel.value || "",
            days: Number($(".cl-days", body).value) || 0,
            grant_points: $(".cl-grant", body).checked,
          },
        });
        if (pane) loadClients(pane, true);
        reopen();
      } catch (e) { msg(errText(e), "error"); }
    });

    $(".cl-block", body).addEventListener("click", async () => {
      const blocked = !c.is_blocked;
      const reason = blocked ? (prompt(t("crm.blockWhy")) || "") : "";
      if (blocked && reason === null) return;
      try {
        await api(`/api/admin/users/${uid}/block`, {
          method: "POST", body: { blocked, reason },
        });
        if (pane) loadClients(pane, true);
        reopen();
      } catch (e) { msg(errText(e), "error"); }
    });

    $(".cl-events", body).addEventListener("click", () => openEventsModal(uid));
    $(".cl-files", body).addEventListener("click", () => {
      closeModal();
      openModal(t("crm.filesOf"), (b2) => {
        b2.innerHTML = `<div class="arc-head"><span class="arc-total"></span>
          <span class="muted arc-sub"></span></div>
          <div class="ev-filters arc-kinds"></div>
          <div class="ev-filters arc-scope"></div>
          <div class="arc-grid"></div><span class="status arc-msg"></span>`;
        ARC_STATE.kind = "";
        ARC_STATE.project_id = 0;
        ARC_STATE.days = 0;
        ARC_STATE.cursor = "";
        ARC_STATE.user_id = uid;
        loadFiles(b2, true).finally(() => { ARC_STATE.user_id = 0; });
      }, { medium: true });
    });

    api(`/api/admin/users/${uid}/actions`).then((r) => {
      const box = $(".cl-actions", body);
      if (!r.items.length) {
        box.innerHTML = `<p class="muted">${escHtml(t("crm.noHistory"))}</p>`;
        return;
      }
      r.items.forEach((a) => {
        const row = document.createElement("div");
        row.className = "dash-ev";
        const kind = document.createElement("span");
        kind.className = "dash-ev-kind";
        kind.textContent = t("crm.act." + a.action) || a.action;
        const what = document.createElement("span");
        what.className = "dash-ev-what";
        what.textContent = a.payload;
        const when = document.createElement("span");
        when.className = "dash-ev-when";
        when.textContent = fmtDate(a.at, true);
        row.append(kind, what, when);
        box.appendChild(row);
      });
    }).catch(() => { /* история действий не критична */ });
  }, { medium: true });
}

// ══════════════════════ ВКЛАДКА «РАССЫЛКА» (только админ) ══════════════════════
// Каналов три, и два из них могут быть не подключены. Интерфейс показывает
// это словами («канал не подключён: нет BOT_TOKEN»), а не пустым списком:
// молчащая кнопка «отправить» хуже честного отказа.

const BC_STATE = { channel: "inapp", segment: "all" };

async function renderBroadcastPane(pane) {
  pane.innerHTML = `
    <div class="bc-channels"></div>
    <p class="bc-warn hidden"></p>
    <label>${escHtml(t("bc.segment"))}</label>
    <div class="bc-seg"></div>
    <label>${escHtml(t("bc.subject"))}</label>
    <input class="bc-subject" />
    <label>${escHtml(t("bc.body"))}</label>
    <textarea class="bc-body" rows="6"></textarea>
    <p class="muted" style="font-size:11.5px">${escHtml(t("bc.vars"))}</p>
    <div class="row">
      <button type="button" class="bc-send primary"></button>
      <button type="button" class="bc-test ghost"></button>
      <label class="approve-check"><input type="checkbox" class="bc-trans" />
        <span>${escHtml(t("bc.transactional"))}</span></label>
    </div>
    <span class="status bc-msg"></span>
    <label>${escHtml(t("bc.history"))}</label>
    <div class="bc-list"></div>`;
  $(".bc-subject", pane).placeholder = t("bc.subjectPh");
  $(".bc-body", pane).placeholder = t("bc.bodyPh");
  $(".bc-send", pane).textContent = t("bc.send");
  $(".bc-test", pane).textContent = t("bc.test");

  let data;
  try { data = await api("/api/admin/segments"); } catch (e) { return accFail(pane, e); }
  const channels = data.channels || {};

  const chanBox = $(".bc-channels", pane);
  const warn = $(".bc-warn", pane);
  const drawWarn = () => {
    const st = channels[BC_STATE.channel] || {};
    const off = !st.enabled;
    warn.classList.toggle("hidden", !off && !st.note && !st.note_code);
    // Чего не хватает: два РАВНОПРАВНЫХ пути (Resend или SMTP). Связку «или»
    // пишем здесь, на языке интерфейса, — сервер отдаёт только имена
    // переменных окружения.
    const need = (st.missing || []).join(", ");
    const alt = (st.missing_alt || []).join(" + ");
    warn.textContent = off
      ? t("bc.off", { what: alt ? t("bc.orAlt", { a: need, b: alt }) : need })
      // Пояснение — по КОДУ канала; текст с сервера всегда по-русски и
      // остаётся лишь запасным вариантом для канала без перевода.
      : (tHas("bc.note." + st.note_code) ? t("bc.note." + st.note_code) : (st.note || ""));
    $(".bc-send", pane).disabled = off;
    $(".bc-test", pane).disabled = off;
  };
  ["inapp", "tg", "email"].forEach((key) => {
    const st = channels[key] || {};
    const b = document.createElement("button");
    b.type = "button";
    b.className = "bc-chan" + (key === BC_STATE.channel ? " on" : "")
      + (st.enabled ? "" : " off");
    b.textContent = t("bc.chan." + key) + (st.enabled ? "" : " ✕");
    b.addEventListener("click", () => {
      BC_STATE.channel = key;
      $$(".bc-chan", chanBox).forEach((x) => x.classList.toggle("on", x === b));
      drawSegments();
      drawWarn();
    });
    chanBox.appendChild(b);
  });

  const segBox = $(".bc-seg", pane);
  const drawSegments = () => {
    segBox.innerHTML = "";
    (data.segments || []).forEach((sg) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "bc-seg-row" + (sg.id === BC_STATE.segment ? " on" : "");
      const col = document.createElement("span");
      col.style.minWidth = "0";
      const b = document.createElement("b");
      b.textContent = t("bc.seg." + sg.id) || sg.id;
      const sub = document.createElement("span");
      sub.style.display = "block";
      // Определение сегмента — на языке интерфейса. sg.title с сервера всегда
      // по-русски (SEGMENTS в crm.py), и в английском кабинете под английским
      // названием висело русское объяснение. Серверное описание остаётся
      // запасным вариантом: новый сегмент появится раньше своего перевода.
      sub.textContent = tHas("bc.segNote." + sg.id) ? t("bc.segNote." + sg.id) : sg.title;
      col.append(b, sub);
      const n = document.createElement("span");
      n.className = "bc-seg-n";
      // Два числа, а не одно: «сегмент 900 человек» ничего не значит, если
      // почты нет ни у кого. Второе — сколько из них мы физически достанем.
      n.textContent = `${tNum(sg.count)} · ${tNum(sg.reach[BC_STATE.channel] || 0)} ✉`;
      row.append(col, n);
      row.addEventListener("click", () => {
        BC_STATE.segment = sg.id;
        $$(".bc-seg-row", segBox).forEach((x) => x.classList.toggle("on", x === row));
      });
      segBox.appendChild(row);
    });
  };
  drawSegments();
  drawWarn();

  const msg = (text, cls = "") => {
    const el = $(".bc-msg", pane);
    el.textContent = text;
    el.className = "status bc-msg " + cls;
  };

  const fire = async (test) => {
    const bodyText = $(".bc-body", pane).value.trim();
    if (!bodyText) return msg(t("bc.needBody"), "error");
    try {
      const c = await api("/api/admin/campaigns", {
        method: "POST",
        body: {
          title: $(".bc-subject", pane).value.trim() || t("bc.untitled"),
          channel: BC_STATE.channel, segment: BC_STATE.segment,
          subject: $(".bc-subject", pane).value.trim(),
          body: bodyText,
          transactional: $(".bc-trans", pane).checked,
        },
      });
      const r = await api(`/api/admin/campaigns/${c.id}/send${test ? "?test=1" : ""}`,
                          { method: "POST" });
      msg(t("bc.started", { n: tNum(r.total),
                            word: tPlural(r.total, tRaw("bc.person")) }), "done");
      setTimeout(() => loadCampaigns(pane), 1500);
    } catch (e) { msg(errText(e), "error"); }
  };
  $(".bc-send", pane).addEventListener("click", () => {
    if (!confirm(t("bc.confirm"))) return;
    fire(false);
  });
  $(".bc-test", pane).addEventListener("click", () => fire(true));
  await loadCampaigns(pane);
}

async function loadCampaigns(pane) {
  const box = $(".bc-list", pane);
  let data;
  try { data = await api("/api/admin/campaigns"); } catch (e) { return; }
  box.innerHTML = "";
  if (!data.items.length) {
    box.innerHTML = `<p class="muted">${escHtml(t("bc.none"))}</p>`;
    return;
  }
  data.items.forEach((c) => {
    const row = document.createElement("div");
    row.className = "bc-item";
    const title = document.createElement("b");
    title.textContent = c.subject || c.title;
    const stat = document.createElement("span");
    stat.className = "muted";
    stat.textContent = `${t("bc.chan." + c.channel)} · ${t("bc.seg." + c.segment) || c.segment}`
      + ` · ${t("bc.result", { s: tNum(c.sent), f: tNum(c.failed), r: tNum(c.read) })}`;
    const when = document.createElement("span");
    when.className = "bc-when";
    when.textContent = c.status === "sending" ? t("bc.sending") : fmtDate(c.at, true);
    row.append(title, stat, when);
    box.appendChild(row);
  });
}

// ────────── входящие в приложении: плашка по каналу inapp ──────────
// Канал, который работает без единого внешнего ключа. Для «у тебя осталось
// 40 очков» это точнее письма и не требует ни DMARC, ни диалога с ботом.

async function checkNotices() {
  if (!me || !me.authed) return;
  let data;
  try { data = await api("/api/notices"); } catch (e) { return; }
  const item = (data.items || [])[0];
  if (!item || $(".notice-dock")) return;
  const dock = document.createElement("div");
  dock.className = "notice-dock";
  const col = document.createElement("div");
  const b = document.createElement("b");
  b.textContent = item.title || "";
  const p = document.createElement("p");
  p.textContent = item.body || "";
  col.append(b, p);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "✕";
  close.addEventListener("click", async () => {
    dock.remove();
    try { await api(`/api/notices/${item.id}/read`, { method: "POST" }); } catch (e) { /* прочитаем позже */ }
    checkNotices();
  });
  dock.append(col, close);
  document.body.appendChild(dock);
}

// ────────── витрина в звёздах (только внутри Telegram) ──────────
//
// Ни долларов, ни рублей здесь нет намеренно: показывать внутри Telegram
// сравнение со своей ценой на сайте нельзя — это и есть тот steering, за
// который приложение сначала прячут из мобильных клиентов, а потом удаляют.
// Годовой период и ULTRA в этой витрине отсутствуют: у звёздной подписки
// период ровно один (30 дней) и потолок 10000 ⭐.

function starsCard(kind, row) {
  const price = row.discount_pct
    ? `<span class="stars-old">${escHtml(tNum(row.xtr_base))} ⭐</span>${escHtml(tNum(row.xtr))} ⭐`
    : `${escHtml(tNum(row.xtr))} ⭐`;
  const title = kind === "plan"
    ? escHtml(row.title)
    : escHtml(t("stars.packTitle", { n: tNum(row.points) }));
  const note = kind === "plan"
    ? escHtml(t("stars.planNote", { n: tNum(row.points), days: row.period_days || 30 }))
    : escHtml(t("stars.packNote"));
  return `<div class="stars-card" data-kind="${kind}" data-id="${escHtml(row.id)}">
    <div class="stars-top"><b>${title}</b><span class="stars-price">${price}</span></div>
    <p class="stars-note">${note}</p>
    <button type="button" class="primary stars-buy">${escHtml(t("stars.buy"))}</button>
  </div>`;
}

async function renderStarsPane(pane) {
  let d;
  try { d = await api("/api/tg/pricing"); } catch (e) { return accFail(pane, e); }
  if (!d.enabled) {
    pane.innerHTML = `<p class="muted">${escHtml(t("stars.off"))}</p>`;
    return;
  }
  const sub = d.subscription || {};
  // Telegram разрешает НЕСКОЛЬКО одновременных подписок одного человека на
  // одного бота и второй счёт оплатится молча. Поэтому при живой звёздной
  // подписке кнопок «купить тариф» не показываем вообще — только управление
  // текущей и докупку очков.
  const hasSub = sub.provider === "stars" && sub.active;
  const plans = hasSub ? [] : (d.plans || []).filter((p) => p.available && p.id !== d.current);
  const packs = (d.packs || []).filter((p) => p.available && d.topup_allowed);
  pane.innerHTML = `
    <div class="acc-stats">
      <div class="acc-stat"><b>${escHtml(String(d.current || "free").toUpperCase())}</b><span>${escHtml(t("account.statPlan"))}</span></div>
      <div class="acc-stat"><b>${escHtml(tNum(d.points))}</b><span>${escHtml(t("account.statPoints"))}</span></div>
    </div>
    ${sub.provider === "stars" ? `<p class="stars-state">${escHtml(t("stars.subLine", {
        plan: String(sub.plan || "").toUpperCase(), xtr: tNum(sub.xtr || 0),
        days: sub.period_days || 30, date: sub.until ? fmtDate(sub.until) : "—",
      }))}<br>${escHtml(t("stars.subWhere"))}</p>` : ""}
    ${hasSub ? `<p class="muted acc-note">${escHtml(t("stars.subOnly"))}</p>` : ""}
    <div class="stars-grid">${plans.map((p) => starsCard("plan", p)).join("")}</div>
    ${packs.length ? `<label>${escHtml(t("stars.packsLabel"))}</label>
      <div class="stars-grid">${packs.map((p) => starsCard("topup", p)).join("")}</div>` : ""}
    ${!d.topup_allowed && (d.packs || []).length
      ? `<p class="muted acc-note">${escHtml(t("stars.packsNeedPlan"))}</p>` : ""}
    ${sub.provider === "stars" && sub.active
      ? `<div class="row"><button type="button" class="danger stars-cancel">${escHtml(t("account.autopayOff"))}</button></div>` : ""}
    <span class="acc-msg status"></span>`;

  // Длинная витрина + свайп вниз = случайно закрытое приложение посреди
  // покупки. На этом экране свайп-закрытие придерживаем.
  TGA.swipeGuard(true);

  $$(".stars-buy", pane).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".stars-card");
      const kind = card.dataset.kind;
      const id = card.dataset.id;
      btn.disabled = true;
      accMsg(pane, "");
      // Снимок ДО оплаты: выдачу мы определяем по изменению кабинета, а не по
      // ответу openInvoice — «paid» не значит, что очки уже начислены.
      let before = { plan: d.current, points: d.points };
      try {
        const st = await TGA.pay(kind, id, () => {});
        if (st === "cancelled") { btn.disabled = false; return; }
        if (st === "failed") {
          btn.disabled = false;
          accMsg(pane, t("stars.failed"), "error");
          return;
        }
        accMsg(pane, t("stars.checking"));
        const got = await TGA.waitGrant(before, 90);
        if (got) {
          me = await api("/api/me").catch(() => me);
          renderUserBar();
          await renderStarsPane(pane);
          accMsg(pane, t("stars.done"), "done");
        } else {
          // Деньги взяты, выдача задерживается — это правда, и говорить надо
          // именно её. Суточная сверка догонит платёж сама.
          accMsg(pane, t("stars.slow"), "error");
          btn.disabled = false;
        }
      } catch (e) {
        btn.disabled = false;
        TGA.oops();
        accMsg(pane, errText(e), "error");
      }
    });
  });

  const cancel = $(".stars-cancel", pane);
  if (cancel) {
    cancel.addEventListener("click", () => {
      TGA.confirm(t("stars.cancelAsk"), async (yes) => {
        if (!yes) return;
        cancel.disabled = true;
        accMsg(pane, t("account.autopayOffBusy"));
        try {
          const r = await api("/api/billing/cancel", { method: "POST" });
          await renderStarsPane(pane);
          accMsg(pane, r && r.stars_cancelled === false
            ? t("stars.cancelPartial") : t("account.autopayOffDone"),
            r && r.stars_cancelled === false ? "error" : "done");
        } catch (e) {
          cancel.disabled = false;
          accMsg(pane, errText(e), "error");
        }
      });
    });
  }
}

// ────────── вкладка «Тариф» ──────────
// Ступень, выбранная в кабинете: {plan_id: индекс}. Живёт вне функции —
// панель перерисовывается на каждый клик, и выбор обязан это пережить.
let accTierPick = {};

async function renderPlanPane(pane) {
  // Внутри Telegram цифровой товар продаётся ТОЛЬКО за звёзды: внешняя
  // платёжная ссылка здесь — нарушение правил платформы. На обычном сайте
  // всё остаётся как было, ЮKassa и Stripe.
  if (window.TGA && TGA.active) return renderStarsPane(pane);
  let data;
  try { data = await api("/api/billing/plans"); } catch (e) { return accFail(pane, e); }
  // Контракт витрины: новый (usd + providers) и старый (price в рублях +
  // enabled) читаются одинаково — вкладка не должна зависеть от того, какой
  // из них сегодня отдаёт бэкенд.
  const providers = data.providers && typeof data.providers === "object" ? data.providers : null;
  const enabled = providers ? Boolean(providers.stripe || providers.yookassa) : Boolean(data.enabled);
  const current = (data.current && data.current.plan) || data.current || "free";
  // Описание тарифа берём из своего словаря: у бэкенда оно всегда английское,
  // а интерфейс обязан говорить на одном языке. Числа — из API.
  const planNote = (p) => t(`landing.pricing.plans.${p.id}.note`) || p.note || "";
  const planPaid = (p) => (p.usd != null ? Number(p.usd) : Number(p.price)) > 0;
  const curTier = data.current_tier || "";
  // Ступень, выбранная в кабинете. По умолчанию — уже купленная: человек с
  // ULTRA u3 не должен видеть в своей карточке чужой объём.
  const tierOf = (p) => {
    const tiers = p.tiers || [];
    if (!tiers.length) return null;
    if (accTierPick[p.id] == null) {
      const i = tiers.findIndex((x) => x.id === curTier);
      accTierPick[p.id] = p.id === current && i >= 0 ? i : 0;
    }
    return tiers[Math.min(Math.max(accTierPick[p.id], 0), tiers.length - 1)];
  };
  const planMoney = (p) => {
    const tr = tierOf(p);
    if (tr) return ldMoney(tr.usd != null ? tr.usd : tr.usd_cents / 100);
    return p.usd != null ? ldMoney(p.usd) : fmtRub((Number(p.price) || 0) * 100);
  };
  pane.innerHTML = `
    <div class="acc-plans">
      ${(data.plans || []).map((p) => {
        const tr = tierOf(p);
        const pts = tr ? tr.points : p.points;
        // «Текущий» — это тариф И ступень: на ULTRA имя тарифа одно, а
        // объёмов четыре, и путать их значит показывать неверную цену.
        const isCur = p.id === current && (!tr || tr.id === (curTier || (p.tiers[0] || {}).id));
        // Ступени объёма — ползунок, а не ряд кнопок: у ULTRA их четыре,
        // и в узкой карточке они не читаются. Заодно считаем выгоду —
        // насколько очко на этой ступени дешевле, чем на первой.
        let scale = "";
        let saveBadge = "";
        if (tr) {
          const idx = Math.max(0, p.tiers.findIndex((x) => x.id === tr.id));
          const base = p.tiers[0] || {};
          const perBase = (base.usd != null ? base.usd : base.usd_cents / 100) / (base.points || 1);
          const perCur = (tr.usd != null ? tr.usd : tr.usd_cents / 100) / (tr.points || 1);
          const off = perBase > 0 ? Math.round((1 - perCur / perBase) * 100) : 0;
          if (off > 0) saveBadge = `<span class="acc-plan-off">−${off}%</span>`;
          scale = `<div class="acc-scale">
            <input type="range" class="acc-range" min="0" max="${p.tiers.length - 1}"
                   step="1" value="${idx}" data-plan="${escHtml(p.id)}"
                   aria-label="${escHtml(t("plan.tierAria") || "объём")}" />
            <div class="acc-scale-marks">${p.tiers.map((x, i) => `
              <button type="button" class="${i === idx ? "on" : ""}"
                      data-plan="${escHtml(p.id)}" data-idx="${i}">
                <b>${escHtml(ldPointsLabel(x.points))}</b>
                <i>${escHtml(ldMoney(x.usd != null ? x.usd : x.usd_cents / 100))}</i>
              </button>`).join("")}</div>
          </div>`;
        }
        return `<div class="acc-plan${isCur ? " on" : ""}${tr ? " acc-plan-wide" : ""}">
          <div class="acc-plan-top"><b>${escHtml(p.title)}</b><span>${escHtml(planMoney(p))}${saveBadge}</span></div>
          ${scale}
          <p class="acc-plan-note">${escHtml(planNote(p))}</p>
          <p class="acc-plan-points">${escHtml(t("plan.pointsLine", { n: tNum(pts) }))}</p>
          ${isCur
            ? `<span class="acc-plan-cur">${escHtml(t("plan.current"))}</span>`
            : (planPaid(p)
              ? `<button type="button" class="primary acc-pay" data-plan="${escHtml(p.id)}"
                  data-tier="${escHtml(tr ? tr.id : "")}"
                  >${escHtml(tr && p.id === current ? t("plan.changeTier") : t("plan.pay"))}</button>`
              : `<span class="acc-plan-cur muted">${escHtml(t("plan.basic"))}</span>`)}
        </div>`;
      }).join("")}
    </div>
    ${data.next_tier ? `<p class="muted acc-note">${escHtml(t("plan.tierScheduled", {
        tier: data.next_tier.toUpperCase(),
        date: data.plan_until ? fmtDate(data.plan_until) : "",
      }))}</p>` : ""}
    <label>${escHtml(t("plan.promoLabel"))}</label>
    <input class="acc-promo" placeholder="${escHtml(t("plan.promoPh"))}" />
    <span class="acc-msg status"></span>`;

  // Промокод из реферальной ссылки подставляем сам: человек уже пришёл по нему.
  const promo = $(".acc-promo", pane);
  if (refCode) promo.value = refCode;

  $$(".acc-range", pane).forEach((rng) => {
    rng.addEventListener("input", () => {
      accTierPick[rng.dataset.plan] = Number(rng.value) || 0;
      renderPlanPane(pane, data);
    });
  });

  $$(".acc-pay", pane).forEach((btn) => {
    if (!enabled) {
      // Честно: касса не подключена, а не «что-то пошло не так».
      btn.disabled = true;
      btn.textContent = t("plan.payOff");
      btn.title = t("plan.payOffTitle");
      return;
    }
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      accMsg(pane, t("plan.creating"));
      try {
        // Понижение ступени внутри УЖЕ КУПЛЕННОГО тарифа — не платёж: за
        // текущий период заплачено, и новый счёт выставлять не за что.
        // Сервер сам решает, что это: "scheduled" (записал на продление)
        // или "checkout" (нужна доплата — уходим в кассу).
        if (btn.dataset.tier && btn.dataset.plan === current) {
          const r0 = await api("/api/billing/tier",
                               { method: "POST", body: { tier: btn.dataset.tier } });
          if (r0 && r0.action !== "checkout") {
            await renderPlanPane(pane);
            accMsg(pane, t("plan.tierSaved"), "done");
            return;
          }
        }
        const r = await api("/api/billing/create", {
          method: "POST",
          body: { kind: "plan", plan: btn.dataset.plan, tier: btn.dataset.tier || "",
                  period: "month", promo: promo.value.trim() },
        });
        if (r && r.url) { window.location.href = r.url; return; }
        throw new Error(t("plan.noUrl"));
      } catch (e) {
        btn.disabled = false;
        accMsg(pane, errText(e), "error");
      }
    });
  });
  // Тики ступени: перерисовываем панель, а не только цену — от объёма зависят
  // и надпись на кнопке, и отметка «текущий».
  $$(".acc-scale-marks button", pane).forEach((b) => b.addEventListener("click", () => {
    accTierPick[b.dataset.plan] = Number(b.dataset.idx) || 0;
    renderPlanPane(pane);
  }));
  if (!enabled) {
    const note = document.createElement("p");
    note.className = "muted acc-note";
    note.textContent = t("plan.payOffNote");
    pane.insertBefore(note, $(".acc-msg", pane));
  }
}

// ────────── вкладка «Амбассадор» ──────────
async function renderRefPane(pane) {
  let d;
  try { d = await api("/api/ambassador"); } catch (e) { return accFail(pane, e); }
  if (d.is_ambassador) renderRefCabinet(pane, d); else renderRefJoin(pane, d);
}

function renderRefJoin(pane, d) {
  // <b> в строках словаря — наша собственная разметка, поэтому идёт как есть;
  // проценты и суммы подставляются числами.
  pane.innerHTML = `
    <p class="acc-lead">${escHtml(t("ref.joinLead"))}</p>
    <ul class="acc-list">
      <li>${t("ref.joinDiscount", { pct: Number(d.discount_pct) || 0 })}</li>
      <li>${t("ref.joinReward", { pct: Number(d.reward_pct) || 0 })}</li>
      <li>${escHtml(t("ref.joinPayout", { sum: fmtRub(d.min_payout_kopeks) }))}</li>
    </ul>
    <div class="row">
      <button type="button" class="primary acc-join">${escHtml(t("ref.join"))}</button>
      <span class="acc-msg status"></span>
    </div>`;
  const btn = $(".acc-join", pane);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    accMsg(pane, t("ref.joining"));
    try {
      await api("/api/ambassador/join", { method: "POST" });
      await renderRefPane(pane);
    } catch (e) {
      btn.disabled = false;
      accMsg(pane, errText(e), "error");
    }
  });
}

function renderRefCabinet(pane, d) {
  const s = d.stats || {};
  const link = d.link || "";
  const events = d.events || [];
  const payouts = d.payouts || [];
  pane.innerHTML = `
    <label>${escHtml(t("ref.codeLabel"))}</label>
    <div class="acc-copy-row">
      <span class="acc-code">${escHtml(d.code)}</span>
      <button type="button" class="acc-copy" data-copy="${escHtml(d.code)}">${escHtml(t("common.copy"))}</button>
    </div>
    <label>${escHtml(t("ref.linkLabel"))}</label>
    <div class="acc-copy-row">
      <input class="acc-link" readonly value="${escHtml(link)}" />
      <button type="button" class="acc-copy" data-copy="${escHtml(link)}">${escHtml(t("common.copy"))}</button>
    </div>
    <p class="muted acc-note">${escHtml(t("ref.note", {
      discount: Number(d.discount_pct) || 0, reward: Number(d.reward_pct) || 0 }))}</p>

    <div class="acc-stats acc-stats-5">
      <div class="acc-stat"><b>${escHtml(tNum(s.invited))}</b><span>${escHtml(t("ref.statInvited"))}</span></div>
      <div class="acc-stat"><b>${escHtml(tNum(s.buyers))}</b><span>${escHtml(t("ref.statBuyers"))}</span></div>
      <div class="acc-stat"><b>${escHtml(fmtRub(s.accrued_kopeks))}</b><span>${escHtml(t("ref.statAccrued"))}</span></div>
      <div class="acc-stat"><b>${escHtml(fmtRub(s.paid_kopeks))}</b><span>${escHtml(t("ref.statPaid"))}</span></div>
      <div class="acc-stat acc-stat-hi"><b>${escHtml(fmtRub(s.available_kopeks))}</b><span>${escHtml(t("ref.statAvailable"))}</span></div>
    </div>
    <p class="muted acc-note">${escHtml(t("ref.turnover", { sum: fmtRub(s.turnover_kopeks) }))}${
      Number(s.reserved_kopeks) ? escHtml(t("ref.reserved", { sum: fmtRub(s.reserved_kopeks) })) : ""}</p>

    <label>${escHtml(t("ref.eventsLabel"))}</label>
    ${events.length ? `<div class="acc-table-wrap"><table class="acc-table"><tbody>
      ${events.map((e) => `<tr>
        <td class="acc-td-date">${escHtml(fmtDate(e.created_at))}</td>
        <td class="acc-td-who">${escHtml(e.who || "")}</td>
        <td>${escHtml(e.kind === "payment"
          ? t("ref.eventPayment", { sum: fmtRub(e.amount_kopeks) })
          : t("ref.eventVisit"))}</td>
        <td class="acc-td-sum">${e.reward_kopeks ? "+" + escHtml(fmtRub(e.reward_kopeks)) : "—"}</td>
      </tr>`).join("")}
    </tbody></table></div>` : `<p class="muted">${escHtml(t("ref.eventsEmpty"))}</p>`}

    <label>${escHtml(t("ref.detailsLabel"))}</label>
    <textarea class="acc-details" rows="2" placeholder="${escHtml(t("ref.detailsPh"))}">${escHtml(d.payout_details || "")}</textarea>
    <div class="row">
      <button type="button" class="acc-save-details">${escHtml(t("ref.detailsSave"))}</button>
    </div>
    <label>${escHtml(t("ref.payoutLabel"))}</label>
    <div class="row acc-payout-row">
      <input class="acc-payout-sum" type="number" min="0" step="1" placeholder="${escHtml(t("ref.payoutPh"))}" />
      <button type="button" class="primary acc-payout-btn">${escHtml(t("ref.payoutBtn"))}</button>
    </div>
    <p class="muted acc-note">${escHtml(t("ref.payoutNote", { sum: fmtRub(d.min_payout_kopeks) }))}</p>
    <span class="acc-msg status"></span>

    ${payouts.length ? `<label>${escHtml(t("ref.myPayouts"))}</label>
      <div class="acc-payouts">${payouts.map((p) => {
        const st = payoutStatus(p.status);
        return `<div class="acc-payout">
          <b>${escHtml(fmtRub(p.amount_kopeks))}</b>
          <span class="acc-badge ${st.cls}">${escHtml(st.label)}</span>
          <span class="muted">${escHtml(fmtDate(p.created_at))}</span>
          ${p.comment ? `<span class="muted acc-payout-note">${escHtml(p.comment)}</span>` : ""}
        </div>`;
      }).join("")}</div>` : ""}`;

  bindCopy(pane);
  const linkInput = $(".acc-link", pane);
  if (linkInput) linkInput.addEventListener("focus", () => linkInput.select());

  const detailsBtn = $(".acc-save-details", pane);
  detailsBtn.addEventListener("click", async () => {
    detailsBtn.disabled = true;
    accMsg(pane, t("common.saving"));
    try {
      await api("/api/ambassador/details", {
        method: "POST", body: { details: $(".acc-details", pane).value.trim() },
      });
      accMsg(pane, t("ref.detailsSaved"), "done");
    } catch (e) {
      accMsg(pane, errText(e), "error");
    }
    detailsBtn.disabled = false;
  });

  const payBtn = $(".acc-payout-btn", pane);
  payBtn.addEventListener("click", async () => {
    payBtn.disabled = true;
    accMsg(pane, t("ref.payoutBusy"));
    const rub = Number($(".acc-payout-sum", pane).value || 0);
    try {
      await api("/api/ambassador/payout", {
        method: "POST",
        body: {
          // Бэк ждёт копейки; пусто или 0 = «всё доступное».
          amount_kopeks: rub > 0 ? Math.round(rub * 100) : 0,
          details: $(".acc-details", pane).value.trim(),
        },
      });
      await renderRefPane(pane);
      accMsg(pane, t("ref.payoutDone"), "done");
    } catch (e) {
      payBtn.disabled = false;
      accMsg(pane, errText(e), "error");
    }
  });
}

// ────────── вкладка «Выплаты» (только админ) ──────────
async function renderPayoutsPane(pane, status = "new") {
  pane.innerHTML = `<div class="muted acc-loading">${escHtml(t("common.loading"))}</div>`;
  let d;
  try {
    d = await api(`/api/admin/payouts${status ? `?status=${encodeURIComponent(status)}` : ""}`);
  } catch (e) { return accFail(pane, e); }
  const rows = d.payouts || [];
  const opts = [["new", t("payouts.filterNew")], ["paid", t("payouts.filterPaid")],
                ["rejected", t("payouts.filterRejected")], ["", t("payouts.filterAll")]];
  pane.innerHTML = `
    <div class="row acc-filter-row">
      <label class="acc-filter-label">${escHtml(t("payouts.queue"))}</label>
      <select class="acc-filter">
        ${opts.map(([v, label]) => `<option value="${v}"${v === status ? " selected" : ""}>${escHtml(label)}</option>`).join("")}
      </select>
    </div>
    ${rows.length ? `<div class="acc-payouts">${rows.map((p) => {
      const st = payoutStatus(p.status);
      const a = p.ambassador || {};
      const contacts = [a.tg ? "@" + a.tg : "", a.email || ""].filter(Boolean).join(" · ");
      return `<div class="acc-payout acc-payout-admin" data-id="${p.id}">
        <div class="acc-payout-head">
          <b>${escHtml(fmtRub(p.amount_kopeks))}</b>
          <span class="acc-badge ${st.cls}">${escHtml(st.label)}</span>
          <span class="muted">${escHtml(fmtDate(p.created_at, true))}</span>
        </div>
        <div class="acc-payout-who">
          ${escHtml(a.name || "")}${a.code ? ` <span class="acc-code acc-code-sm">${escHtml(a.code)}</span>` : ""}
          ${contacts ? `<span class="muted">${escHtml(contacts)}</span>` : ""}
        </div>
        <div class="acc-payout-details">${escHtml(p.details || t("payouts.noDetails"))}</div>
        ${p.comment ? `<div class="muted acc-payout-note">${escHtml(p.comment)}</div>` : ""}
        ${p.status === "new" ? `<div class="row acc-payout-actions">
          <button type="button" class="primary acc-mark" data-status="paid">${escHtml(t("payouts.markPaid"))}</button>
          <button type="button" class="danger acc-mark" data-status="rejected">${escHtml(t("payouts.markRejected"))}</button>
        </div>` : ""}
      </div>`;
    }).join("")}</div>` : `<p class="muted">${escHtml(t("payouts.empty"))}</p>`}
    <span class="acc-msg status"></span>`;

  $(".acc-filter", pane).addEventListener("change", (e) => renderPayoutsPane(pane, e.target.value));

  $$(".acc-mark", pane).forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".acc-payout");
      $$(".acc-mark", card).forEach((b) => { b.disabled = true; });
      accMsg(pane, t("common.saving"));
      try {
        await api(`/api/admin/payouts/${card.dataset.id}`, {
          method: "POST", body: { status: btn.dataset.status },
        });
        await renderPayoutsPane(pane, status);
      } catch (e) {
        $$(".acc-mark", card).forEach((b) => { b.disabled = false; });
        accMsg(pane, errText(e), "error");
      }
    });
  });
}

let project = null;
let projects = [];
let activeProjectId = Number(localStorage.getItem("rc_project") || 0) || null;
let providers = { video: ["grok"], seedance: false };

// ═══════════════════════════ РЕЖИМЫ (КЛИЕНТ) ═══════════════════════════
// Реестр приезжает с сервера (GET /api/modes → backend/formats.py) и НЕ
// дублируется здесь: вторая копия таблицы «какой вид проекта в каком режиме»
// однажды разъедется с первой, и разъедется молча. Здесь только подписи из
// словаря и то, что из реестра рисуется.
//
// Режим = project.kind. Отдельного переключателя режимов нет намеренно:
// режим выбирается один раз, в момент создания проекта, и меняться потом не
// может — сериал не превращается в клип, у них разные объекты второго уровня.

let modesCatalog = null;
let modesPromise = null;

async function loadModes(force) {
  if (!force && modesCatalog) return modesCatalog;
  if (modesPromise && !force) return modesPromise;
  modesPromise = api(`/api/modes?lang=${encodeURIComponent(LANG)}`)
    .then((d) => { modesCatalog = d; return d; })
    .catch(() => {
      // Реестр не приехал: работаем как клип и БОЛЬШЕ НЕ ДЁРГАЕМСЯ. Без этой
      // записи modesCatalog остался бы null, и поллинг (раз в 3 секунды, пока
      // что-то генерится) бил бы в /api/modes бесконечно.
      modesCatalog = { modes: [], kinds: ["album", "single", "ugc", "series"] };
      return modesCatalog;
    })
    .finally(() => { modesPromise = null; });
  return modesPromise;
}

const CLIP_FALLBACK = {
  id: "clip", object: "track", needs_audio: true, needs_lyrics: true,
  format_catalog: "clip", docs: [], track_docs: [], group_by: "",
  scenes: { min: 12, typ: 30, max: 45, slot: [2, 10] }, formats: [], steps: [],
};

function modeById(id) {
  const list = (modesCatalog && modesCatalog.modes) || [];
  return list.find((m) => m.id === id) || (id === "clip" ? CLIP_FALLBACK : null);
}

/* Режим ОТКРЫТОГО проекта. Пока /api/modes не приехал — клип: он умеет всё,
   что умели проекты до появления режимов, и ничего не ломает. */
function curMode() {
  return modeById((project && project.mode) || "clip") || CLIP_FALLBACK;
}

function modeOfKind(kind) {
  const list = (modesCatalog && modesCatalog.modes) || [];
  return list.find((m) => (m.kinds || []).includes(kind)) || CLIP_FALLBACK;
}

/* Как называется объект второго уровня: трек / ролик / серия. */
function objT(sub, mode) {
  const m = mode || curMode();
  return t(`modes.object.${m.object || "track"}.${sub}`);
}

function modeFormats(mode) { return ((mode || curMode()).formats) || []; }

function formatLabel(mode, key) {
  const f = modeFormats(mode).find((x) => x.key === key);
  if (!f) return "";
  return typeof f.label === "string" ? f.label : (f.label && (f.label[LANG] || f.label.en)) || key;
}

function kindLabel(kind) {
  return t({ single: "top.kindSingle", ugc: "top.kindUgc", series: "top.kindSeries" }[kind]
           || "top.kindAlbum");
}

/* Можно ли добавить ещё один объект. Сингл — ровно один трек, и форму
   «добавить» надо не блокировать, а убирать: заблокированная кнопка,
   которая никогда не разблокируется, — это мусор на экране. */
function canAddObject() {
  if (!project) return false;
  return !(project.kind === "single" && (project.tracks || []).length >= 1);
}

// ────────── сценарные документы ──────────
// Один вид документа = одна строка project.docs. Ручная правка обязательна:
// модель ошибается в именах и мелочах мира, а перегенерировать сезон ради
// одной фразы — это заново платить за весь текст.

function docsOf(scope) {
  const all = (project && project.docs) || [];
  return scope === "project" ? all.filter((d) => !d.track_id) : all;
}

function docBy(kind, trackId) {
  return ((project && project.docs) || []).find(
    (d) => d.kind === kind && (trackId ? d.track_id === trackId : !d.track_id)) || null;
}

function docsBusy() {
  return ((project && project.docs) || [])
    .some((d) => d.status === "queued" || d.status === "running");
}

let pollTimer = null;

async function loadProject() {
  // Обновляем и очки: после каждой генерации бейдж «⚡ N» должен быть честным.
  me = await api("/api/me").catch(() => me);
  renderUserBar();
  projects = await api("/api/projects");
  if (!projects.length) { project = await api("/api/project"); projects = await api("/api/projects"); }
  if (!activeProjectId || !projects.some((p) => p.id === activeProjectId)) {
    activeProjectId = projects[0].id;
  }
  localStorage.setItem("rc_project", activeProjectId);
  project = await api(`/api/project?project_id=${activeProjectId}`);
  if (!providers.loaded) {
    providers = { ...(await api("/api/providers")), loaded: true };
  }
  await loadModes();
  // До отрисовки: автосборка может поставить клип в очередь, и карточка
  // должна показать это сразу, а поллер — не погаснуть.
  await autoAssembleTick();
  render();
  schedulePoll();
  // Отложенный выбор с витрины промтов: трек мог появиться только сейчас.
  if (ldPending) await ldApplyPending();
  renderOnboarding();
  // Входящие по каналу inapp: плашка показывается один раз за загрузку.
  checkNotices();
}

function renderProjectBar() {
  const sel = $("#project-select");
  sel.innerHTML = "";
  for (const p of projects) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name;
    // Вид проекта прямо в option: навигация (nav.js) читает режим отсюда и
    // не заводит второй копии списка проектов.
    o.dataset.kind = p.kind || "album";
    o.dataset.mode = p.mode || "clip";
    if (p.id === activeProjectId) o.selected = true;
    sel.appendChild(o);
  }
  $("#project-kind").textContent = kindLabel(project.kind);
  // Режим — не второй вид проекта, а его прочтение: album/single читаются как
  // «rap clips». Бейдж стоит рядом с видом, чтобы «сингл» не выглядел режимом.
  const modeEl = $("#project-mode");
  const m = curMode();
  modeEl.innerHTML = "";
  const ico = document.createElement("span");
  ico.className = "mode-ico";
  ico.textContent = m.icon || "🎬";
  const cap = document.createElement("span");
  cap.textContent = t(`modes.${m.id}.title`);
  modeEl.append(ico, cap);
  modeEl.title = t(`modes.${m.id}.full`);
  modeEl.classList.remove("hidden");
  const coverImg = $("#project-cover-img");
  if (project.cover_url) {
    coverImg.src = project.cover_url;
    coverImg.classList.remove("hidden");
  } else {
    coverImg.removeAttribute("src");
    coverImg.classList.add("hidden");
  }
}

$("#project-select").addEventListener("change", (e) => {
  activeProjectId = Number(e.target.value);
  localStorage.setItem("rc_project", activeProjectId);
  loadProject();
});

$("#new-project-btn").addEventListener("click", () => {
  openModal(t("modal.newProject.title"), (body) => {
    body.innerHTML = `
      <label>${escHtml(t("modal.newProject.nameLabel"))}</label>
      <input class="np-name" placeholder="${escHtml(t("modal.newProject.namePh"))}" />
      <label>${escHtml(t("modal.newProject.kindLabel"))}</label>
      <!-- Вид проекта И ЕСТЬ режим: album/single читаются как rap clips, ugc —
           канал блогера, series — сериал. Отдельного переключателя режимов
           нет намеренно: режим выбирается один раз и потом не меняется —
           сериал не превращается в клип, у них разные объекты. -->
      <div class="kind-cards">
        ${[["album", "album", "albumNote"], ["single", "single", "singleNote"],
           ["ugc", "ugc", "ugcNote"], ["series", "series", "seriesNote"]]
          .map(([k, lab, note], i) => `<button type="button" class="kind-card${i ? "" : " on"}" data-kind="${k}">
          <b>${escHtml(t("modal.newProject." + lab))}</b><span class="muted">${escHtml(t("modal.newProject." + note))}</span>
        </button>`).join("")}
      </div>
      <label>${escHtml(t("modal.newProject.coverLabel"))}</label>
      <label class="cover-drop">
        <input type="file" class="np-cover hidden" accept="image/jpeg,image/png,image/webp" />
        <img class="np-cover-preview hidden" alt="" />
        <span class="np-cover-hint">${escHtml(t("modal.newProject.coverHint"))}</span>
      </label>
      <div class="row">
        <button type="button" class="primary np-create">${escHtml(t("common.create"))}</button>
        <span class="np-error error hidden"></span>
      </div>`;

    let kind = "album";
    $$(".kind-card", body).forEach((cardBtn) => {
      cardBtn.addEventListener("click", () => {
        kind = cardBtn.dataset.kind;
        $$(".kind-card", body).forEach((el) => el.classList.toggle("on", el === cardBtn));
      });
    });

    const coverInput = $(".np-cover", body);
    coverInput.addEventListener("change", () => {
      const file = coverInput.files[0];
      const preview = $(".np-cover-preview", body);
      if (file) {
        preview.src = URL.createObjectURL(file);
        preview.classList.remove("hidden");
        $(".np-cover-hint", body).textContent = file.name;
      }
    });

    const nameInput = $(".np-name", body);
    const errEl = $(".np-error", body);
    const createBtn = $(".np-create", body);
    const create = async () => {
      const name = nameInput.value.trim();
      if (!name) {
        errEl.textContent = t("modal.newProject.nameRequired");
        errEl.classList.remove("hidden");
        return;
      }
      createBtn.disabled = true;
      try {
        const created = await api("/api/projects", { method: "POST", body: { name, kind } });
        const file = coverInput.files[0];
        if (file) {
          const fd = new FormData();
          fd.append("cover", file);
          await api(`/api/projects/${created.id}/cover`, { method: "POST", body: fd });
        }
        activeProjectId = created.id;
        localStorage.setItem("rc_project", activeProjectId);
        closeModal();
        await loadProject();
      } catch (e) {
        errEl.textContent = errText(e);
        errEl.classList.remove("hidden");
        createBtn.disabled = false;
      }
    };
    createBtn.addEventListener("click", create);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
    nameInput.focus();
  });
});

// Обложка активного проекта: клик по миниатюре в топбаре = заменить.
$("#project-cover-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("cover", file);
  await api(`/api/projects/${activeProjectId}/cover`, { method: "POST", body: fd });
  e.target.value = "";
  await loadProject();
});

// Ожидание промежуточных кадров: у них нет статуса в БД (прогресс — рост
// midframes_json), поэтому «занятость» отслеживаем на клиенте по ожидаемому
// количеству; протухает через 10 минут, чтобы упавшая генерация не крутила поллер.
const midframesExpect = new Map(); // scene.id → {n, ts}

function midframesBusy(s) {
  const exp = midframesExpect.get(s.id);
  if (!exp) return false;
  if ((s.midframes || []).length >= exp.n || Date.now() - exp.ts > 600000) {
    midframesExpect.delete(s.id);
    return false;
  }
  return true;
}

function sceneBusy(s) {
  return ["queued", "running"].includes(s.image_status) ||
    ["queued", "running"].includes(s.video_status) ||
    midframesBusy(s);
}

// ═════════════ ПОЛЛИНГ: статусы отдельно от перерисовки ═════════════
//
// Было: пока хоть одна сцена в очереди, каждые три секунды загружался ВЕСЬ
// проект (/api/me + /api/projects + /api/project), а потом render() сносил
// #tracks целиком и собирал заново. На альбоме из десяти треков это около
// 68 000 узлов и под тысячу медиа-элементов каждые три секунды — ровно то,
// что человек называет «лагает пиздец».
//
// Стало: поллер дёргает /api/project/status (единицы килобайт: id и
// статусы) и ТОЧЕЧНО правит статусные узлы. Полная перезагрузка — только
// когда работа реально доделалась, то есть появился новый файл.

let pollIdle = 0;               // сколько опросов подряд ничего не изменилось

function projectBusy() {
  return project.story_status === "queued" || project.story_status === "running" ||
    // Сценарные документы живут в том же ответе и имеют тот же status/error.
    docsBusy() ||
    project.tracks.some(
      (t) => ["queued", "running"].includes(t.scenes_status) ||
        ["queued", "running"].includes(t.storyboard_status) ||
        ["queued", "running"].includes(t.clip_status) ||
        ["queued", "running"].includes(t.supergen_status) ||
        (t.scenes || []).some(sceneBusy),
    );
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!projectBusy()) { pollIdle = 0; return; }
  // Пауза растёт, пока ничего не меняется: генерация видео идёт минутами, и
  // долбить сервер каждые три секунды всё это время незачем.
  const wait = Math.min(12000, 2500 + pollIdle * 700);
  pollTimer = setTimeout(pollStatus, wait);
}

async function pollStatus() {
  const st = await api(`/api/project/status?project_id=${activeProjectId}`)
    .catch(() => null);
  if (!st) { pollIdle += 1; schedulePoll(); return; }
  if (applyStatus(st)) {
    // Что-то доделалось — вот теперь нужна полная загрузка: появились новые
    // файлы, ссылки и кнопки.
    pollIdle = 0;
    await loadProject();
    return;
  }
  pollIdle += 1;
  schedulePoll();
}

/* Разложить лёгкий ответ по DOM. Возвращает true, если нужна полная
   перезагрузка: изменился состав файлов или работа ушла из очереди. */
function applyStatus(st) {
  let reload = false;
  if (st.story_status !== project.story_status) {
    project.story_status = st.story_status;
    const el = $("#story-status");
    if (el) {
      const lab = statusLabel(st.story_status);
      el.textContent = lab.text;
      el.className = "status " + lab.cls;
    }
    if (!["queued", "running"].includes(st.story_status)) reload = true;
  }
  (st.docs || []).forEach((d) => {
    const local = ((project.docs || []).find((x) => x.id === d.id));
    if (local && local.status !== d.status) {
      local.status = d.status;
      local.error = d.error;
      reload = true;                  // тело документа приезжает только целиком
    }
  });
  (st.tracks || []).forEach((ts) => {
    const tr = project.tracks.find((x) => x.id === ts.id);
    if (!tr) { reload = true; return; }
    const card = $(`.track-card[data-id="${ts.id}"]`);
    reload = trackStatus(tr, ts, card) || reload;
    (ts.scenes || []).forEach((ss) => {
      const sc = sceneByIdIndex.get(ss.id);
      if (!sc) { reload = true; return; }
      reload = sceneStatus(sc, ss, card) || reload;
    });
    if ((ts.scenes || []).length !== (tr.scenes || []).length) reload = true;
  });
  return reload;
}

function paint(card, sel, status, doneWord) {
  if (card) setStatus($(sel, card), status, doneWord);
}

function trackStatus(tr, ts, card) {
  let reload = false;
  const pairs = [
    ["scenes_status", ".scenes-status"],
    ["storyboard_status", ".sb-status"],
    ["clip_status", ".clip-status"],
  ];
  const busy = (v) => ["queued", "running"].includes(v);
  pairs.forEach(([key, sel]) => {
    if (tr[key] === ts[key]) return;
    // Полная перезагрузка нужна, только когда работа ВЫШЛА из очереди: тогда
    // появился файл, ссылка и новые кнопки. Переход «в очереди → генерирую»
    // ничего нового не создаёт — это просто другая подпись.
    const finished = busy(tr[key]) && !busy(ts[key]);
    tr[key] = ts[key];
    paint(card, sel, ts[key]);
    if (finished) reload = true;
  });
  if (tr.supergen_status !== ts.supergen_status) {
    const finished = busy(tr.supergen_status) && !busy(ts.supergen_status);
    tr.supergen_status = ts.supergen_status;
    if (finished) reload = true;
  }
  if (card && tr.supergen_note !== ts.supergen_note) {
    tr.supergen_note = ts.supergen_note;
    const note = $(".supergen-note", card);
    if (note) note.textContent = ts.supergen_note || "";
  }
  if (Boolean(tr.clip_url) !== Boolean(ts.clip_url)) reload = true;
  if (Boolean(tr.storyboard_url) !== Boolean(ts.storyboard_url)) reload = true;
  return reload;
}

function sceneStatus(sc, ss, card) {
  let reload = false;
  // Новый файл виден по флагам has_*: именно ради него и нужна полная
  // перезагрузка — статуса мало, у кадра меняется картинка и кнопки.
  if (Boolean(sc.image_url) !== ss.has_image
      || Boolean(sc.image_last_url) !== ss.has_last
      || Boolean(sc.video_url) !== ss.has_video
      || (sc.midframes || []).length !== ss.mid) {
    return true;
  }
  [["image_status", ".s-image-status"], ["video_status", ".s-video-status"]]
    .forEach(([key, sel]) => {
      if (sc[key] === ss[key]) return;
      const wasBusy = ["queued", "running"].includes(sc[key]);
      sc[key] = ss[key];
      const holder = card ? $(`.scene-card[data-id="${sc.id}"]`, card) : null;
      if (holder) setStatus($(sel, holder) || $(".s-anim-status", holder), ss[key]);
      // Плитка сетки: точка статуса вместо строки текста.
      const tile = card ? $(`.scene-tile[data-id="${sc.id}"]`, card) : null;
      if (tile) {
        const isAnim = key === "video_status";
        const dot = $(".st-dot", tile);
        if (dot) {
          dot.className = "st-dot " + tileDotClass(
            ss[key], isAnim ? Boolean(sc.video_url) : Boolean(sc.image_url));
        }
      }
      if (wasBusy && !["queued", "running"].includes(ss[key])) reload = true;
    });
  return reload;
}

// ═════════════════════ автосборка клипа ═════════════════════
// Галочка «автосборка» живёт на треке: как только у трека появляется новое
// готовое видео сцены, клип пересобирается сам. Бэкенд не трогаем — флаг
// хранится в localStorage по id трека, а решение принимается на клиенте,
// на том же поллинге статусов, что уже крутится (schedulePoll → loadProject).
const AUTOASM_KEY = "rc_autoasm";

function autoAsmMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(AUTOASM_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch (e) {
    return {};   // приватный режим или мусор в ключе — считаем, что выключено
  }
}

function autoAsmOn(trackId) {
  return Boolean(autoAsmMap()[String(trackId)]);
}

function setAutoAsm(trackId, on) {
  const map = autoAsmMap();
  if (on) map[String(trackId)] = true;
  else delete map[String(trackId)];
  try { localStorage.setItem(AUTOASM_KEY, JSON.stringify(map)); } catch (e) { /* приватный режим */ }
}

// Снимок «какие видео сцен трек уже показывал» с прошлого опроса: по нему и
// видно новое готовое видео. undefined = трека ещё не видели (первый проход
// после загрузки страницы) — тогда только запоминаем, не собираем.
const autoAsmSeen = new Map();      // trackId → сигнатура готовых видео
const autoAsmFlight = new Set();    // трек, по которому сборка уже отправлена
const autoAsmNote = new Map();      // trackId → текст ошибки под галочкой

function videosSig(tr) {
  return (tr.scenes || [])
    .filter((s) => s.video_url)
    .map((s) => `${s.id}:${s.video_url}`)
    .sort()
    .join("|");
}

// Один проход по всем трекам проекта. Вызывается из loadProject ДО render(),
// поэтому статусы, которые мы поменяли, сразу видны и в разметке, и в
// schedulePoll — иначе поллинг погас бы, не дождавшись нашей же сборки.
async function autoAssembleTick() {
  for (const tr of project.tracks || []) {
    const prev = autoAsmSeen.get(tr.id);
    const sig = videosSig(tr);
    autoAsmSeen.set(tr.id, sig);

    // Наша сборка в полёте: ждём, пока сервер её доведёт. Статус вернулся из
    // «queued/running» — значит, отработала (успешно или с ошибкой).
    if (autoAsmFlight.has(tr.id)) {
      if (["queued", "running"].includes(tr.clip_status)) continue;
      autoAsmFlight.delete(tr.id);
    }
    if (!autoAsmOn(tr.id)) continue;
    if (prev === undefined) continue;                                  // первый проход — только базовый снимок
    if (sig === prev) continue;                                        // ничего нового не появилось
    if (["queued", "running"].includes(tr.clip_status)) continue;      // сервер уже собирает (в т.ч. супергенерация)

    // Новые видео сами идут в клип: без этого пересборка дала бы ту же
    // склейку. Сцены, снятые владельцем с галочки вручную, не трогаем —
    // «новым» считается только видео, которого в прошлом снимке не было.
    const prevSet = new Set(prev.split("|").filter(Boolean));
    const fresh = (tr.scenes || []).filter(
      (s) => s.video_url && !s.approved && !prevSet.has(`${s.id}:${s.video_url}`),
    );
    try {
      for (const s of fresh) {
        await api(`/api/scenes/${s.id}/approve`, { method: "POST", body: { approved: true } });
        s.approved = true;
      }
      const inClip = (tr.scenes || []).filter((s) => s.video_url && s.approved).length;
      if (!inClip) continue;                                           // собирать нечего — бэк ответил бы 400
      autoAsmFlight.add(tr.id);
      await api(`/api/tracks/${tr.id}/assemble`, { method: "POST" });
      // Сервер поставил сборку в очередь — отражаем это здесь же, чтобы
      // карточка сразу показала «собираю», а поллер не заснул.
      tr.clip_status = "queued";
      tr.approved_count = inClip;
      autoAsmNote.delete(tr.id);
    } catch (e) {
      autoAsmFlight.delete(tr.id);
      autoAsmNote.set(tr.id, errText(e));
    }
  }
}

function fmtTime(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* Проставить статус, НЕ потеряв опознавательный класс элемента.
   Раньше здесь стояло el.className = "status " + cls — и первая же
   отрисовка стирала с узла его собственный класс (.scenes-status, .sb-status,
   .clip-status, .s-image-status). Дальше $(".scenes-status", card) находил
   пустоту, и точечное обновление статуса было физически невозможно: любое
   изменение требовало снести и собрать карточку заново. */
function setStatus(el, status, doneWord) {
  if (!el) return;
  const own = (el.className || "").split(" ").find((c) => c && c !== "status") || "";
  const lab = statusLabel(status, doneWord);
  el.textContent = lab.text;
  el.className = (own ? own + " " : "") + "status" + (lab.cls ? " " + lab.cls : "");
}

function statusLabel(status, doneWord) {
  if (status === "queued") return { text: t("status.queued"), cls: "" };
  if (status === "running") return { text: t("status.running"), cls: "" };
  if (status === "error") return { text: t("status.error"), cls: "error" };
  if (status === "done") return { text: doneWord || t("status.done"), cls: "done" };
  return { text: "", cls: "" };
}

/* Какие панели видит режим. Панель сюжета — только у клипа: у сериала её
   место занимает библия сезона, у UGC — персона блогера, и держать оба
   блока на экране значило бы предлагать написать сюжет дважды. */
function applyMode() {
  const m = curMode();
  const storyPanel = $("#story").closest(".panel");
  const docsPanel = $("#docs-panel");
  const isClip = m.id === "clip";
  storyPanel.classList.toggle("hidden", !isClip);
  docsPanel.classList.toggle("hidden", isClip);
  $("#tracks-title").textContent = objT("many", m);
  // Заголовок персонажей тоже режимный: «Персонажи альбома» над сквозными
  // героями сериала читается как чужая надпись из другого приложения.
  const charsTitle = $("[data-i18n='chars.title']");
  if (charsTitle) charsTitle.textContent = objT("chars", m) || t("chars.title");
  const addPanel = $("#add-track-panel");
  addPanel.classList.toggle("hidden", !canAddObject());
  const summary = $("#add-track-panel .add-track > summary");
  if (summary) summary.textContent = objT("add", m);
  // Лирика и аудио — свойства клипа: у ролика реплики пишет генератор, у
  // серии их пишет сценарий. Показывать пустые поля «текст песни» в сериале
  // значит предлагать заполнить то, что никуда не пойдёт.
  const form = $("#add-track-form");
  if (form) {
    form.lyrics.classList.toggle("hidden", !m.needs_lyrics);
    form.audio.classList.toggle("hidden", !m.needs_audio);
    const fw = $("#add-format-wrap");
    const fsel = $("#add-format");
    const list = modeFormats(m);
    fw.classList.toggle("hidden", !list.length);
    if (list.length && fsel.dataset.mode !== m.id) {
      fsel.dataset.mode = m.id;
      fsel.innerHTML = "";
      list.forEach((f) => {
        const o = document.createElement("option");
        o.value = f.key;
        o.textContent = formatLabel(m, f.key);
        fsel.appendChild(o);
      });
    }
  }
}

// Индекс «сцена → трек». Строится один раз на перерисовку: без него каждая
// карточка кадра искала свой трек линейным обходом всего проекта.
let sceneTrackIndex = new Map();
let sceneByIdIndex = new Map();

function buildSceneIndex() {
  sceneTrackIndex = new Map();
  sceneByIdIndex = new Map();
  ((project && project.tracks) || []).forEach((tr) => {
    (tr.scenes || []).forEach((sc) => {
      sceneTrackIndex.set(sc.id, tr);
      sceneByIdIndex.set(sc.id, sc);
    });
  });
}

function sceneTrack(id) { return sceneTrackIndex.get(id) || null; }

function render() {
  buildSceneIndex();
  renderProjectBar();
  applyMode();
  renderDocs();
  $("#project-name").value = project.name;
  $("#character-bible").value = project.character_bible;
  $("#story").value = project.story;
  const st = statusLabel(project.story_status);
  const statusEl = $("#story-status");
  statusEl.textContent = st.text;
  statusEl.className = "status " + st.cls;
  $("#gen-story-btn").disabled = project.story_status === "queued" || project.story_status === "running";

  const charsBox = $("#characters");
  charsBox.innerHTML = "";
  (project.characters || []).forEach((c) => charsBox.appendChild(renderCharacter(c)));
  // «Добавить» — не кнопка под лентой, а последняя карточка В ленте: пустое
  // место в ряду само подсказывает, куда нажать, и не ломает ритм карточек.
  const addCard = document.createElement("button");
  addCard.type = "button";
  addCard.className = "char-card char-add";
  addCard.innerHTML = '<span class="char-add-plus">+</span><span class="char-add-cap"></span>';
  $(".char-add-cap", addCard).textContent = t("chars.add") || "добавить персонажа";
  addCard.addEventListener("click", () => $("#add-character-btn").click());
  charsBox.appendChild(addCard);
  // Стрелки ленты персонажей статичны (лежат в разметке, а не в шаблоне) —
  // вешаем обработчики один раз, иначе каждый опрос добавлял бы ещё пару.
  const charsWrap = charsBox.closest(".strip-wrap");
  if (charsWrap && !charsWrap.dataset.bound) {
    charsWrap.dataset.bound = "1";
    bindStrip(charsWrap);
  }

  const container = $("#tracks");
  container.innerHTML = "";
  const mode = curMode();
  if (mode.group_by === "season_no") {
    // Сезон — не таблица, а колонка Track.season_no: группировка живёт здесь,
    // в отрисовке. Заводить ради номера отдельную сущность значило бы менять
    // внешний ключ у самой горячей таблицы проекта.
    const seasons = [...new Set(project.tracks.map((tr) => tr.season_no || 0))]
      .sort((a, b) => a - b);
    seasons.forEach((n) => {
      const head = document.createElement("div");
      head.className = "season-head";
      head.textContent = n ? t("modes.season", { n }) : objT("many", mode);
      container.appendChild(head);
      project.tracks.filter((tr) => (tr.season_no || 0) === n)
        .forEach((tr) => container.appendChild(renderTrack(tr)));
    });
  } else {
    project.tracks.forEach((tr) => container.appendChild(renderTrack(tr)));
  }
  if (!project.tracks.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = objT("empty", mode);
    container.appendChild(empty);
  }
}


// ═══════════════ СЦЕНАРНЫЕ ДОКУМЕНТЫ: сезонный слой ═══════════════
// Панель одна на два режима: у сериала это библия сезона + поэпизодный план,
// у UGC — персона блогера + формула локации. Разводит их только список видов
// документов из реестра режима, а не две отдельные панели: рисование,
// правка, статусы и поллинг у них общие.

function docTitle(kind) { return t("docs." + kind) || kind; }

function docCard(doc, kind) {
  const card = document.createElement("div");
  card.className = "doc-card";
  const busy = doc && (doc.status === "queued" || doc.status === "running");
  if (busy) card.classList.add("is-busy");
  if (doc && doc.status === "error") card.classList.add("is-error");

  const head = document.createElement("div");
  head.className = "doc-head";
  const name = document.createElement("b");
  name.textContent = docTitle(kind);
  const status = document.createElement("span");
  const st = statusLabel(doc ? doc.status : "");
  status.className = "status " + st.cls;
  status.textContent = doc && doc.status === "error" ? (doc.error || st.text) : st.text;
  const acts = document.createElement("div");
  acts.className = "doc-acts";
  head.append(name, status, acts);
  card.appendChild(head);

  const body = document.createElement("div");
  body.className = "doc-body" + (doc && doc.body ? "" : " muted");
  body.textContent = (doc && doc.body) || t("docs.empty");
  card.appendChild(body);

  // Поэпизодный план — сетка карточек, а не простыня: по ней сразу видно,
  // где сезон провисает и какая серия ничем не заканчивается.
  if (kind === "beatsheet" && doc && doc.data && (doc.data.episodes || []).length) {
    body.classList.add("hidden");
    const grid = document.createElement("div");
    grid.className = "ep-grid";
    (doc.data.episodes || []).forEach((r, i) => {
      const c = document.createElement("div");
      c.className = "ep-card";
      const no = document.createElement("span");
      no.className = "ep-no";
      no.textContent = t("modes.episodeNo", { n: r.no || i + 1 });
      const title = document.createElement("b");
      title.textContent = r.title || "";
      c.append(no, title);
      [["event", r.event], ["changes", r.changes], ["cliffhanger", r.cliffhanger]]
        .forEach(([, val]) => {
          if (!val) return;
          const line = document.createElement("div");
          line.className = "ep-line";
          line.textContent = val;
          c.appendChild(line);
        });
      grid.appendChild(c);
    });
    card.appendChild(grid);
  }

  if (!busy) {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = t("docs.edit");
    edit.addEventListener("click", () => openDocEditor(kind, doc));
    acts.appendChild(edit);
  }
  return card;
}

function openDocEditor(kind, doc) {
  openModal(docTitle(kind), (box) => {
    const ta = document.createElement("textarea");
    ta.rows = 14;
    ta.value = (doc && doc.body) || "";
    const row = document.createElement("div");
    row.className = "row";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.textContent = t("docs.save");
    const msg = document.createElement("span");
    msg.className = "status";
    save.addEventListener("click", async () => {
      save.disabled = true;
      msg.textContent = t("common.saving");
      try {
        await api(`/api/projects/${activeProjectId}/docs`, {
          method: "POST",
          body: { kind, body: ta.value, track_id: (doc && doc.track_id) || 0 },
        });
        closeModal();
        await loadProject();
      } catch (e) {
        save.disabled = false;
        msg.textContent = errText(e);
        msg.className = "status error";
      }
    });
    row.append(save, msg);
    box.append(ta, row);
    ta.focus();
  });
}

function renderDocs() {
  const panel = $("#docs-panel");
  if (panel.classList.contains("hidden")) return;
  const mode = curMode();
  $("#docs-title").textContent = t(`modes.${mode.id}.full`);
  $("#docs-lead").textContent = mode.id === "series" ? t("docs.seriesHint") : t("docs.ugcHint");
  const isSeries = mode.id === "series";
  $("#docs-eps-wrap").classList.toggle("hidden", !isSeries);

  const busy = docsBusy();
  const bibleBtn = $("#docs-gen-bible");
  bibleBtn.textContent = busy ? t("docs.genBibleBusy")
    : (isSeries ? t("docs.genBibleSeries") : t("docs.genBibleUgc"));
  bibleBtn.disabled = busy;

  const sheetBtn = $("#docs-gen-sheet");
  const epsBtn = $("#docs-make-eps");
  sheetBtn.classList.toggle("hidden", !isSeries);
  epsBtn.classList.toggle("hidden", !isSeries);
  if (isSeries) {
    const hasBible = Boolean(docBy("logline"));
    const sheet = docBy("beatsheet");
    sheetBtn.textContent = t("docs.genBeatsheet");
    sheetBtn.disabled = busy || !hasBible;
    sheetBtn.title = hasBible ? "" : t("docs.needBible");
    epsBtn.textContent = t("docs.createEpisodes");
    epsBtn.disabled = busy || !(sheet && sheet.data && (sheet.data.episodes || []).length);
    epsBtn.title = epsBtn.disabled ? t("docs.needSheet") : "";
  }

  const grid = $("#docs-grid");
  grid.innerHTML = "";
  // Порядок карточек — из реестра режима; «location» у UGC дописываем следом
  // за персоной: это второй обязательный дословный блок, а не мелочь.
  const kinds = (mode.docs || []).slice();
  if (mode.id === "ugc" && !kinds.includes("location")) kinds.push("location");
  kinds.forEach((kind) => grid.appendChild(docCard(docBy(kind), kind)));
}

function docsMsg(text, cls = "") {
  const el = $("#docs-status");
  el.textContent = text;
  el.className = "status " + cls;
}

$("#docs-gen-bible").addEventListener("click", async () => {
  const body = { idea: $("#docs-idea").value.trim() };
  if (curMode().id === "series") body.episodes = Number($("#docs-eps").value) || 8;
  docsMsg(t("docs.genBibleBusy"));
  try {
    await api(`/api/projects/${activeProjectId}/generate-bible`, { method: "POST", body });
    docsMsg("");
  } catch (e) { docsMsg(errText(e), "error"); }
  await loadProject();
});

$("#docs-gen-sheet").addEventListener("click", async () => {
  docsMsg(t("docs.genBibleBusy"));
  try {
    await api(`/api/projects/${activeProjectId}/generate-beatsheet`, {
      method: "POST", body: { episodes: Number($("#docs-eps").value) || 8 },
    });
    docsMsg("");
  } catch (e) { docsMsg(errText(e), "error"); }
  await loadProject();
});

$("#docs-make-eps").addEventListener("click", async () => {
  try {
    const r = await api(`/api/projects/${activeProjectId}/create-episodes`, {
      method: "POST", body: { season_no: 1 },
    });
    docsMsg(r.created ? t("docs.createdEpisodes", { n: r.created }) : t("docs.createdNone"),
            r.created ? "done" : "");
  } catch (e) { docsMsg(errText(e), "error"); }
  await loadProject();
});

// ────────── степпер трека: 3 этапа, никакой автогенерации при переключении ──────────
// Ключи этапов; подписи — в словаре (stages.*), чтобы степпер переводился.
// Сюжета среди этапов нет: он общий на проект и живёт в своей панели, а
// «Готовое» — не этап, а витрина клипа внизу карточки (.clip-dock).
const STAGES = ["setup", "board", "anim"];
// Активный этап на трек — переживает пере-рендеры поллинга.
const trackStages = new Map();

function stageStates(tr) {
  const scenes = tr.scenes || [];
  const busy = (st) => ["queued", "running"].includes(st);
  const anyImgBusy = scenes.some((s) => busy(s.image_status) || midframesBusy(s));
  const anyVidBusy = scenes.some((s) => busy(s.video_status));
  const framesDone = scenes.length > 0 && scenes.every((s) => s.image_url && s.image_last_url);
  const videosDone = scenes.length > 0 && scenes.every((s) => s.video_url);
  return {
    setup: tr.has_style && tr.audio_duration_sec ? "done"
      : (tr.title || tr.has_style || tr.audio_duration_sec || tr.lyrics || tr.comment) ? "part" : "empty",
    board: (tr.scenes_status === "error" || tr.storyboard_status === "error" ||
        scenes.some((s) => s.image_status === "error")) ? "error"
      : (busy(tr.scenes_status) || busy(tr.storyboard_status) || anyImgBusy) ? "busy"
      : framesDone ? "done" : scenes.length ? "part" : "empty",
    anim: scenes.some((s) => s.video_status === "error") ? "error"
      : anyVidBusy ? "busy"
      : videosDone ? "done" : scenes.some((s) => s.video_url) ? "part" : "empty",
  };
}

function defaultStage(tr) {
  // Клип больше не этап: трек с готовым видео открывается на «Анимации»,
  // а сам клип всё равно виден внизу карточки на любом этапе.
  if ((tr.scenes || []).some((s) => s.video_url)) return "anim";
  if (tr.scenes_count) return "board";
  return "setup";
}

// Активный этап переживает пере-рендеры, но не переживает смену набора этапов
// (в trackStages может лежать ключ, которого уже нет) — иначе трек откроется
// без единой видимой панели.
function activeStage(tr) {
  // Когда включён верстак (nav.js), активный этап знает ОН: у app.js и у
  // навигации иначе получаются два источника правды об одном и том же, и
  // после каждого опроса render() возвращал бы свой этап, а MutationObserver
  // через 30 мс — навовский. Видимое мигание раз в цикл поллинга.
  const nav = window.QlolNav;
  if (nav && nav.state && document.body.classList.contains("wb-on")) {
    const def = (nav.MODES || [])
      .filter((m) => m.id === nav.state.mode)
      .flatMap((m) => m.steps || [])
      .find((x) => x.id === nav.state.step);
    if (def && STAGES.includes(def.pane)) return def.pane;
  }
  const cur = trackStages.get(tr.id);
  return STAGES.includes(cur) ? cur : defaultStage(tr);
}

function setStage(card, key) {
  // Этап строится в момент первого показа: невидимые ленты рисовать незачем,
  // а раньше рисовались обе плюс витрина клипа.
  if (typeof card.__ensureStage === "function") card.__ensureStage(key);
  $$(".stage-tab", card).forEach((el) => el.classList.toggle("on", el.dataset.stage === key));
  $$(".stage-pane", card).forEach((el) => el.classList.toggle("on", el.dataset.stage === key));
}

// Стрелки ‹ › у горизонтальной ленты сцен.
function bindStrip(wrap) {
  const box = $(".scenes", wrap);
  const step = () => Math.max(280, Math.round(box.clientWidth * 0.8));
  $(".strip-prev", wrap).addEventListener("click", () => box.scrollBy({ left: -step(), behavior: "smooth" }));
  $(".strip-next", wrap).addEventListener("click", () => box.scrollBy({ left: step(), behavior: "smooth" }));
}


// ═══════════ ДВИЖКИ ОБЪЕКТА: один выбор на трек, а не на кадр ═══════════
// Владелец просил ровно это: «выбирать не надо в каждом кадре, всё табами и
// на весь видос». Раньше чипы движка рисовались в КАЖДОЙ карточке кадра
// (тридцать наборов по восемь), нигде не сохранялись — клик менял только
// dataset в DOM, который следующий же опрос стирал, — и кнопки «все кадры» /
// «все видео» звали роут вообще без параметров, то есть выбор игнорировали
// всегда. Теперь выбор живёт на треке (tracks.video_engine/image_engine),
// а карточка кадра его наследует.

function liveImageEngines() {
  return (providers.image_engines || []).filter((e) => e.live !== false);
}
function liveVideoEngines() {
  return (providers.video_engines || []).filter((e) => e.live !== false);
}

/* Что реально поедет в генерацию: выбор объекта, иначе дефолт тарифа. */
function effImageEngine(tr) {
  const list = liveImageEngines();
  const want = (tr && tr.image_engine) || "";
  if (want && list.some((e) => e.id === want)) return want;
  const cur = list.find((e) => e.current) || list[0];
  return cur ? cur.id : "";
}
function effVideoEngine(tr) {
  const list = liveVideoEngines();
  const want = (tr && tr.video_engine) || "";
  if (want && list.some((e) => e.id === want)) return want;
  const def = list.find((e) => e.default) || list[0];
  return def ? def.id : "";
}
function imageEngineById(id) { return liveImageEngines().find((e) => e.id === id) || null; }
function videoEngineById(id) { return liveVideoEngines().find((e) => e.id === id) || null; }

function engineTitle(id) {
  const e = imageEngineById(id) || videoEngineById(id);
  return e ? e.title : id;
}

/* Одна лента чипов. value === "" — «по тарифу»: выбор должно быть можно не
   только сделать, но и снять, иначе первый же клик становится вечным. */
function buildEngineTabs(box, list, current, costOf, onPick) {
  box.innerHTML = "";
  const mk = (id, title, cost) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "eng-chip" + (id === current ? " on" : "");
    chip.dataset.engine = id;
    chip.textContent = title;
    if (cost != null) {
      const c = document.createElement("span");
      c.className = "eng-cost";
      c.textContent = cost;
      chip.appendChild(c);
    }
    chip.addEventListener("click", () => onPick(id));
    box.appendChild(chip);
    return chip;
  };
  mk("", t("engines.auto"), null).title = t("engines.autoNote");
  list.forEach((e) => {
    const chip = mk(e.id, e.title, costOf(e));
    chip.title = e.note || e.title;
  });
}

function renderTrackEngines(card, tr) {
  const box = $(".t-engines", card);
  if (!box) return;
  const mode = curMode();
  $(".eng-hint", box).textContent = t("engines.hint", { object: objT("one", mode) });

  const imgList = liveImageEngines();
  const vidList = liveVideoEngines();
  const save = async (patch) => {
    try {
      await api(`/api/tracks/${tr.id}`, { method: "PATCH", body: patch });
      Object.assign(tr, patch);
      renderTrackEngines(card, tr);
      // Карточки кадров показывают наследование — их строку тоже освежаем.
      $$(".scene-card", card).forEach((sc) => {
        const line = $(".s-engine-line", sc);
        if (line) paintSceneEngineLine(line, tr, Number(sc.dataset.id));
      });
    } catch (e) { fail(e); }
  };

  buildEngineTabs($(".t-img-engines", box), imgList, tr.image_engine || "",
                  (e) => e.frames_cost, (id) => save({ image_engine: id }));
  buildEngineTabs($(".t-vid-engines", box), vidList, tr.video_engine || "",
                  (e) => e.scene_cost, (id) => save({ video_engine: id }));

  // Прогноз на весь объект, а не на кадр: цена сцены отличается в тридцать
  // раз между Grok и Seedance 2.5, и решение принимается по итогу за трек.
  const img = imageEngineById(effImageEngine(tr));
  const vid = videoEngineById(effVideoEngine(tr));
  const scenes = tr.scenes_count || (mode.scenes && mode.scenes.typ) || 30;
  const per = (img ? img.frames_cost : 0) + (vid ? vid.video_cost : 0);
  const fc = $(".eng-forecast", box);
  fc.innerHTML = "";
  if (per > 0) {
    fc.appendChild(document.createTextNode(
      t("engines.forecast", { scenes, cost: per, total: tNum(scenes * per) })));
  }
}

/* Строка «как у объекта · Seedance 2 Mini» в карточке кадра.
   Показывает то, что РЕАЛЬНО поедет в следующую генерацию, а не то, чем
   сцену сняли в прошлый раз: прежний чип подсвечивал именно прошлое и
   поэтому всегда врал после смены движка на треке. Чем сняли — в подсказке. */
function paintSceneEngineLine(line, tr, sceneId) {
  const s = (tr.scenes || []).find((x) => x.id === sceneId);
  const eff = videoEngineById(effVideoEngine(tr));
  line.innerHTML = "";
  const cap = document.createElement("span");
  cap.className = "s-engine-name";
  cap.textContent = t("engines.inherit", { object: objT("one") });
  line.appendChild(cap);
  if (eff) {
    const note = document.createElement("span");
    note.textContent = "· " + eff.title;
    line.appendChild(note);
  }
  const made = s && (s.video_engine || s.image_engine);
  if (made) line.title = engineTitle(made);
}


/* Поля режимов «сериалы» и «UGC» на этапе «Настройка». У клипа блок скрыт
   целиком: сезона, акта и формулы локации у него нет, и пустые поля здесь
   были бы предложением заполнить то, что никуда не поедет. */
function renderTrackModeFields(card, tr) {
  const box = $(".t-mode-fields", card);
  if (!box) return;
  const mode = curMode();
  const isClip = mode.id === "clip";
  box.classList.toggle("hidden", isClip);
  if (isClip) return;

  const isSeries = mode.id === "series";
  $(".t-season-wrap", card).classList.toggle("hidden", !isSeries);
  $(".t-episode-wrap", card).classList.toggle("hidden", !isSeries);
  $(".t-location-wrap", card).classList.toggle("hidden", isSeries);
  $(".t-script-wrap", card).classList.toggle("hidden", !isSeries);

  const patch = async (body) => {
    try {
      await api(`/api/tracks/${tr.id}`, { method: "PATCH", body });
      Object.assign(tr, body);
    } catch (e) { fail(e); }
  };

  const fsel = $(".t-format", card);
  fsel.innerHTML = "";
  modeFormats(mode).forEach((f) => {
    const o = document.createElement("option");
    o.value = f.key;
    o.textContent = formatLabel(mode, f.key);
    o.title = typeof f.logline === "string" ? f.logline : "";
    fsel.appendChild(o);
  });
  fsel.value = tr.format_key || "";
  fsel.addEventListener("change", () => patch({ format_key: fsel.value }));

  if (isSeries) {
    const se = $(".t-season", card);
    const ep = $(".t-episode", card);
    se.value = tr.season_no || 0;
    ep.value = tr.episode_no || 0;
    se.addEventListener("change", () => patch({ season_no: Number(se.value) || 0 }));
    ep.addEventListener("change", () => patch({ episode_no: Number(ep.value) || 0 }));

    // Сценарий серии живёт документом (docs, kind="script", track_id) и
    // ОБЯЗАТЕЛЕН до разбивки на кадры: без него разбивка выдумывает сюжет
    // заново и расходится с поэпизодным планом.
    const doc = docBy("script", tr.id);
    const btn = $(".t-gen-script", card);
    const st = $(".t-script-status", card);
    const bodyEl = $(".t-script-body", card);
    const busy = doc && (doc.status === "queued" || doc.status === "running");
    btn.textContent = busy ? t("docs.genScriptBusy") : t("docs.genScript");
    btn.disabled = Boolean(busy) || !docBy("logline");
    btn.title = docBy("logline") ? "" : t("docs.needBible");
    const lab = statusLabel(doc ? doc.status : "");
    st.textContent = doc && doc.status === "error" ? (doc.error || lab.text) : lab.text;
    st.className = "status t-script-status " + lab.cls;
    bodyEl.textContent = (doc && doc.body) || t("docs.needScript");
    bodyEl.className = "t-script-body doc-body" + (doc && doc.body ? "" : " muted");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api(`/api/tracks/${tr.id}/generate-script`, { method: "POST" });
      } catch (e) { fail(e); }
      await loadProject();
    });
  } else {
    const loc = $(".t-location", card);
    loc.value = tr.location_bible || "";
    loc.addEventListener("change", () => patch({ location_bible: loc.value }));
  }
}

function renderTrack(tr) {
  const tpl = $("#track-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".track-card");
  applyI18n(card);   // содержимое <template> обходом документа не задевается
  card.dataset.id = tr.id;
  $(".pos", card).textContent = `#${tr.position}`;
  // Обложка трека: клик по квадрату = заменить (скрытый file input в label).
  if (tr.cover_url) {
    const cImg = $(".t-cover-img", card);
    cImg.src = tr.cover_url;
    cImg.classList.remove("hidden");
  }
  $(".t-cover-input", card).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("cover", file);
    await api(`/api/tracks/${tr.id}/cover`, { method: "POST", body: fd });
    await loadProject();
  });
  $(".t-title", card).value = tr.title;

  // ── табы-этапы с точками-статусами
  const states = stageStates(tr);
  const active = activeStage(tr);
  const tabsBox = $(".stage-tabs", card);
  STAGES.forEach((key, i) => {
    const name = t("stages." + key);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "stage-tab" + (key === active ? " on" : "");
    b.dataset.stage = key;
    const num = document.createElement("span");
    num.className = "st-num";
    num.textContent = String(i + 1);
    const dot = document.createElement("span");
    dot.className = "stage-dot " + states[key];
    b.append(num, document.createTextNode(name), dot);
    b.addEventListener("click", () => {
      trackStages.set(tr.id, key);
      setStage(card, key);
    });
    tabsBox.appendChild(b);
  });
  setStage(card, active);

  // Свайп влево/вправо по контенту = соседний этап (ленты сцен и поля не трогаем).
  const panes = $(".stage-panes", card);
  let touchX = null, touchY = null;
  panes.addEventListener("touchstart", (e) => {
    if (e.target.closest(".scenes, audio, video, input, textarea, select, details")) { touchX = null; return; }
    touchX = e.touches[0].clientX;
    touchY = e.touches[0].clientY;
  }, { passive: true });
  panes.addEventListener("touchend", (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    const dy = e.changedTouches[0].clientY - touchY;
    touchX = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
    const cur = activeStage(tr);
    const idx = STAGES.indexOf(cur);
    const next = STAGES[idx + (dx < 0 ? 1 : -1)];
    if (next) {
      trackStages.set(tr.id, next);
      setStage(card, next);
    }
  }, { passive: true });

  // ── этап 1: настройка (+ режиссёрская заметка трека, если сюжет её написал)
  if (tr.director_note) {
    $(".t-note-view", card).classList.remove("hidden");
    $(".t-note-text", card).textContent = tr.director_note;
  }
  // Стиль трека: пикер отдаёт КЛЮЧИ, текст промпта собирает сервер. Скрытое
  // поле держит их же csv — на нём завязан обычный сабмит карточки.
  $(".t-style", card).value = (tr.style_keys || []).join(",");
  buildStylePicker($(".t-style-picker", card), tr.style_keys || [], async (keys) => {
    $(".t-style", card).value = keys.join(",");
    try {
      await api(`/api/tracks/${tr.id}/style`, { method: "POST", body: { style_keys: keys } });
    } catch (e) { fail(e); }
  });
  // ── движки объекта и поля режима ──
  renderTrackEngines(card, tr);
  renderTrackModeFields(card, tr);
  $(".t-comment", card).value = tr.comment;
  $(".t-grain", card).checked = Boolean(tr.film_grain);
  $(".t-nostory", card).checked = Boolean(tr.no_story);
  $(".t-lyrics", card).value = tr.lyrics;
  const audioEl = $(".t-audio", card);
  if (tr.audio_filename) audioEl.src = `/api/tracks/${tr.id}/audio`;
  else audioEl.style.display = "none";
  const durEl = $(".t-duration", card);
  durEl.textContent = tr.audio_duration_sec ? fmtTime(tr.audio_duration_sec) : "";
  if (tr.audio_profile) durEl.title = t("track.audioProfile") + ": " + tr.audio_profile;

  // Лирика и аудио — свойства КЛИПА, и режим уже знает это (needs_lyrics,
  // needs_audio). До этого гейт стоял только на форме «добавить объект», а в
  // карточке серии всё равно висели «Текст песни» и загрузка дорожки: поля,
  // которые никуда не поедут (серию режет сценарий, ролик — генератор).
  // Значения не стираем — прячем: у серии, заведённой как клип, лирика
  // остаётся в базе и вернётся, если проект снова станет клипом.
  const trMode = curMode();
  const gate = (sel, on) => {
    const el = $(sel, card);
    if (el) el.classList.toggle("hidden", !on);
  };
  gate(".t-lyrics-label", trMode.needs_lyrics);
  gate(".t-lyrics", trMode.needs_lyrics);
  gate(".t-audio-label", trMode.needs_audio);
  gate(".t-duration", trMode.needs_audio);
  // «Без сюжета (рандомные кадры)» — обход раскадровки по дорожке, у серии и
  // ролика обходить нечего.
  gate(".t-nostory-wrap", trMode.needs_audio);
  if (!trMode.needs_audio) audioEl.style.display = "none";
  $(".save-track", card).textContent = objT("save", trMode) || t("track.saveTrack");
  const styleLab = $(".stage-pane[data-stage=\"setup\"] > label", card);
  if (styleLab) styleLab.textContent = objT("style", trMode) || t("track.styleLabel");

  // Плеер трека сам подсвечивает кадр, который сейчас звучит — и наоборот,
  // клик по кадру перематывает трек на его начало и проигрывает.
  audioEl.addEventListener("timeupdate", () => highlightActiveScene(card, audioEl.currentTime));

  $(".up", card).addEventListener("click", () => moveTrack(tr.id, -1));
  $(".down", card).addEventListener("click", () => moveTrack(tr.id, 1));
  $(".del", card).addEventListener("click", () => deleteTrack(tr.id));
  $(".save-track", card).addEventListener("click", () => saveTrack(tr.id, card));

  // ── этап 2: раскадровка
  $(".add-scene", card).addEventListener("click", () => addManualScene(tr.id));
  const allBtn = $(".gen-all-frames", card);
  const framesBusy = (tr.scenes || []).some((s) => ["queued", "running"].includes(s.image_status));
  // «(готовый кадр» — служебная метка бэкенда в image_prompt (backend/main.py),
  // не текст для человека: переводить её нельзя, иначе фильтр разъедется.
  const framesTodo = (tr.scenes || []).filter((s) => !(s.image_url && s.image_last_url) && s.image_prompt && !s.image_prompt.startsWith("(готовый кадр")).length;
  allBtn.disabled = framesBusy || !framesTodo;
  allBtn.textContent = framesBusy ? t("track.allFramesBusy") : t("track.allFramesN", { n: framesTodo });
  allBtn.title = t("track.allFramesTitle");
  $(".all-frames-note", card).textContent = framesBusy ? t("track.allFramesNote") : "";
  allBtn.addEventListener("click", async () => {
    try {
      // Движок передаём ЯВНО. Раньше кнопка звала роут без параметров, и
      // любой выбор в интерфейсе для пакетной генерации не значил ничего.
      const eng = encodeURIComponent(effImageEngine(tr));
      await api(`/api/tracks/${tr.id}/generate-all-frames?engine=${eng}`, { method: "POST" });
    } catch (e) {
      fail(e);
    }
    await loadProject();
  });
  const allVidBtn = $(".gen-all-videos", card);
  if (allVidBtn) {
    const vidBusy = (tr.scenes || []).some((s) => ["queued", "running"].includes(s.video_status));
    const vidTodo = (tr.scenes || []).filter((s) => s.image_url && !s.video_url).length;
    allVidBtn.disabled = vidBusy || !vidTodo;
    allVidBtn.textContent = vidBusy ? t("track.allVideosBusy") : t("track.allVideosN", { n: vidTodo });
    allVidBtn.addEventListener("click", async () => {
      const engSpec = videoEngineById(effVideoEngine(tr));
      const priceLine = engSpec
        ? `\n${engSpec.title} — ${tNum(vidTodo * engSpec.scene_cost)} ⚡`
        : "";
      if (!confirm(t("track.allVideosConfirm", { n: vidTodo }) + priceLine)) return;
      try {
        const eng = effVideoEngine(tr);
        const spec = videoEngineById(eng);
        const qs = `?engine=${encodeURIComponent(eng)}`
          + (spec ? `&provider=${encodeURIComponent(spec.family)}` : "");
        await api(`/api/tracks/${tr.id}/generate-all-videos${qs}`, { method: "POST" });
      } catch (e) { fail(e); }
      await loadProject();
    });
  }
  const genBtn = $(".gen-scenes", card);
  const busy = tr.scenes_status === "queued" || tr.scenes_status === "running";
  // Предусловие разбивки у каждого режима СВОЁ: клипу нужен сквозной сюжет
  // проекта, серии — её собственный сценарий по актам, ролику не нужно
  // ничего (каркас формата и есть его план). Общее «нет сюжета — кнопка
  // мертва» держало бы два режима из трёх выключенными навсегда.
  const modeNow = curMode();
  let scenesReady = true;
  let scenesWhy = "";
  if (modeNow.id === "clip") {
    scenesReady = Boolean(project.story) || Boolean(tr.no_story);
    scenesWhy = t("track.genScenesTitle");
  } else if (modeNow.id === "series") {
    const sc = docBy("script", tr.id);
    scenesReady = Boolean(sc && sc.body);
    scenesWhy = t("docs.needScript");
  }
  genBtn.disabled = busy || !scenesReady;
  genBtn.title = scenesReady ? "" : scenesWhy;
  genBtn.addEventListener("click", () => genScenes(tr.id));

  // ⚡ Супергенерация: весь конвейер одним нажатием (кнопка живёт в шапке).
  const superBtn = $(".s-supergen", card);
  const superBusy = ["queued", "running"].includes(tr.supergen_status);
  // Дорожка обязательна только там, где она задаёт ритм: у ролика ритм дают
  // слоты 5–8 секунд, у серии — акты. Требовать mp3 значило бы запрещать им
  // супергенерацию без всякой причины.
  superBtn.disabled = superBusy
    || (modeNow.needs_audio !== false && !tr.audio_duration_sec)
    || (modeNow.id === "series" && !scenesReady);
  superBtn.textContent = superBusy ? t("track.supergenBusy") : t("track.supergen");
  superBtn.addEventListener("click", () => openSupergenModal(tr));
  const superNote = $(".supergen-note", card);
  superNote.textContent = tr.supergen_note || "";
  superNote.className = "status supergen-note " +
    (tr.supergen_status === "error" ? "error" : tr.supergen_status === "done" ? "done" : "");
  // Своего интервала у супергенерации больше нет: она попала в projectBusy(),
  // и её ведёт общий лёгкий поллер. Раньше здесь крутился второй таймер,
  // который каждые 15 секунд тянул ВЕСЬ проект и вызывал loadProject поверх
  // работающего опроса — то есть две полные перерисовки вместо ноля.
  if (window.__supergenPoll) {
    clearInterval(window.__supergenPoll);
    window.__supergenPoll = null;
  }
  const st = statusLabel(tr.scenes_status, t("track.scenesDone", { n: tr.scenes_count }));
  const stEl = $(".scenes-status", card);
  setStatus(stEl, tr.scenes_status, t("track.scenesDone", { n: tr.scenes_count }));
  if (!st.text) {
    stEl.textContent = tr.scenes_count ? t("track.scenesCount", { n: tr.scenes_count }) : "";
  }

  // Лист раскадровки: весь клип одной картинкой — до покадровой отрисовки.
  const sbStatus = statusLabel(tr.storyboard_status);
  setStatus($(".sb-status", card), tr.storyboard_status);
  const sbEmpty = $(".sb-empty", card);
  const sbOpenBtn = $(".sb-open", card);
  if (tr.storyboard_url) {
    const img = $(".sb-preview", card);
    img.src = tr.storyboard_url;
    img.classList.remove("hidden");
    img.addEventListener("click", () => openSheetModal(tr));
    if (sbEmpty) sbEmpty.classList.add("hidden");
  }
  if (sbOpenBtn) {
    sbOpenBtn.disabled = !tr.storyboard_url;
    sbOpenBtn.addEventListener("click", () => openSheetModal(tr));
  }
  const sbBtn = $(".gen-storyboard", card);
  const sbBusy = ["queued", "running"].includes(tr.storyboard_status);
  sbBtn.disabled = sbBusy || !tr.scenes_count;
  sbBtn.textContent = sbBusy ? t("track.sheetBusy")
    : tr.storyboard_url ? t("track.redrawSheet") : t("track.genSheet");
  sbBtn.addEventListener("click", () => genStoryboard(tr.id));
  const sliceBtn = $(".slice-storyboard", card);
  sliceBtn.disabled = !tr.storyboard_url;
  sliceBtn.addEventListener("click", () => openCellsModal(tr));

  // ── Сцены двумя лентами: «Раскадровка» — кадры, «Анимация» — видео.
  //
  // ЛЕНИВО И ПО ЭТАПАМ. Раньше renderTrack строил ОБЕ ленты и final-grid
  // сразу, хотя CSS показывает ровно одну: тридцать сцен превращались в
  // 6800 узлов и девяносто медиа-элементов, и всё это сносилось и
  // собиралось заново каждые три секунды. Теперь строится только открытый
  // этап, остальные — в момент первого переключения.
  const boardBox = $(".scenes-board", card);
  const animBox = $(".scenes-anim", card);
  const built = new Set();
  card.__ensureStage = (key) => {
    if (built.has(key)) return;
    built.add(key);
    if (key === "board") fillScenes(boardBox, tr, "board", audioEl, card);
    if (key === "anim") fillScenes(animBox, tr, "anim", audioEl, card);
  };
  card.__ensureStage(active);
  bindSceneViews(card, tr, audioEl);
  $$(".strip-wrap", card).forEach(bindStrip);

  // ── витрина клипа внизу карточки: видна на любом этапе, всегда актуальна
  const clipStatus = statusLabel(tr.clip_status, t("track.clipDone"));
  setStatus($(".clip-status", card), tr.clip_status, t("track.clipDone"));
  $(".clip-title", card).textContent =
    t("track.clipTitle", { a: tr.approved_count, b: tr.scenes_count });
  const clipEmpty = $(".clip-empty", card);
  if (tr.clip_url) {
    const v = $(".clip-preview", card);
    v.src = tr.clip_url;
    v.classList.remove("hidden");
    const dl = $(".clip-download", card);
    dl.href = tr.clip_url;
    dl.classList.remove("hidden");
    if (clipEmpty) clipEmpty.classList.add("hidden");
  }
  const asmBtn = $(".assemble", card);
  const asmBusy = ["queued", "running"].includes(tr.clip_status);
  asmBtn.disabled = asmBusy || !tr.approved_count;
  asmBtn.title = tr.approved_count ? "" : t("track.assembleTitle");
  asmBtn.textContent = asmBusy ? t("track.assembleBusy")
    : tr.clip_url ? t("track.reassemble") : t("track.assemble");
  asmBtn.addEventListener("click", () => assembleClip(tr.id));

  // Автосборка: флаг живёт в localStorage, работу делает autoAssembleTick
  // на общем поллинге. Здесь только галочка и её сообщение.
  const autoBox = $(".t-autoasm", card);
  if (autoBox) {
    autoBox.checked = autoAsmOn(tr.id);
    autoBox.addEventListener("change", () => {
      setAutoAsm(tr.id, autoBox.checked);
      autoAsmNote.delete(tr.id);
      // Снимок «что уже видели» обновляем прямо сейчас: включённая галочка не
      // должна пересобирать клип из-за видео, которые лежали тут и до неё.
      autoAsmSeen.set(tr.id, videosSig(tr));
      const note = $(".autoasm-note", card);
      if (note) {
        note.textContent = autoBox.checked ? t("track.autoAsmOn") : "";
        note.className = "status autoasm-note";
      }
    });
    const note = $(".autoasm-note", card);
    if (note) {
      const err = autoAsmNote.get(tr.id);
      note.textContent = err || (autoBox.checked ? t("track.autoAsmOn") : "");
      note.className = "status autoasm-note" + (err ? " error" : "");
    }
  }

  // Витрина «все сцены клипа» — третья копия тех же видео. Строится ЛЕНИВО,
  // по раскрытию <details>: раньше она собиралась на каждую перерисовку и
  // добавляла ещё тридцать <video preload="metadata">, то есть тридцать
  // Range-запросов за заголовками файлов каждые три секунды.
  const finalDet = $(".final-scenes", card);
  const grid = $(".final-grid", card);
  const fillFinal = () => {
    if (grid.dataset.built) return;
    grid.dataset.built = "1";
    const withVideo = (tr.scenes || []).filter((s) => s.video_url);
    if (!withVideo.length) {
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = t("track.finalEmpty");
      grid.appendChild(empty);
      return;
    }
    withVideo.forEach((s) => {
      const cell = document.createElement("div");
      cell.className = "final-cell";
      const v = document.createElement("video");
      v.src = s.video_url;
      v.controls = true;
      v.loop = true;
      // preload="none" + постер: браузер трогает файл только когда нажали ▶.
      v.preload = "none";
      v.poster = s.image_thumb_url || "";
      const cap = document.createElement("span");
      cap.className = "muted";
      cap.textContent = t("scene.cap", { n: s.position, time: fmtTime(s.start_sec) })
        + (s.approved ? t("scene.capApproved") : "");
      cell.append(v, cap);
      grid.appendChild(cell);
    });
  };
  if (finalDet) {
    finalDet.addEventListener("toggle", () => { if (finalDet.open) fillFinal(); });
    if (finalDet.open) fillFinal();
  }

  return card;
}

// ═════════ ВИД ЛЕНТЫ КАДРОВ: сетка по умолчанию, лента по выбору ═════════
//
// «Обложку поменьше, покомпактнее, раскадровку по ширине сразу» — это две
// правки: маленькая обложка (style.css) и вот эта сетка. Сетка ещё и дешевле
// для браузера: в ней кадр рисуется ПЛИТКОЙ из двенадцати узлов, а полная
// карточка (сто десять узлов, два <video>, <audio>, до 38 чипов движков)
// открывается по клику в модалке — одна штука на экран вместо тридцати.
//
// Лента не убрана: под музыку, с подсветкой звучащего кадра, горизонтальная
// прокрутка честно удобнее. Выбор запоминается по треку.

const VIEW_KEY = "rc_sceneview";
const GRID_FIRST = 12;          // сколько плиток показываем до «ещё N»
const STRIP_FIRST = 30;         // лента и так прокручивается — режем только хвост

function viewMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(VIEW_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch (e) {
    return {};                  // приватный режим или мусор в ключе
  }
}

function sceneView(trackId) {
  // Владелец просил вернуть как было: кадры лентой, без переключателя вида.
  // Функция оставлена, чтобы не переписывать вызовы по всему рендеру.
  return "strip";
}

function setSceneView(trackId, view) {
  const map = viewMap();
  if (view === "strip") map[String(trackId)] = "strip";
  else delete map[String(trackId)];
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(map)); } catch (e) { /* приватный режим */ }
}

// Сколько плиток трека уже развёрнуто. В памяти, а не в localStorage:
// «показать ещё» — состояние сеанса, а не настройка.
const scenesShown = new Map();

function sceneRows(tr, mode) {
  const all = tr.scenes || [];
  if (mode !== "anim") return all;
  // В «Анимацию» кадр попадает, только когда видео есть или генерится.
  return all.filter((s) => s.video_url
    || ["queued", "running", "error"].includes(s.video_status));
}

function fillScenes(box, tr, mode, audioEl, card) {
  box.innerHTML = "";
  const view = sceneView(tr.id);
  const rows = sceneRows(tr, mode);
  const wrap = box.closest(".strip-wrap");
  box.classList.toggle("grid-view", view === "grid");
  if (wrap) wrap.classList.toggle("is-grid", view === "grid");

  if (!rows.length) {
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.padding = "8px 4px";
    hint.textContent = mode === "anim" ? t("track.animEmpty") : t("track.boardEmpty");
    box.appendChild(hint);
    const moreBtn0 = $(mode === "anim" ? ".anim-more" : ".scenes-more:not(.anim-more)", card);
    if (moreBtn0) moreBtn0.classList.add("hidden");
    return;
  }

  const key = `${tr.id}:${mode}:${view}`;
  const cap = view === "grid" ? GRID_FIRST : STRIP_FIRST;
  const shown = Math.min(rows.length, scenesShown.get(key) || cap);
  rows.slice(0, shown).forEach((sc) => {
    box.appendChild(view === "grid"
      ? renderSceneTile(sc, tr, mode, audioEl)
      : renderScene(sc, audioEl, mode));
  });

  const moreBtn = $(mode === "anim" ? ".anim-more" : ".scenes-more:not(.anim-more)", card);
  if (moreBtn) {
    const left = rows.length - shown;
    moreBtn.classList.toggle("hidden", left <= 0);
    moreBtn.textContent = t("track.showMore", { n: left });
    moreBtn.onclick = () => {
      scenesShown.set(key, rows.length);
      fillScenes(box, tr, mode, audioEl, card);
    };
  }
  const count = $(".scenes-count", card);
  if (count && mode === "board") {
    count.textContent = t("track.shownOf", { a: shown, b: rows.length });
  }
}

function bindSceneViews(card, tr, audioEl) {
  const view = sceneView(tr.id);
  $$(".view-btn", card).forEach((b) => {
    b.classList.toggle("on", b.dataset.view === view);
    b.addEventListener("click", () => {
      setSceneView(tr.id, b.dataset.view);
      $$(".view-btn", card).forEach((x) => x.classList.toggle("on", x === b));
      // Перерисовываем ТОЛЬКО ленты этого трека, а не весь проект.
      const boardBox = $(".scenes-board", card);
      const animBox = $(".scenes-anim", card);
      if (boardBox.children.length) fillScenes(boardBox, tr, "board", audioEl, card);
      if (animBox.children.length) fillScenes(animBox, tr, "anim", audioEl, card);
    });
  });
}

/* Одна плитка кадра. Никаких <video> и <audio>: постер видео — та же
   миниатюра первого кадра, которую отдаёт /api/thumb. Раньше каждая
   перерисовка создавала до 91 медиа-элемента на трек, и браузер, упершись в
   лимит одновременных медиа, просто переставал их грузить. */
function renderSceneTile(s, tr, mode, audioEl) {
  const tpl = $("#scene-tile-tpl").content.cloneNode(true);
  const tile = tpl.querySelector(".scene-tile");
  tile.dataset.id = s.id;
  tile.dataset.start = s.start_sec;
  tile.dataset.duration = s.duration_sec;
  const img = $(".st-img", tile);
  const poster = s.image_thumb_url || s.image_url || "";
  if (poster) {
    img.src = poster;
    img.classList.remove("hidden");
  } else {
    $(".st-ph", tile).textContent = "▦";
  }
  if (mode === "anim" && s.video_url) $(".st-play", tile).classList.remove("hidden");
  $(".st-no", tile).textContent = t("scene.pos", { n: s.position });
  $(".st-time", tile).textContent = fmtTime(s.start_sec);

  const status = mode === "anim" ? s.video_status : s.image_status;
  const done = mode === "anim" ? Boolean(s.video_url) : Boolean(s.image_url);
  const dot = $(".st-dot", tile);
  dot.className = "st-dot " + tileDotClass(status, done);
  if (mode === "anim" && s.approved) {
    const ok = document.createElement("span");
    ok.className = "st-ok";
    ok.textContent = "✓";
    $(".st-shot", tile).appendChild(ok);
  }
  tile.title = (s.shot_note || s.lyric_line || "").slice(0, 140);
  tile.addEventListener("click", () => openSceneModal(s, tr, mode, audioEl));
  return tile;
}

function tileDotClass(status, done) {
  if (["queued", "running"].includes(status)) return "busy";
  if (status === "error") return "error";
  return done ? "done" : "";
}

/* Полная карточка кадра — в модалке. Тот же #scene-tpl и тот же renderScene:
   второго набора полей и второй логики сохранения не заводим. */
function openSceneModal(s, tr, mode, audioEl) {
  openModal(t("scene.modalTitle", { n: s.position }), (body) => {
    const holder = document.createElement("div");
    holder.className = "scene-modal";
    const card = renderScene(s, audioEl || document.createElement("audio"), mode);
    holder.appendChild(card);
    body.appendChild(holder);
    // Сохранение и удаление ЗАКРЫВАЮТ модалку. Оба вызывают loadProject,
    // после которого лента пересобрана, а открытая карточка остаётся висеть
    // оторванной от проекта копией — человек смотрит на данные, которых уже
    // нет. Перевешиваем обработчик клонированием узла: свой у кнопки один.
    ["s-save", "s-del"].forEach((cls) => {
      const btn = $("." + cls, card);
      if (!btn) return;
      const fresh = btn.cloneNode(true);
      btn.replaceWith(fresh);
      fresh.addEventListener("click", async () => {
        fresh.disabled = true;
        try {
          if (cls === "s-save") await saveScene(s.id, card);
          else await deleteScene(s.id);
        } catch (e) {
          fresh.disabled = false;
          return fail(e);
        }
        closeModal();
      });
    });
  }, { medium: true });
}

// Подсвечивает кадр под текущей секундой трека и без дёрганий скроллит его
// в видимую область — плеер "листает" раскадровку сам, по факту звучания.
// Подсветка висит на timeupdate — это четыре раза в секунду ВО ВРЕМЯ
// проигрывания, и каждый раз здесь делался полный querySelectorAll по
// карточке трека плюс scrollIntoView. Список кадров кэшируем на карточке и
// пересобираем только когда он реально поменялся.
function highlightActiveScene(trackCard, currentTime) {
  const now = Date.now();
  if (trackCard.__hlAt && now - trackCard.__hlAt < 240) return;
  trackCard.__hlAt = now;
  const cards = $$(".scene-card, .scene-tile", trackCard);
  for (const sc of cards) {
    const start = Number(sc.dataset.start);
    const end = start + Number(sc.dataset.duration);
    const active = currentTime >= start && currentTime < end;
    sc.classList.toggle("active", active);
    if (active && !sc.dataset.seen) {
      sc.dataset.seen = "1";
      sc.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    if (!active) delete sc.dataset.seen;
  }
}

// mode: "board" — компактная карточка кадров (лента этапа «Раскадровка»),
// "anim" — карточка видео (лента этапа «Анимация»). Один шаблон, CSS решает.
/* Переопределение движка кадров ЭТОЙ сцены. Пусто — значит «как у объекта». */
function sceneImgOverride(card) {
  const seg = $(".s-image-seg", card);
  return (seg && seg.dataset.engine) || "";
}

function renderScene(s, audioEl, mode = "board") {
  const tpl = $("#scene-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".scene-card");
  applyI18n(card);
  card.classList.add("mode-" + mode);
  card.dataset.id = s.id;
  card.dataset.start = s.start_sec;
  card.dataset.duration = s.duration_sec;
  $(".s-pos", card).textContent = t("scene.pos", { n: s.position });
  $(".s-time", card).textContent = `${fmtTime(s.start_sec)} — ${fmtTime(s.start_sec + s.duration_sec)}`;
  // Акт серии и кто говорит — метки режимов «сериалы»/«UGC». У клипа обе
  // пустые, и пустой span в разметке ничего не занимает.
  const actEl = $(".s-act", card);
  if (actEl) actEl.textContent = s.act ? s.act.replace(/_/g, " ") : "";
  const spEl = $(".s-speaker", card);
  if (spEl) spEl.textContent = s.speaker || "";
  $(".s-duration", card).value = s.duration_sec;
  $(".s-shotsize", card).value = s.shot_size || "";
  $(".s-camera", card).value = s.camera_move || "";
  card.classList.add("shot-" + (s.shot_size || "").replace(/\s+/g, "-"));
  $(".s-chars", card).value = s.characters || "";

  // Персонажи сцены — чипами: клик включает/выключает имя в s.characters
  // (скрытый input .s-chars остаётся источником правды для saveScene).
  const charsInput = $(".s-chars", card);
  const chipsBox = $(".s-chars-chips", card);
  const projCharNames = (project.characters || [])
    .map((c) => (c.name || "").trim()).filter(Boolean);
  const selectedChars = () =>
    charsInput.value.split(",").map((n) => n.trim()).filter(Boolean);
  if (projCharNames.length) {
    projCharNames.forEach((name) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "style-chip char-chip";
      chip.textContent = name;
      const isOn = () =>
        selectedChars().some((n) => n.toLowerCase() === name.toLowerCase());
      chip.classList.toggle("on", isOn());
      chip.addEventListener("click", async () => {
        const cur = selectedChars();
        const next = isOn()
          ? cur.filter((n) => n.toLowerCase() !== name.toLowerCase())
          : [...cur, name];
        charsInput.value = next.join(", ");
        chip.classList.toggle("on");
        chip.disabled = true;
        try {
          await api(`/api/scenes/${s.id}`, {
            method: "PATCH", body: { characters: charsInput.value },
          });
        } catch (e) {
          fail(e);
        }
        await loadProject();
      });
      chipsBox.appendChild(chip);
    });
  } else {
    chipsBox.classList.add("hidden");
  }

  $(".s-lyric", card).value = s.lyric_line;
  $(".s-note", card).value = s.shot_note;
  $(".s-image", card).value = s.image_prompt;
  $(".s-motion", card).value = s.motion_prompt;
  $(".s-motion-last", card).value = s.image_prompt_last || "";
  $(".s-del", card).addEventListener("click", () => deleteScene(s.id));
  $(".s-save", card).addEventListener("click", () => saveScene(s.id, card));
  // ✎ раскрывает остальные поля кадра (крупность, камера, лирика, анимация).
  $(".s-edit-toggle", card).addEventListener("click", () =>
    $(".s-edit", card).classList.toggle("hidden"));
  // Клик по ▶ — трек перематывается на начало кадра и играет: удобно
  // сверять текст/промпт кадра с тем, что реально звучит в этот момент.
  $(".s-play", card).addEventListener("click", () => {
    if (!audioEl.src) return;
    audioEl.currentTime = s.start_sec;
    audioEl.play();
  });

  // Кадры сцены: первый и последний (Seedance интерполирует между ними).
  const imgStatus = statusLabel(s.image_status);
  setStatus($(".s-image-status", card), s.image_status);
  if (s.image_url) {
    const p = $(".s-image-preview", card);
    // 4К-оригиналы по 15МБ сетка не тянет — превью с миниатюры, клик = оригинал.
    p.src = s.image_thumb_url || s.image_url;
    p.classList.remove("hidden");
    p.style.cursor = "zoom-in";
    p.onclick = () => window.open(s.image_url, "_blank");
  }
  if (s.image_last_url) {
    const p = $(".s-image-last-preview", card);
    p.src = s.image_last_thumb_url || s.image_last_url;
    p.classList.remove("hidden");
    p.style.cursor = "zoom-in";
    p.onclick = () => window.open(s.image_last_url, "_blank");
  }
  const framesBtn = $(".s-gen-frames", card);
  const imgBusy = ["queued", "running"].includes(s.image_status);
  framesBtn.disabled = imgBusy;
  framesBtn.textContent = imgBusy ? t("scene.framesBusy")
    : s.image_url ? t("scene.regenFrames") : t("scene.genFrames");
  framesBtn.addEventListener("click", () => genSceneFrames(s.id, "both", sceneImgOverride(card)));
  const firstBtn = $(".s-gen-first", card);
  const lastBtn = $(".s-gen-last", card);
  if (firstBtn) {
    firstBtn.disabled = imgBusy;
    firstBtn.addEventListener("click", () => genSceneFrames(s.id, "first", sceneImgOverride(card)));
  }
  if (lastBtn) {
    lastBtn.disabled = imgBusy;
    lastBtn.addEventListener("click", () => genSceneFrames(s.id, "last", sceneImgOverride(card)));
  }

  // Промежуточные кадры: ряд мини-превью + кнопка с числом по длительности.
  const midBox = $(".s-midframes", card);
  (s.midframes || []).forEach((m, i) => {
    const img = document.createElement("img");
    img.src = m.thumb_url || m.url;
    img.alt = "";
    img.title = t("scene.midThumb", { n: i + 1 });
    img.addEventListener("click", () => window.open(m.url, "_blank"));
    midBox.appendChild(img);
  });
  // Референсы кадра: композиция, свет, вайб. Модельки персонажей остаются
  // только ради узнаваемости лиц — стилистику диктуют реф и стиль трека.
  const refsBox = $(".s-refs", card);
  if (refsBox) {
    (s.refs || []).forEach((r) => {
      const wrap = document.createElement("div");
      wrap.className = "s-ref";
      const img = document.createElement("img");
      img.src = r.thumb_url || r.url;
      img.alt = "";
      img.title = t("scene.refTitle");
      img.addEventListener("click", () => window.open(r.url, "_blank"));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger s-ref-del";
      del.textContent = "✕";
      del.title = t("scene.refDel");
      del.addEventListener("click", async () => {
        try {
          await api(`/api/scenes/refs/${r.id}`, { method: "DELETE" });
        } catch (e) {
          fail(e);
        }
        await loadProject();
      });
      wrap.append(img, del);
      refsBox.appendChild(wrap);
    });
    const upload = document.createElement("label");
    upload.className = "s-ref-upload";
    upload.title = t("scene.refUploadTitle");
    const refInput = document.createElement("input");
    refInput.type = "file";
    refInput.className = "hidden";
    refInput.accept = "image/*";
    refInput.multiple = true;
    // Бэк принимает по одному файлу — шлём каждый своим POST'ом.
    refInput.addEventListener("change", async () => {
      for (const file of refInput.files) {
        const fd = new FormData();
        fd.append("photo", file);
        try {
          await api(`/api/scenes/${s.id}/refs`, { method: "POST", body: fd });
        } catch (e) {
          fail(e);
          break;
        }
      }
      await loadProject();
    });
    const refBtn = document.createElement("span");
    refBtn.className = "s-ref-btn";
    refBtn.textContent = t("scene.refAdd");
    upload.append(refInput, refBtn);
    refsBox.appendChild(upload);
  }

  const midBtn = $(".s-gen-mid", card);
  const midN = s.midframes_expected != null
    ? s.midframes_expected
    : Math.max(0, Math.min(4, Math.round(s.duration_sec / 2) - 1));
  const midBusy = midframesBusy(s);
  midBtn.textContent = midBusy
    ? t("scene.midBusy", {
        a: (s.midframes || []).length,
        b: (midframesExpect.get(s.id) || { n: midN }).n,
      })
    : t("scene.midBtn", { n: midN });
  midBtn.disabled = !midN || !s.image_url || midBusy || imgBusy;
  midBtn.title = !midN ? t("scene.midShort")
    : !s.image_url ? t("scene.midNoFrame")
    : t("scene.midTitle");
  midBtn.addEventListener("click", async () => {
    try {
      const r = await api(`/api/scenes/${s.id}/generate-midframes`, { method: "POST" });
      midframesExpect.set(s.id, { n: r.count, ts: Date.now() });
    } catch (e) {
      fail(e);
    }
    await loadProject();
  });

  // Видео сцены + отрезок трека под неё.
  const vidStatus = statusLabel(s.video_status);
  const vidStatusEl = $(".s-video-status", card) || $(".s-anim-status", card);
  if (vidStatusEl) {
    vidStatusEl.textContent = vidStatus.text;
    vidStatusEl.className = vidStatusEl.className.split(" ")[0] + " status " + vidStatus.cls;
  }
  if (s.video_url) {
    const v = $(".s-video-preview", card);
    v.src = s.video_url; v.classList.remove("hidden");
    v.poster = s.image_thumb_url || "";
  } else if (s.image_url) {
    // Видео ещё нет — в «Анимации» показываем первый кадр как постер сцены.
    const ph = $(".s-image-preview", card);
    if (ph) { ph.src = s.image_thumb_url || s.image_url; ph.classList.remove("hidden"); }
  }
  // ── ДВИЖОК КАДРА: наследование от объекта, переопределение по требованию ──
  // Ключевое отличие от прежнего кода: чипы больше не «выбор по умолчанию».
  // Умолчание задано на треке, здесь только исключение — и оно свёрнуто,
  // потому что на треке из тридцати сцен развёрнутые чипы занимали экран
  // тридцать раз подряд.
  // Индекс, а не поиск. Раньше здесь стоял find по всем трекам со сканом их
  // сцен НА КАЖДУЮ сцену: альбом 10×30 = 300 сцен давал до 90 000 сравнений
  // за перерисовку, и вдвое больше — потому что сцена рисовалась в двух
  // лентах. Данные для карты лежат в том же project.
  const trackOfScene = sceneTrack(s.id);
  const engLine = $(".s-engine-line", card);
  if (engLine && trackOfScene) paintSceneEngineLine(engLine, trackOfScene, s.id);

  const imgSeg = $(".s-image-seg", card);
  // Чипы движков строятся ЛЕНИВО — по раскрытию «поменять для этого кадра».
  // buildEngineTabs звался дважды на карточку (кадры и видео) и давал до 38
  // узлов, которые в свёрнутом <details> никто никогда не видел.
  const buildSceneEngines = () => {
    if (card.dataset.engBuilt) return;
    card.dataset.engBuilt = "1";
    if (imgSeg) {
      buildEngineTabs(imgSeg, liveImageEngines(), "",
        (e) => e.frames_cost,
        (id) => {
          imgSeg.dataset.engine = id || "";
          $$(".eng-chip", imgSeg).forEach((el) =>
            el.classList.toggle("on", el.dataset.engine === id));
        });
    }
    const seg = $(".s-provider-seg", card);
    if (seg && card.__applyEngine) {
      buildEngineTabs(seg, liveVideoEngines(), "", (e) => e.scene_cost,
                      card.__applyEngine);
    }
  };
  if (imgSeg) {
    // По умолчанию подсвечен «По тарифу» — то есть НАСЛЕДОВАНИЕ от объекта, а
    // не «движок этой сцены». Иначе поменять движок сразу всему треку было бы
    // нельзя: каждая уже отрисованная сцена держала бы старый.
    imgSeg.dataset.engine = "";
  }
  const engDet = $(".s-engine-override", card);
  if (engDet) engDet.addEventListener("toggle", () => { if (engDet.open) buildSceneEngines(); });

  const provSel = $(".s-provider", card);
  const provSeg = $(".s-provider-seg", card);
  if (provSel) {
    provSel.innerHTML = "";
    // Источник правды — video_engines из /api/providers: там КОНКРЕТНЫЕ модели
    // с настоящими именами и ценой. Раньше рисовался список семейств, и всё,
    // что не seedance, подписывалось «Grok» — поэтому Kling выглядел вторым Grok.
    const engineList = liveVideoEngines();
    engineList.forEach((e) => {
      const opt = document.createElement("option");
      opt.value = e.family || "grok";
      opt.dataset.engine = e.id;
      opt.textContent = `${e.title} · ${e.scene_cost}`;
      provSel.appendChild(opt);
    });
    const inherited = effVideoEngine(trackOfScene);
    const applyEngine = (id) => {
      // Семейство (grok|seedance|kling) сервер по-прежнему принимает отдельно:
      // select остаётся источником правды для него.
      const spec = videoEngineById(id || inherited);
      if (spec) {
        const opt = $$("option", provSel).find((o) => o.dataset.engine === spec.id);
        provSel.value = opt ? opt.value : (spec.family || "grok");
      }
      // dataset.engine пустой = «как у объекта»: цепочку сцена → трек → тариф
      // разрешает сервер, и второй её копии на клиенте нет.
      provSel.dataset.engine = id || "";
      if (provSeg) {
        $$(".eng-chip", provSeg).forEach((el) =>
          el.classList.toggle("on", el.dataset.engine === id));
      }
    };
    // Чипы соберутся при раскрытии «поменять для этого кадра» — здесь только
    // запоминаем, чем их наполнять.
    card.__applyEngine = applyEngine;
    applyEngine("");
  }

  // Раскрытый промпт занимает всю ширину карточки, свёрнутый — жмётся вправо.
  const promptDet = $(".s-prompt-details", card);
  if (promptDet) {
    promptDet.addEventListener("toggle", () =>
      card.classList.toggle("prompt-open", promptDet.open));
  }

  const vidBtn = $(".s-gen-video", card);
  if (vidBtn) {
  const vidBusy = ["queued", "running"].includes(s.video_status);
  vidBtn.disabled = vidBusy || !s.image_url;
  vidBtn.textContent = vidBusy ? t("scene.videoBusy")
    : !s.image_url ? t("scene.videoNoFrame")
    : s.video_url ? t("scene.regenVideo") : t("scene.genVideo");
  vidBtn.title = !s.image_url ? t("scene.videoTitleNoFrame") : t("scene.videoTitle");
  vidBtn.addEventListener("click", () => genSceneVideo(s.id, provSel.value, provSel.dataset.engine || ""));
  }

  if (s.audio_url) {
    const row = $(".scene-audio-row", card);
    row.classList.remove("hidden");
    $(".s-audio", row).src = s.audio_url;
  }

  const approveBox = $(".s-approve", card);
  approveBox.checked = s.approved;
  approveBox.disabled = !s.video_url;
  if (!s.video_url) approveBox.title = t("scene.approveNeedVideo");
  approveBox.addEventListener("change", () => approveScene(s.id, approveBox.checked));

  return card;
}

async function saveProject() {
  await api(`/api/project?project_id=${activeProjectId}`, {
    method: "PATCH",
    body: { name: $("#project-name").value, character_bible: $("#character-bible").value },
  });
}
$("#save-project-btn").addEventListener("click", saveProject);

$("#gen-story-btn").addEventListener("click", async () => {
  await saveProject();
  try {
    await api(`/api/project/generate-story?project_id=${activeProjectId}`, { method: "POST" });
  } catch (e) {
    fail(e);
  }
  await loadProject();
});

async function moveTrack(id, dir) {
  const ids = project.tracks.map((tr) => tr.id);
  const idx = ids.indexOf(id);
  const swapWith = idx + dir;
  if (swapWith < 0 || swapWith >= ids.length) return;
  [ids[idx], ids[swapWith]] = [ids[swapWith], ids[idx]];
  await api("/api/tracks/reorder", { method: "POST", body: { order: ids } });
  await loadProject();
}

async function deleteTrack(id) {
  if (!confirm(t("track.delConfirm"))) return;
  await api(`/api/tracks/${id}`, { method: "DELETE" });
  await loadProject();
}

async function saveTrack(id, card) {
  // Стиль сохраняется СВОИМ роутом и ключами: PATCH больше не принимает
  // текст промпта — реестр живёт на сервере, и собирать его тут нечем.
  const keys = ($(".t-style", card).value || "").split(",").filter(Boolean);
  await api(`/api/tracks/${id}/style`, { method: "POST", body: { style_keys: keys } });
  await api(`/api/tracks/${id}`, {
    method: "PATCH",
    body: {
      title: $(".t-title", card).value,
      comment: $(".t-comment", card).value,
      film_grain: $(".t-grain", card).checked,
      no_story: $(".t-nostory", card).checked,
      lyrics: $(".t-lyrics", card).value,
    },
  });
  await loadProject();
}

async function genStoryboard(id) {
  try {
    await api(`/api/tracks/${id}/generate-storyboard`, { method: "POST" });
  } catch (e) {
    fail(e);
  }
  await loadProject();
}

async function assembleClip(id) {
  try {
    await api(`/api/tracks/${id}/assemble`, { method: "POST" });
  } catch (e) {
    fail(e);
  }
  await loadProject();
}

function openSupergenModal(tr) {
  openModal(t("modal.supergen.title"), (body) => {
    // Чек-лист готовности: без стиля и персонажей генератор выдумывает своё.
    const chars = (project.characters || []).filter((c) => (c.name || "").trim());
    const checks = [
      [Boolean(tr.has_style), t("modal.supergen.styleOk"), t("modal.supergen.styleBad")],
      [chars.length > 0, t("modal.supergen.charsOk", { names: chars.map((c) => c.name).join(", ") }),
        t("modal.supergen.charsBad")],
      [Boolean((tr.comment || "").trim() || (tr.lyrics || "").trim()),
        t("modal.supergen.ideaOk"), t("modal.supergen.ideaBad")],
    ];
    const list = document.createElement("div");
    list.style.margin = "0 0 12px";
    let ready = true;
    checks.forEach(([ok, okText, badText]) => {
      const row = document.createElement("p");
      row.style.margin = "4px 0";
      row.textContent = (ok ? "✓ " : "✕ ") + (ok ? okText : badText);
      if (!ok) { row.style.color = "var(--danger)"; ready = false; }
      list.appendChild(row);
    });
    body.appendChild(list);
    const info = document.createElement("p");
    info.className = "muted";
    info.style.margin = "0 0 12px";
    info.textContent = t("modal.supergen.info");
    body.appendChild(info);
    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = t("modal.supergen.go");
    go.disabled = !ready;
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await api(`/api/tracks/${tr.id}/supergen`, { method: "POST" });
        closeModal();
        await loadProject();
      } catch (e) {
        go.disabled = false;
        fail(e);
      }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = t("common.cancel");
    cancel.addEventListener("click", closeModal);
    row.appendChild(go);
    row.appendChild(cancel);
    body.appendChild(row);
  });
}

async function genScenes(id) {
  try {
    await api(`/api/tracks/${id}/generate-scenes`, { method: "POST" });
  } catch (e) {
    fail(e); // в т.ч. «не хватает очков» — текст соберёт errText()
  }
  await loadProject();
}

async function saveScene(id, card) {
  await api(`/api/scenes/${id}`, {
    method: "PATCH",
    body: {
      duration_sec: Number($(".s-duration", card).value) || 6,
      shot_size: $(".s-shotsize", card).value,
      camera_move: $(".s-camera", card).value,
      characters: $(".s-chars", card).value,
      lyric_line: $(".s-lyric", card).value,
      shot_note: $(".s-note", card).value,
      image_prompt: $(".s-image", card).value,
      motion_prompt: $(".s-motion", card).value,
      image_prompt_last: $(".s-motion-last", card).value,
    },
  });
  await loadProject();
}

async function deleteScene(id) {
  if (!confirm(t("scene.delConfirm"))) return;
  await api(`/api/scenes/${id}`, { method: "DELETE" });
  await loadProject();
}

async function genSceneFrames(id, which = "both", engine = "") {
  try {
    // engine пустой = «как у объекта»: сервер разрешит цепочку
    // сцена → трек → тариф сам, второй копии этой логики на клиенте нет.
    const q = engine ? `&engine=${encodeURIComponent(engine)}` : "";
    await api(`/api/scenes/${id}/generate-frames?which=${which}${q}`, { method: "POST" });
  } catch (e) {
    fail(e);
  }
  await loadProject();
}

async function genSceneVideo(id, provider, engine) {
  try {
    // engine — конкретная модель (seedance-2-5, kling-3.0-pro…). Без неё сервер
    // берёт дефолт семейства, и выбор чипа ни на что не влиял бы.
    await api(`/api/scenes/${id}/generate-video`, { method: "POST", body: { provider, engine: engine || "" } });
  } catch (e) {
    fail(e);
  }
  await loadProject();
}

async function approveScene(id, approved) {
  try {
    await api(`/api/scenes/${id}/approve`, { method: "POST", body: { approved } });
  } catch (e) {
    fail(e);
  }
  await loadProject();
}

$("#add-track-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData();
  fd.append("title", form.title.value);
  fd.append("style_keys", form.style_keys.value);
  fd.append("lyrics", form.lyrics.value);
  fd.append("comment", form.comment.value);
  if (form.format_key && form.format_key.value) fd.append("format_key", form.format_key.value);
  if (form.audio.files[0]) fd.append("audio", form.audio.files[0]);
  await api(`/api/tracks?project_id=${activeProjectId}`, { method: "POST", body: fd });
  form.reset();
  await loadProject();
});

// Визитка персонажа в ленте: моделька, имя, «главный», счётчик атрибутов.
// Всё остальное — в досье (openCharacterModal): панель не должна занимать
// пол-экрана ради трёх героев.
function renderCharacter(c) {
  const tpl = $("#char-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".char-card");
  applyI18n(card);
  card.dataset.id = c.id;
  const name = (c.name || "").trim();
  $(".cc-name", card).textContent = name || t("character.noName");
  // На визитке показываем ТУ картинку, которая реально уедет в кадры сцен
  // (разворот, если он есть), а не первое попавшееся фото.
  const photos = c.photos || [];
  const photo = photos.find((p) => p.id === c.model_photo_id) || photos[0];
  if (photo) {
    const img = $(".cc-img", card);
    img.src = photo.url + `?t=${photo.id}`;
    img.classList.remove("hidden");
  } else {
    $(".cc-ph", card).textContent = (name || "?").charAt(0).toUpperCase() || "?";
  }
  if (c.is_main) $(".cc-main", card).classList.remove("hidden");
  const attrs = (c.attributes || []).length;
  $(".cc-attrs", card).textContent = attrs
    ? t("character.attrsN", { n: attrs })
    : t("character.attrsNone");
  card.addEventListener("click", () => openCharacterModal(c));
  return card;
}

// Досье персонажа в модалке: имя, описание, фото-модельки, атрибуты.
// Любая правка внутри перезагружает проект — поэтому тело модалки после неё
// пересобирается на свежих данных, а не остаётся врать прошлым снимком.
async function charModalRefresh(id) {
  await loadProject();
  const body = $("#modal-body");
  if (body.dataset.charId !== String(id)) return;   // модалку уже закрыли или сменили
  const fresh = (project.characters || []).find((c) => c.id === id);
  if (fresh) openCharacterModal(fresh);
  else closeModal();
}

function openCharacterModal(c) {
  openModal((c.name || "").trim() || t("modal.character.title"), (body) => {
    body.dataset.charId = String(c.id);
    const card = $("#char-edit-tpl").content.cloneNode(true).querySelector(".char-edit");
    applyI18n(card);
    body.appendChild(card);
    bindCharacterEditor(card, c);
  });
}

function bindCharacterEditor(card, c) {
  const back = () => charModalRefresh(c.id);
  card.dataset.id = c.id;
  $(".c-name", card).value = c.name;
  $(".c-desc", card).value = c.description;
  $(".c-main", card).checked = c.is_main;
  // Два ряда: ЗАГРУЖЕННЫЕ фото (они уходят референсом в разворот) и
  // СГЕНЕРИРОВАННЫЕ развороты (последний из них берут кадры сцен). Одна
  // общая куча скрывала главное: какая именно картинка работает.
  const photosBox = $(".char-photos", card);
  const modelsBox = $(".char-models", card);
  const modelsHead = $(".char-models-head", card);
  const all = c.photos || [];
  const uploads = all.filter((p) => (p.kind || "photo") !== "model");
  const models = all.filter((p) => (p.kind || "photo") === "model");

  const photoTile = (ph, isModel) => {
    const wrap = document.createElement("div");
    wrap.className = "char-photo" + (isModel ? " char-photo-model" : "");
    if (ph.id === c.model_photo_id) wrap.classList.add("in-use");
    const img = document.createElement("img");
    img.src = (ph.thumb_url || ph.url) + `?t=${ph.id}`;
    img.loading = "lazy";
    // Миниатюру делает ffmpeg; не вышло — показываем оригинал, а не иконку
    // битой картинки на месте лица героя.
    img.addEventListener("error", () => {
      const full = ph.url + `?t=${ph.id}`;
      if (!img.src.endsWith(full)) img.src = full;
    }, { once: true });
    wrap.appendChild(img);

    const del = document.createElement("button");
    del.className = "ghost danger char-photo-del";
    del.textContent = "✕";
    del.title = t("character.photoDel");
    del.addEventListener("click", async () => {
      await api(`/api/characters/photos/${ph.id}`, { method: "DELETE" });
      await back();
    });
    wrap.appendChild(del);

    if (isModel) {
      const cap = document.createElement("span");
      cap.className = "char-photo-cap";
      // Разворот честно говорит, по скольким фото он собран: «не похоже»
      // почти всегда означает «фото не было ни одного».
      cap.textContent = ph.from_photos
        ? t("character.builtFrom", { n: ph.from_photos })
        : t("character.builtFromNone");
      wrap.appendChild(cap);
      if (ph.id === c.model_photo_id) {
        const badge = document.createElement("span");
        badge.className = "char-photo-badge";
        badge.textContent = t("character.inUse");
        wrap.appendChild(badge);
      } else {
        const use = document.createElement("button");
        use.className = "ghost char-photo-use";
        use.textContent = t("character.makePrimary");
        use.addEventListener("click", async () => {
          await api(`/api/characters/photos/${ph.id}`, { method: "PATCH", body: { primary: true } });
          await back();
        });
        wrap.appendChild(use);
      }
    } else {
      // Подпись ракурса уезжает прямо в промпт разворота. Классификатора у
      // нас нет и не надо: человек знает про своё фото больше любой модели.
      const pose = document.createElement("select");
      pose.className = "char-photo-pose";
      pose.title = t("character.poseTitle");
      [["", "none"], ["face", "face"], ["three_quarter", "three_quarter"],
       ["full", "full"], ["back", "back"]].forEach(([v, key]) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t(`character.pose.${key}`);
        pose.appendChild(o);
      });
      pose.value = ph.pose_kind || "";
      pose.addEventListener("change", async () => {
        try {
          await api(`/api/characters/photos/${ph.id}`, { method: "PATCH", body: { pose: pose.value } });
        } catch (e) { fail(e); }
      });
      wrap.appendChild(pose);
    }
    return wrap;
  };

  uploads.forEach((ph) => photosBox.appendChild(photoTile(ph, false)));
  if (models.length) {
    modelsHead.classList.remove("hidden");
    modelsBox.classList.remove("hidden");
    models.forEach((ph) => modelsBox.appendChild(photoTile(ph, true)));
  }
  $(".c-save", card).addEventListener("click", async () => {
    await api(`/api/characters/${c.id}`, { method: "PATCH", body: {
      name: $(".c-name", card).value,
      description: $(".c-desc", card).value,
      is_main: $(".c-main", card).checked,
    }});
    closeModal();
    await loadProject();
  });
  $(".c-del", card).addEventListener("click", async () => {
    if (!confirm(t("character.delConfirm", { name: c.name }))) return;
    await api(`/api/characters/${c.id}`, { method: "DELETE" });
    closeModal();
    await loadProject();
  });
  const genModelBtn = $(".c-gen-model", card);
  if (genModelBtn) genModelBtn.addEventListener("click", () => openModelModal(c, back));
  const input = $(".c-photo-input", card);
  input.addEventListener("change", async () => {
    for (const file of input.files) {
      const fd = new FormData();
      fd.append("photo", file);
      await api(`/api/characters/${c.id}/photos`, { method: "POST", body: fd });
    }
    await back();
  });

  // Атрибуты — фирменные вещи персонажа: чипы с миниатюрами ракурсов.
  const attrsBox = $(".char-attrs", card);
  (c.attributes || []).forEach((a) => attrsBox.appendChild(renderAttribute(a, back)));
  $(".attr-add", card).addEventListener("click", () => openAttributeModal(c.id, null, back));

  return card;
}

// Чип атрибута: имя (клик = редактирование), миниатюры фото с ✕, «+ фото», ✕ атрибута.
function renderAttribute(a, onDone = null) {
  const chip = document.createElement("div");
  chip.className = "attr-chip";
  chip.dataset.id = a.id;

  const name = document.createElement("button");
  name.type = "button";
  name.className = "attr-name";
  name.textContent = a.name;
  name.title = (a.description ? a.description + " — " : "") + t("character.attrEditTitle");
  name.addEventListener("click", () => openAttributeModal(null, a, onDone));
  chip.appendChild(name);

  const photos = document.createElement("div");
  photos.className = "attr-photos";
  (a.photos || []).forEach((ph) => {
    const wrap = document.createElement("div");
    wrap.className = "attr-thumb";
    const img = document.createElement("img");
    img.src = ph.url + `?t=${ph.id}`;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost danger attr-photo-del";
    del.textContent = "✕";
    del.title = t("character.photoDel");
    del.addEventListener("click", async () => {
      await api(`/api/attributes/photos/${ph.id}`, { method: "DELETE" });
      if (onDone) await onDone(); else await loadProject();
    });
    wrap.append(img, del);
    photos.appendChild(wrap);
  });
  chip.appendChild(photos);

  const upload = document.createElement("label");
  upload.className = "attr-upload";
  const input = document.createElement("input");
  input.type = "file";
  input.className = "hidden";
  input.accept = "image/jpeg,image/png,image/webp";
  input.multiple = true;
  input.addEventListener("change", async () => {
    for (const file of input.files) {
      const fd = new FormData();
      fd.append("photo", file);
      await api(`/api/attributes/${a.id}/photos`, { method: "POST", body: fd });
    }
    if (onDone) await onDone(); else await loadProject();
  });
  const uploadBtn = document.createElement("span");
  uploadBtn.className = "attr-upload-btn";
  uploadBtn.textContent = t("character.attrPhotoAdd");
  upload.append(input, uploadBtn);
  chip.appendChild(upload);

  const delAttr = document.createElement("button");
  delAttr.type = "button";
  delAttr.className = "ghost danger attr-del";
  delAttr.textContent = "✕";
  delAttr.title = t("character.attrDelTitle");
  delAttr.addEventListener("click", () => confirmDeleteAttribute(a, onDone));
  chip.appendChild(delAttr);

  return chip;
}

// Одна модалка на создание (charId) и редактирование (attr) атрибута.
// onDone — куда вернуться после сохранения: из досье персонажа это оно само,
// иначе просто закрытие модалки.
function openAttributeModal(charId, attr = null, onDone = null) {
  openModal(t(attr ? "modal.attribute.editTitle" : "modal.attribute.newTitle"), (body) => {
    body.innerHTML = `
      <label>${escHtml(t("modal.attribute.nameLabel"))}</label>
      <input class="at-name" placeholder="${escHtml(t("modal.attribute.namePh"))}" />
      <label>${escHtml(t("modal.attribute.descLabel"))}</label>
      <textarea class="at-desc" rows="2"></textarea>
      <div class="row">
        <button type="button" class="primary at-save">${escHtml(t(attr ? "common.save" : "common.create"))}</button>
        <span class="at-error error hidden"></span>
      </div>`;
    const nameInput = $(".at-name", body);
    const descInput = $(".at-desc", body);
    if (attr) { nameInput.value = attr.name; descInput.value = attr.description || ""; }
    const errEl = $(".at-error", body);
    const saveBtn = $(".at-save", body);
    const save = async () => {
      const name = nameInput.value.trim();
      if (!name) {
        errEl.textContent = t("modal.attribute.nameRequired");
        errEl.classList.remove("hidden");
        return;
      }
      saveBtn.disabled = true;
      try {
        const payload = { name, description: descInput.value.trim() };
        if (attr) await api(`/api/attributes/${attr.id}`, { method: "PATCH", body: payload });
        else await api(`/api/characters/${charId}/attributes`, { method: "POST", body: payload });
        if (onDone) { await onDone(); } else { closeModal(); await loadProject(); }
      } catch (e) {
        errEl.textContent = errText(e);
        errEl.classList.remove("hidden");
        saveBtn.disabled = false;
      }
    };
    saveBtn.addEventListener("click", save);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    nameInput.focus();
  });
}

function confirmDeleteAttribute(a, onDone = null) {
  openModal(t("modal.attribute.delTitle"), (body) => {
    body.innerHTML = `
      <p class="muted attr-del-text" style="margin:10px 0 0"></p>
      <div class="row">
        <button type="button" class="primary ad-yes">${escHtml(t("common.del"))}</button>
        <button type="button" class="ad-no">${escHtml(t("common.cancel"))}</button>
        <span class="ad-error error hidden"></span>
      </div>`;
    $(".attr-del-text", body).textContent = t("modal.attribute.delText", { name: a.name });
    $(".ad-no", body).addEventListener("click", closeModal);
    const yesBtn = $(".ad-yes", body);
    yesBtn.addEventListener("click", async () => {
      yesBtn.disabled = true;
      try {
        await api(`/api/attributes/${a.id}`, { method: "DELETE" });
        if (onDone) { await onDone(); } else { closeModal(); await loadProject(); }
      } catch (e) {
        const errEl = $(".ad-error", body);
        errEl.textContent = errText(e);
        errEl.classList.remove("hidden");
        yesBtn.disabled = false;
      }
    });
  });
}

$("#add-character-btn").addEventListener("click", () => {
  openModal(t("modal.addChar.title"), (body) => {
    body.innerHTML = `
      <div class="modal-tabs">
        <button type="button" class="modal-tab on" data-tab="new">${escHtml(t("modal.addChar.tabNew"))}</button>
        <button type="button" class="modal-tab" data-tab="library">${escHtml(t("modal.addChar.tabLibrary"))}</button>
      </div>
      <div class="tab-pane" data-pane="new">
        <label>${escHtml(t("modal.addChar.nameLabel"))}</label>
        <input class="ch-name" placeholder="${escHtml(t("modal.addChar.namePh"))}" />
        <div class="row">
          <button type="button" class="primary ch-create">${escHtml(t("common.create"))}</button>
          <span class="ch-error error hidden"></span>
        </div>
      </div>
      <div class="tab-pane hidden" data-pane="library">
        <p class="muted" style="margin:10px 0 0">${escHtml(t("modal.addChar.libLead"))}</p>
        <div class="lib-grid"><span class="muted">${escHtml(t("common.loading"))}</span></div>
        <span class="lib-error error hidden"></span>
      </div>`;

    // Переключение вкладок
    let libLoaded = false;
    $$(".modal-tab", body).forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".modal-tab", body).forEach((el) => el.classList.toggle("on", el === tab));
        $$(".tab-pane", body).forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab.dataset.tab));
        if (tab.dataset.tab === "library" && !libLoaded) { libLoaded = true; loadLibrary(); }
      });
    });

    // Вкладка «Новый»
    const nameInput = $(".ch-name", body);
    const errEl = $(".ch-error", body);
    const createBtn = $(".ch-create", body);
    const create = async () => {
      const name = nameInput.value.trim();
      if (!name) {
        errEl.textContent = t("modal.addChar.nameRequired");
        errEl.classList.remove("hidden");
        return;
      }
      createBtn.disabled = true;
      try {
        await api(`/api/characters?project_id=${activeProjectId}`, { method: "POST", body: { name } });
        closeModal();
        await loadProject();
      } catch (e) {
        errEl.textContent = errText(e);
        errEl.classList.remove("hidden");
        createBtn.disabled = false;
      }
    };
    createBtn.addEventListener("click", create);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
    nameInput.focus();

    // Вкладка «Из базы»: все персонажи всех проектов, клик = клон в текущий
    async function loadLibrary() {
      const grid = $(".lib-grid", body);
      const libErr = $(".lib-error", body);
      try {
        const raw = await api("/api/characters/library");
        // Клоны по проектам — это один и тот же герой: схлопываем по имени,
        // источником клона берём копию с максимумом фото-моделек.
        const byName = new Map();
        for (const c of raw) {
          const key = (c.name || "").trim().toLowerCase();
          const here = c.project_id === activeProjectId;
          const prev = byName.get(key);
          if (!prev) {
            byName.set(key, { ...c, here });
            continue;
          }
          prev.here = prev.here || here;
          if ((c.photos?.length || 0) > (prev.photos?.length || 0)) {
            byName.set(key, { ...c, here: prev.here });
          }
        }
        const chars = [...byName.values()];
        grid.innerHTML = "";
        if (!chars.length) {
          grid.innerHTML = `<span class="muted">${escHtml(t("modal.addChar.libEmpty"))}</span>`;
          return;
        }
        for (const c of chars) {
          const el = document.createElement("button");
          el.type = "button";
          const here = c.here;
          el.className = "lib-card" + (here ? " here" : "");
          const thumb = document.createElement("span");
          thumb.className = "lib-thumb";
          if (c.photos && c.photos.length) {
            const im = document.createElement("img");
            im.src = c.photos[0].url;
            im.alt = "";
            thumb.appendChild(im);
          } else {
            thumb.textContent = (c.name || "?").trim().charAt(0).toUpperCase() || "?";
          }
          const nm = document.createElement("b");
          nm.textContent = c.name;
          const from = document.createElement("span");
          from.className = "muted";
          from.textContent = here ? t("modal.addChar.libHere") : c.project_name;
          el.append(thumb, nm, from);
          if (here) {
            el.disabled = true;
          } else {
            el.addEventListener("click", async () => {
              el.disabled = true;
              libErr.classList.add("hidden");
              try {
                await api(`/api/characters/clone?project_id=${activeProjectId}`, {
                  method: "POST",
                  body: { source_id: c.id },
                });
                closeModal();
                await loadProject();
              } catch (e) {
                libErr.textContent = errText(e);
                libErr.classList.remove("hidden");
                el.disabled = false;
              }
            });
          }
          grid.appendChild(el);
        }
      } catch (e) {
        grid.innerHTML = "";
        libErr.textContent = errText(e);
        libErr.classList.remove("hidden");
      }
    }
  });
});

async function addManualScene(trackId) {
  await api(`/api/tracks/${trackId}/scenes`, { method: "POST", body: {} });
  await loadProject();
}

// Пикер стилей формы «добавить трек» рисуется вне render(), поэтому его
// пересобираем отдельно — на старте и при смене языка.
function rebuildAddTrackPicker() {
  const form = document.querySelector("#add-track-form");
  if (!form) return;
  // Скрытое поле называется style_keys и хранит csv ключей: полного текста
  // промпта на клиенте больше нет — его собирает сервер при создании трека.
  const current = (form.style_keys.value || "").split(",").filter(Boolean);
  buildStylePicker(form.querySelector(".style-picker"), current,
                   (keys) => { form.style_keys.value = keys.join(","); });
}
rebuildAddTrackPicker();

(async () => {
  // ── мини-апп Telegram ──
  // Вход делается ДО первого /api/me: иначе человек на долю секунды увидит
  // лендинг, которого внутри Telegram быть не должно.
  if (window.TGA && TGA.active) {
    try {
      await TGA.signIn();
    } catch (e) {
      showTgFail(e);
      return;
    }
    me = await api("/api/me");
    if (!me.authed) { showTgFail(new Error("no_session")); return; }
    showApp();
    tgAfterSignIn();
    return;
  }
  me = await api("/api/me");
  // Без сессии гость видит главную, а не форму пароля. С живой сессией сразу
  // открывается студия — кроме случая, когда человек пришёл именно на главную
  // (ссылка с ?home или якорь #ld-…): тогда первый экран зовёт в студию.
  if (me.authed && !ldWantsLanding()) showApp(); else showWelcome();
  // Возврат из кассы: ЮKassa приводит на /?paid=<тариф>. Открываем кабинет на
  // вкладке тарифа, чтобы человек своими глазами увидел, что тариф встал.
  const paid = new URLSearchParams(location.search).get("paid");
  if (paid) {
    stripQueryParam("paid");
    if (me.authed) openAccountModal("plan");
  }
})();

// Лист раскадровки крупно: в карточке трека он живёт миниатюрой, а разглядывать
// его нужно во весь экран. Два режима — «вписать» и 1:1 с прокруткой; стрелки
// листают лист по горизонтали, когда он не влезает целиком.
function openSheetModal(tr) {
  if (!tr.storyboard_url) return;
  openModal(t("modal.sheet.title"), (body) => {
    const wrap = document.createElement("div");
    wrap.className = "sheet-view fit";
    const img = document.createElement("img");
    img.src = tr.storyboard_url;
    img.alt = "";
    wrap.appendChild(img);

    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "strip-arrow sheet-prev";
    prev.textContent = "‹";
    prev.setAttribute("aria-label", t("common.prev"));
    const next = document.createElement("button");
    next.type = "button";
    next.className = "strip-arrow sheet-next";
    next.textContent = "›";
    next.setAttribute("aria-label", t("common.next"));
    const step = () => Math.max(240, Math.round(wrap.clientWidth * 0.8));
    prev.addEventListener("click", () => wrap.scrollBy({ left: -step(), behavior: "smooth" }));
    next.addEventListener("click", () => wrap.scrollBy({ left: step(), behavior: "smooth" }));

    const stage = document.createElement("div");
    stage.className = "sheet-stage";
    stage.append(prev, wrap, next);
    body.appendChild(stage);

    const row = document.createElement("div");
    row.className = "row";
    const zoom = document.createElement("button");
    zoom.type = "button";
    zoom.textContent = t("modal.sheet.full");
    zoom.addEventListener("click", () => {
      const fit = wrap.classList.toggle("fit");
      zoom.textContent = fit ? t("modal.sheet.full") : t("modal.sheet.fit");
    });
    const open = document.createElement("a");
    open.className = "clip-download";
    open.href = tr.storyboard_url;
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = t("modal.sheet.original");
    const close = document.createElement("button");
    close.className = "ghost";
    close.textContent = t("common.close");
    close.addEventListener("click", closeModal);
    row.append(zoom, open, close);
    body.appendChild(row);
  }, { wide: true });
}

// Разбор листа раскадровки: ячейки сеткой, владелец сам решает, какие взять
// и в какие сцены их положить. Не выбранные сцены остаются как есть.
async function openCellsModal(tr) {
  let data;
  try {
    data = await api(`/api/tracks/${tr.id}/storyboard-cells`, { method: "POST" });
  } catch (e) { fail(e); return; }
  const scenes = tr.scenes || [];
  openModal(t("modal.cells.title"), (body) => {
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.margin = "0 0 10px";
    hint.textContent = t("modal.cells.hint");
    body.appendChild(hint);
    const grid = document.createElement("div");
    grid.className = "cells-grid";
    const rows = [];
    data.cells.forEach((c, i) => {
      const box = document.createElement("div");
      box.className = "cell-card";
      const img = document.createElement("img");
      img.src = c.thumb_url || c.url;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      const sel = document.createElement("select");
      scenes.forEach((sc) => {
        const o = document.createElement("option");
        o.value = sc.id;
        o.textContent = t("modal.cells.toScene", { n: sc.position });
        sel.appendChild(o);
      });
      if (scenes[i]) sel.value = scenes[i].id;
      const foot = document.createElement("div");
      foot.className = "cell-foot";
      foot.appendChild(cb);
      foot.appendChild(sel);
      box.appendChild(img);
      box.appendChild(foot);
      grid.appendChild(box);
      rows.push({ cb, sel, filename: c.filename });
    });
    body.appendChild(grid);
    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = t("modal.cells.apply");
    go.addEventListener("click", async () => {
      const pairs = rows.filter((r) => r.cb.checked)
        .map((r) => ({ filename: r.filename, scene_id: Number(r.sel.value) }));
      if (!pairs.length) { alert(t("modal.cells.nonePicked")); return; }
      go.disabled = true;
      try {
        const res = await api(`/api/tracks/${tr.id}/apply-cells`, { method: "POST", body: { pairs } });
        closeModal();
        alert(t("modal.cells.applied", { n: res.applied }));
        await loadProject();
      } catch (e) { go.disabled = false; fail(e); }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = t("common.cancel");
    cancel.addEventListener("click", closeModal);
    row.appendChild(go);
    row.appendChild(cancel);
    body.appendChild(row);
  });
}

// 3D-РАЗВОРОТ персонажа: лист ракурсов, собранный по его фотографиям.
//
// Слово «3D-модель» здесь не употребляется намеренно: вращаемого объекта не
// получится, и обещать его нельзя. Что получится — лист ракурсов в
// 3D-рендер-стиле, из которого дальше строятся кадры сцен; ровно это и
// написано под заголовком, до нажатия кнопки, а не после.
function openModelModal(c, onDone = null) {
  openModal(t("modal.model.title", { name: c.name || t("modal.model.someone") }), (body) => {
    // openModal стирает data-char-id, а charModalRefresh по нему решает, не
    // закрыл ли человек модалку. Без этой строки досье после генерации не
    // возвращалось: модалка навсегда застывала на «генерирую…».
    body.dataset.charId = String(c.id);
    const uploads = (c.photos || []).filter((p) => (p.kind || "photo") !== "model");

    const what = document.createElement("p");
    what.className = "muted";
    what.style.margin = "0 0 8px";
    what.textContent = t("modal.model.what");
    body.appendChild(what);

    const info = document.createElement("p");
    // Отсутствие фото и «фото всего одно» — это предупреждения, а не ошибки:
    // красный тут читался бы как «сломалось», хотя генерация пойдёт.
    info.className = uploads.length > 1 ? "muted" : "modal-warn";
    info.style.margin = "0 0 10px";
    if (!uploads.length) info.textContent = t("modal.model.noPhotos");
    else if (uploads.length === 1) info.textContent = t("modal.model.onePhoto");
    else info.textContent = t("modal.model.withPhotos", { n: Math.min(6, uploads.length) });
    body.appendChild(info);

    const lab = document.createElement("label");
    lab.textContent = t("modal.model.descLabel");
    lab.title = t("modal.model.descHint");
    body.appendChild(lab);
    const ta = document.createElement("textarea");
    ta.rows = 4;
    ta.value = c.description || "";
    ta.placeholder = t("modal.model.descPh");
    body.appendChild(ta);
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.margin = "4px 0 10px";
    hint.textContent = t("modal.model.descHint");
    body.appendChild(hint);

    const lab2 = document.createElement("label");
    lab2.textContent = t("modal.model.kindLabel");
    body.appendChild(lab2);
    const sel = document.createElement("select");
    [["3d", "modal.model.kind3d"], ["real", "modal.model.kindReal"],
     ["anime", "modal.model.kindAnime"]].forEach(([v, key]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t(key); sel.appendChild(o);
    });
    body.appendChild(sel);

    // Второй тип листа: крупные планы лица и кистей. Для узнаваемости героя
    // в кадрах он полезнее полного роста — там лицо занимает три пикселя.
    const lab3 = document.createElement("label");
    lab3.textContent = t("modal.model.viewsLabel");
    body.appendChild(lab3);
    const views = document.createElement("select");
    [["full", "modal.model.viewsFull"], ["closeup", "modal.model.viewsCloseup"]]
      .forEach(([v, key]) => {
        const o = document.createElement("option");
        o.value = v; o.textContent = t(key); views.appendChild(o);
      });
    body.appendChild(views);

    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = t("common.generate");
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = t("modal.model.busy");
      try {
        const res = await api(`/api/characters/${c.id}/generate-model`, {
          method: "POST",
          body: { description: ta.value, kind: sel.value, views: views.value },
        });
        // Чем и по скольким фото собрано — говорим сразу, не заставляя
        // догадываться по картинке.
        if (res && res.engine_title) {
          info.className = "muted";
          info.textContent = t("modal.model.engine",
            { name: res.engine_title, n: res.from_photos || 0 });
        }
        if (onDone) { await onDone(); } else { closeModal(); await loadProject(); }
      } catch (e) {
        go.disabled = false;
        go.textContent = t("common.generate");
        fail(e);
      }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = t("common.cancel");
    cancel.addEventListener("click", closeModal);
    row.appendChild(go); row.appendChild(cancel);
    body.appendChild(row);
  });
}

// Гайд со скриншотами открывается модалкой — лендинг остаётся в один экран.
{
  const link = document.querySelector("#welcome-guide-link");
  const modal = document.querySelector("#guide-modal");
  const closeBtn = document.querySelector("#guide-close");
  const close = () => modal && modal.classList.add("hidden");
  if (link && modal) {
    link.addEventListener("click", (e) => { e.preventDefault(); modal.classList.remove("hidden"); });
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    if (closeBtn) closeBtn.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
}

// ═════════════════════════ ГЛАВНАЯ СТРАНИЦА lolq.ai ═════════════════════════
// Витрина сервиса и воронка: первый экран → как это работает → что внутри →
// тарифы → докупка очков → партнёрка → FAQ → подвал.
//
// ВСЕ пользовательские строки лежат в словаре I18N (i18n.js), раздел landing.*:
// перевод = правка одного файла, разметка и логика не трогаются. В index.html
// текстовые узлы помечены data-i18n="путь.в.словаре" (и data-i18n-alt для alt
// картинок), повторяющиеся блоки рисуются здесь из тех же данных.

// Цена работы в очках — зеркало SCENE_COST в backend/main.py. Если там
// поменяются числа, поменяй и здесь: витрина считает «сколько это клипов».
// Цена сцены в очках — зеркало SCENE_COST в backend/main.py (кадры на шлюзе +
// видео движком). Эти три числа УЖЕ РАЗЪЕЗЖАЛИСЬ с прайсом: было
// {grok:4, seedance:10, top:16} при живых 4 / 101 / 154, то есть витрина
// занижала стоимость впятеро. Живой ответ /api/billing/plans их
// перезаписывает (см. ldNormalizePricing) — здесь только запасной вариант.
const LD_SCENE_COST = { grok: 4, seedance: 22, top: 154 };
const LD_SCENES_PER_CLIP = 30;          // трёхминутный трек ≈ 30 сцен по 6 сек
const LD_REF = { discount: 10, reward: 10 };  // REF_DISCOUNT_PCT / REF_REWARD_PCT

// Запасная витрина: лендинг обязан рисоваться, даже если ответ сервера не
// приехал. Числа ДЕРЖИ СИНХРОННЫМИ с PLANS/TOPUP_PACKS бэкенда — этот блок
// уже однажды протух (PRO 700 очков при живых 660, STUDIO 6000 при 10500,
// пакеты по старым ценам), и заметить это было невозможно.
const LD_PLANS_FALLBACK = [
  { id: "free", points: 150, usd: 0 },
  { id: "pro", points: 660, usd: 20 },
  { id: "pro_max", points: 3400, usd: 100 },
  { id: "studio", points: 10500, usd: 299 },
];
const LD_PACKS_FALLBACK = [
  { id: "p400", points: 400, usd: 15 },
  { id: "p1000", points: 1000, usd: 36 },
  { id: "p2500", points: 2500, usd: 87 },
  { id: "p6000", points: 6000, usd: 199 },
  { id: "p15000", points: 15000, usd: 479 },
];

let ldBuilt = false;         // тяжёлую разметку собираем один раз
let ldPeriod = "month";      // тумблер «помесячно / на год»
let ldPricing = null;        // нормализованный ответ /api/billing/plans
let ldPackIndex = 2;         // выбранная ступень шкалы очков

// ────────── доступ к разделу landing словаря ──────────
// LT — сырое значение (массивы шагов, функций, тарифов), LTX — строка с
// подстановкой {переменных}. Читаются в момент вызова, поэтому смена языка
// подхватывается без перезагрузки страницы.
function LT(path) { return tRaw("landing." + path); }
function LTX(path, vars) { return t("landing." + path, vars); }

// ────────── сборка статичных секций ──────────
function ldBuildSteps() {
  const box = $("#ld-steps");
  if (!box) return;
  box.innerHTML = (LT("how.steps") || []).map((s) => `
    <li class="ld-step">
      <div class="ld-step-copy">
        <span class="ld-step-n">${escHtml(s.n)}</span>
        <h3>${escHtml(s.title)}</h3>
        <p>${escHtml(s.text)}</p>
        <p class="ld-step-meta muted">${escHtml(s.meta)}</p>
      </div>
      <figure class="ld-shot">
        <img src="${escHtml(s.img)}" width="${s.w}" height="${s.h}" loading="lazy"
             alt="${escHtml(s.alt)}" />
      </figure>
    </li>`).join("");
}

function ldBuildFeatures() {
  const box = $("#ld-feats");
  if (!box) return;
  box.innerHTML = (LT("features.items") || []).map((f) => {
    const tag = f.tag ? `<span class="ld-feat-tag">${escHtml(f.tag)}</span>` : "";
    if (f.wide) {
      return `<article class="ld-feat ld-feat-wide">
        <div class="ld-feat-copy">
          <h3>${escHtml(f.title)}</h3>
          <p>${escHtml(f.text)}</p>
          ${tag}
        </div>
        <img src="${escHtml(f.img)}" width="${f.w}" height="${f.h}" loading="lazy"
             alt="${escHtml(f.alt || "")}" />
      </article>`;
    }
    return `<article class="ld-feat">
      <h3>${escHtml(f.title)}</h3>
      <p>${escHtml(f.text)}</p>
      ${tag}
    </article>`;
  }).join("");
}

function ldBuildFaq() {
  const box = $("#ld-faq-list");
  if (!box) return;
  box.innerHTML = (LT("faq.items") || []).map((it) => `
    <details class="ld-q">
      <summary>${escHtml(it.q)}</summary>
      <p class="ld-a">${escHtml(it.a)}</p>
    </details>`).join("");
}

function ldBuildPartner() {
  const box = $("#ld-partner-list");
  if (!box) return;
  // <b> в строках словаря — наша разметка, поэтому идёт как есть.
  box.innerHTML = (LT("partner.items") || [])
    .map((line) => `<li>${tFill(line, LD_REF)}</li>`).join("");
}

function ldBuildFooter() {
  const cols = $("#ld-foot-cols");
  if (cols) {
    cols.innerHTML = (LT("footer.cols") || []).map((c) => `
      <div class="ld-foot-col">
        <h4>${escHtml(c.title)}</h4>
        <ul>${(c.links || []).map((l) => {
          if (l.action === "guide") {
            return `<li><button type="button" data-ld-guide>${escHtml(l.label)}</button></li>`;
          }
          // Пустой href = ссылки ещё нет: показываем честную метку «скоро»,
          // а не заглушку, ведущую в никуда. Метка — из словаря, не из CSS.
          return l.href
            ? `<li><a href="${escHtml(l.href)}">${escHtml(l.label)}</a></li>`
            : `<li><span class="ld-link-off">${escHtml(l.label)}<em>${escHtml(LTX("footer.soon"))}</em></span></li>`;
        }).join("")}</ul>
      </div>`).join("");
    $$("[data-ld-guide]", cols).forEach((b) => b.addEventListener("click", ldOpenGuide));
  }
  // Правовая строка живёт в своём контейнере: перерисовка идемпотентна, и при
  // смене языка строки не дублируются, а авторская подпись остаётся на месте.
  const legal = $("#ld-foot-legal-text");
  if (legal) {
    legal.innerHTML = (LT("footer.legal") || [])
      .map((line) => `<span>${escHtml(line)}</span>`).join("");
  }
}

function ldOpenGuide() {
  const modal = document.querySelector("#guide-modal");
  if (modal) modal.classList.remove("hidden");
}

// ────────── тарифы: живые данные с запасным вариантом ──────────
function ldUsd(src, keyUsd, keyCents) {
  if (src && typeof src[keyUsd] === "number") return src[keyUsd];
  if (src && typeof src[keyCents] === "number") return src[keyCents] / 100;
  return null;
}

// Витрина не обязана знать, какой контракт вернул сервер: старый (price в
// рублях + enabled) и новый (usd/usd_year + providers) сводятся к одному виду.
function ldNormalizePricing(data) {
  const fallbackPlans = LD_PLANS_FALLBACK.map((p) => ({ ...p }));
  let plans = fallbackPlans;
  if (data && Array.isArray(data.plans) && data.plans.length) {
    const live = data.plans
      .map((p) => {
        const usd = ldUsd(p, "usd", "usd_cents");
        const points = Number(p.points) || 0;
        if (usd === null || !p.id) return null;
        return {
          id: String(p.id),
          usd,
          usdYear: ldUsd(p, "usd_year", "usd_year_cents"),
          points,
          badge: p.badge || "",
          movies: p.movies_estimate || "",
          // Ступени объёма (ULTRA). Пустой массив = тариф без шкалы, и
          // карточка рисуется как раньше — старый контракт не ломается.
          tiers: Array.isArray(p.tiers) ? p.tiers.map((tr) => ({
            id: String(tr.id || ""),
            points: Number(tr.points) || 0,
            usd: ldUsd(tr, "usd", "usd_cents") || 0,
            usdYear: ldUsd(tr, "usd_year", "usd_year_cents") || 0,
            listUsd: ldUsd(tr, "list_usd", "list_usd_cents") || 0,
            savePct: Number(tr.save_pct) || 0,
            yearPct: Number(tr.year_discount_pct) || 0,
            volume: tr.volume || null,
          })) : [],
          volume: p.volume || null,
        };
      })
      .filter(Boolean);
    if (live.length) plans = live;
  }

  let packs = LD_PACKS_FALLBACK.map((p) => ({ ...p }));
  if (data && Array.isArray(data.packs) && data.packs.length) {
    const live = data.packs
      .map((p) => {
        const usd = ldUsd(p, "usd", "usd_cents");
        const points = Number(p.points) || 0;
        if (usd === null || !points) return null;
        return { id: String(p.id || `p${points}`), points, usd, badge: p.badge || "" };
      })
      .filter(Boolean);
    if (live.length) packs = live;
  }

  // providers === null значит «неизвестно» (гость без сессии): в этом случае
  // кнопки не выключаем — врать «оплата не работает» так же плохо, как молчать.
  let providers = null;
  if (data && data.providers && typeof data.providers === "object") {
    providers = { stripe: Boolean(data.providers.stripe), yookassa: Boolean(data.providers.yookassa) };
  } else if (data && typeof data.enabled === "boolean") {
    providers = { stripe: false, yookassa: data.enabled };
  }

  let current = null;
  if (data && data.current) {
    current = typeof data.current === "string"
      ? { plan: data.current, period: data.current_period || "month",
          tier: data.current_tier || "", nextTier: data.next_tier || "" }
      : { plan: data.current.plan || "", period: data.current.period || "month",
          tier: data.current.tier || "", nextTier: "" };
  }
  // Цена сцены приезжает живой — иначе на витрине появляется третья копия
  // прайса, и она протухает молча (см. историю LD_SCENE_COST).
  const costs = (data && data.costs && data.costs.scene) || null;
  if (costs) {
    if (costs.grok) LD_SCENE_COST.grok = Number(costs.grok);
    if (costs["seedance-2-mini"]) LD_SCENE_COST.seedance = Number(costs["seedance-2-mini"]);
    if (costs["seedance-2-5"]) LD_SCENE_COST.top = Number(costs["seedance-2-5"]);
  }
  return { plans, packs, providers, current };
}

function ldClips(points, engine) {
  const cost = LD_SCENE_COST[engine] || LD_SCENE_COST.grok;
  return Math.max(1, Math.floor(Number(points || 0) / (cost * LD_SCENES_PER_CLIP)));
}

// Формы множественного числа лежат в словаре ([1, 2–4, 5+]); правило выбора —
// в tPlural (i18n.js), поэтому язык без склонений не требует правки кода.
function ldClipsVars(points, engine) {
  const n = ldClips(points, engine);
  return { clips: tNum(n), word: tPlural(n, LT("pricing.clipWord")), n };
}

function ldClipsLine(points, engine) {
  const vars = ldClipsVars(points, engine);
  return vars.n === 1 ? LTX("pricing.clipsLineOne") : LTX("pricing.clipsLine", vars);
}

// Доллар остаётся префиксом на любом языке (валюта одна и та же), а вот
// разряды разделяются по-местному: $2,870 в английском, $2 870 в русском.
function ldMoney(usd) {
  const n = Number(usd) || 0;
  const digits = Number.isInteger(n) ? 0 : 2;
  try {
    return "$" + new Intl.NumberFormat(tLocale(), {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(n);
  } catch (e) {
    return "$" + (digits ? n.toFixed(2) : n);
  }
}

function ldYearMonthly(plan) {
  const total = plan.usdYear || Math.round(plan.usd * 12 * 0.8);
  return { total, mo: Math.round(total / 12) };
}

// ────────── ULTRA: шкала объёма внутри карточки тарифа ──────────
// Выбранная ступень живёт в состоянии страницы, а не в карточке: карточки
// перерисовываются на каждом чихе (смена языка, тумблер года, ответ кассы),
// и выбор человека переживать это обязан.
let ldTierIndex = {};        // {plan_id: индекс ступени}

function ldTierOf(plan) {
  const tiers = plan.tiers || [];
  if (!tiers.length) return null;
  let i = ldTierIndex[plan.id];
  if (i == null) {
    // По умолчанию — уже КУПЛЕННАЯ ступень, иначе первая: человек с ULTRA u3
    // не должен видеть в своей карточке чужой объём.
    const cur = ldPricing && ldPricing.current && ldPricing.current.plan === plan.id
      ? ldPricing.current.tier : "";
    const found = tiers.findIndex((tr) => tr.id === cur);
    i = found >= 0 ? found : 0;
    ldTierIndex[plan.id] = i;
  }
  return tiers[Math.min(Math.max(i, 0), tiers.length - 1)];
}

// Короткая подпись объёма для тика шкалы: 10.5k, 26k, 104k.
function ldPointsLabel(points) {
  const n = Number(points) || 0;
  if (n < 1000) return String(n);
  const sep = LT("topup.decimalSep") || ".";
  return (Math.round(n / 100) / 10).toString().replace(".", sep) + "k";
}

// Расшифровка объёма в человеческих единицах. ГЛАВНОЕ ПРАВИЛО: если клипов
// выходит меньше одного — пишем СЦЕНЫ, а не «0 клипов». На дорогом движке
// объём часто не дотягивает до целого клипа (3400 очков PRO MAX = 20 сцен
// на Seedance 2.5, две трети песни), и «0 клипов» убивает карточку, а врать
// про единицу нельзя.
function ldVolumeLine(row) {
  const T = LT("pricing");
  if (row.clips >= 1) {
    return tFill(T.volClips, {
      n: tNum(row.clips), word: tPlural(row.clips, T.clipWord), engine: row.title,
    });
  }
  return tFill(T.volScenes, { n: tNum(row.scenes), engine: row.title });
}

// Строка-заголовок карточки берётся ИЗ ТОЙ ЖЕ расшифровки, что и список под
// ней. Раньше она считалась отдельно, по константе LD_SCENE_COST, и на PRO MAX
// выходило «≈ 1 клип» при честных 20 сценах, а на ULTRA u4 — «22 клипа» при
// 20: одна карточка спорила сама с собой. Двух источников правды тут быть
// не должно.
function ldHeadlineRow(volume, engineKey) {
  const rows = (volume && volume.engines) || [];
  if (!rows.length) return null;
  if (engineKey === "top") return rows[0];                       // флагман тарифа
  if (engineKey === "grok") return rows.find((r) => r.engine === "grok") || rows[rows.length - 1];
  // «seedance» в словаре — рабочая лошадка, самая дешёвая модель семейства.
  const fam = rows.filter((r) => String(r.engine).startsWith("seedance"));
  return fam.length ? fam[fam.length - 1] : rows[rows.length - 1];
}

function ldVolumeBlock(volume) {
  if (!volume || !Array.isArray(volume.engines) || !volume.engines.length) return "";
  const T = LT("pricing");
  const rows = volume.engines;
  // Три строки в лицо — потолок, середина, самый дешёвый платный, — а полная
  // таблица под раскрытием: восемь движков в карточке никто не читает.
  const pick = [rows[0], rows[Math.floor(rows.length / 2)], rows[rows.length - 2] || rows[rows.length - 1]]
    .filter((r, i, a) => r && a.indexOf(r) === i);
  const head = pick.map((r) => `<li>${escHtml(ldVolumeLine(r))}</li>`).join("");
  const table = rows.map((r) => `<tr><td>${escHtml(r.title)}</td>`
    + `<td>${escHtml(tFill(T.volCost, { n: tNum(r.scene_cost) }))}</td>`
    + `<td>${escHtml(r.clips >= 1 ? tFill(T.volClipsShort, { n: tNum(r.clips) })
                                  : tFill(T.volScenesShort, { n: tNum(r.scenes) }))}</td></tr>`).join("");
  return `<ul class="ld-vol">${head}
      <li>${escHtml(tFill(T.volImages, { n: tNum(volume.images) }))}</li>
    </ul>
    <details class="ld-vol-all"><summary>${escHtml(T.volAll)}</summary>
      <div class="tw"><table>${table}</table></div>
    </details>`;
}

function ldPlanCard(plan) {
  const T = LT("pricing");
  const copy = (T.plans || {})[plan.id] || {};
  const paid = plan.usd > 0;
  const tier = ldTierOf(plan);
  // Ступень подменяет цену и объём тарифа целиком: дальше по коду разницы
  // между «тариф» и «тариф на ступени» уже нет.
  const usd = tier ? tier.usd : plan.usd;
  const usdYear = tier ? tier.usdYear : plan.usdYear;
  const points = tier ? tier.points : plan.points;
  const year = paid ? ldYearMonthly({ usd, usdYear }) : null;
  const yearMode = paid && ldPeriod === "year";
  const price = paid ? ldMoney(yearMode ? year.mo : usd) : T.free;
  const per = paid ? T.perMonth : T.forever;
  const hint = !paid ? "&nbsp;"
    : yearMode ? escHtml(tFill(T.yearNote, { total: ldMoney(year.total) }))
               : escHtml(tFill(T.yearHint, { mo: ldMoney(year.mo) }));
  const badge = copy.badge || plan.badge || "";
  const engine = copy.engine || "grok";
  const isCur = Boolean(me && me.authed && ldPricing && ldPricing.current
                        && ldPricing.current.plan === plan.id
                        && (!tier || ldPricing.current.tier === tier.id
                            || (!ldPricing.current.tier && tier.id === (plan.tiers[0] || {}).id)));
  const feats = (copy.features || []).map((f) => `<li>${escHtml(f)}</li>`).join("");

  // Шкала: те же тики, что у докупки очков, плюс ползунок на широком экране.
  let scale = "";
  let saveBadge = "";
  let volume = "";
  if (tier) {
    const idx = plan.tiers.indexOf(tier);
    scale = `<div class="ld-plan-scale">
      <input class="ld-range ld-tier-range" type="range" min="0" max="${plan.tiers.length - 1}"
             step="1" value="${idx}" data-plan="${escHtml(plan.id)}"
             aria-label="${escHtml(T.tierAria)}"
             aria-valuetext="${escHtml(tFill(T.pointsLine, { points: tNum(points) }))}" />
      <div class="ld-ticks ld-tier-ticks">${plan.tiers.map((tr, i) => `
        <button type="button" class="${i === idx ? "on" : ""}" data-plan="${escHtml(plan.id)}" data-idx="${i}">
          <span>${escHtml(ldPointsLabel(tr.points))}</span>
        </button>`).join("")}</div>
    </div>`;
    // Зачёркнутая цена — честная: тот же объём по цене очка базовой ступени.
    // Проценты приходят с сервера посчитанными, включая годовой (после пола
    // цены очка он уже не −20 %, и рисовать −20 % там нельзя).
    // Зачёркнутое — всегда ЧЕСТНАЯ база сравнения, не выдуманный якорь:
    //   в месячном режиме — тот же объём по цене очка базовой ступени;
    //   в годовом — месячная цена ЭТОЙ ЖЕ ступени, ведь рядом стоит «в месяц
    //   при оплате за год», и сравнивать надо именно эти две цифры.
    const pct = yearMode ? tier.yearPct : tier.savePct;
    if (pct > 0) {
      const was = yearMode ? tier.usd : tier.listUsd;
      saveBadge = `<div class="ld-plan-was"><s>${escHtml(ldMoney(was))}</s>`
        + `<span class="ld-save-badge">${escHtml(tFill(
            yearMode ? T.saveYear : T.saveVolume, { pct }))}</span></div>`;
    }
    volume = ldVolumeBlock(tier.volume);
  } else if (plan.volume && paid) {
    volume = ldVolumeBlock(plan.volume);
  }

  const headRow = ldHeadlineRow(tier ? tier.volume : plan.volume, engine);
  const clips = headRow ? ldVolumeLine(headRow) : ldClipsLine(points, engine);

  let action;
  if (isCur) {
    action = `<div class="ld-plan-cur">${escHtml(T.current)}</div>`;
  } else if (!paid) {
    action = `<button type="button" class="ld-plan-start">${escHtml(T.ctaFree)}</button>`;
  } else {
    action = `<button type="button" class="primary ld-plan-pay" data-plan="${escHtml(plan.id)}"
      data-tier="${escHtml(tier ? tier.id : "")}"
      >${escHtml(tFill(T.cta, { plan: copy.title || plan.id.toUpperCase() }))}</button>`;
  }

  // Рамкой выделяем РОВНО один тариф — рекомендованный (hi в словаре).
  // Бейдж может быть и у других, но второй жирной рамки на экране не будет.
  return `<article class="ld-plan${copy.hi ? " ld-plan-hi" : ""}${tier ? " ld-plan-ultra" : ""}">
    ${badge ? `<span class="ld-plan-badge">${escHtml(badge)}</span>` : ""}
    <span class="ld-plan-name">${escHtml(copy.title || plan.id.toUpperCase())}</span>
    ${scale}
    ${saveBadge}
    <div class="ld-plan-price">${escHtml(price)}<span> ${escHtml(per)}</span></div>
    <div class="ld-plan-year muted">${hint}</div>
    <span class="ld-plan-clips">${escHtml(clips)}</span>
    <p class="ld-plan-note">${escHtml(tFill(T.pointsLine, { points: tNum(points) }))}${
      copy.note ? " · " + escHtml(copy.note) : ""}</p>
    ${volume}
    <ul class="ld-plan-feats">${feats}</ul>
    ${action}
  </article>`;
}

function ldRenderPlans() {
  const box = $("#ld-plans");
  if (!box || !ldPricing) return;
  const T = LT("pricing");
  box.innerHTML = ldPricing.plans.map(ldPlanCard).join("");

  const off = Boolean(ldPricing.providers
    && !ldPricing.providers.stripe && !ldPricing.providers.yookassa);
  const note = $("#ld-pay-note");
  if (note) {
    note.textContent = off ? T.payOffNote : "";
    note.classList.remove("error");
  }
  $$(".ld-plan-pay", box).forEach((btn) => {
    if (off) {
      btn.disabled = true;
      btn.textContent = T.payOff;
      return;
    }
    btn.addEventListener("click", () =>
      ldCheckout("plan", btn.dataset.plan, btn, btn.dataset.tier || ""));
  });
  $$(".ld-plan-start", box).forEach((btn) => btn.addEventListener("click", ldStart));

  // Шкала объёма: тики и ползунок двигают одну и ту же цифру.
  const pick = (planId, idx) => {
    const plan = ldPricing.plans.find((p) => p.id === planId);
    if (!plan || !plan.tiers.length) return;
    ldTierIndex[planId] = Math.min(Math.max(Number(idx) || 0, 0), plan.tiers.length - 1);
    ldRenderPlans();
  };
  $$(".ld-tier-ticks button", box).forEach((b) =>
    b.addEventListener("click", () => pick(b.dataset.plan, b.dataset.idx)));
  $$(".ld-tier-range", box).forEach((r) => {
    const paint = () => {
      const max = Number(r.max) || 1;
      const pct = max ? (Number(r.value) / max) * 100 : 0;
      r.style.background =
        `linear-gradient(90deg, var(--accent-2) 0 ${pct}%, var(--surface-2) ${pct}% 100%)`;
    };
    paint();
    // input — на каждое движение (мгновенная подсветка), change — уже перерисовка:
    // пересобирать всю секцию на каждый пиксель ползунка незачем.
    r.addEventListener("input", paint);
    r.addEventListener("change", () => pick(r.dataset.plan, r.value));
  });

  // Приписка мелким с точным расчётом — под всей витриной, один раз.
  const fine = $("#ld-price-fine");
  if (fine) fine.textContent = ldFinePrint();

  // Годовой тумблер держим в актуальном состоянии вместе с карточками.
  $$("#ld-period button").forEach((b) => b.classList.toggle("on", b.dataset.period === ldPeriod));
}

// Приписка мелким: точная цена сцены по каждому движку верхнего тарифа.
// Это то место, где витрина обязана сойтись с кассой до очка — считается из
// живого ответа сервера, а не из констант фронта.
function ldFinePrint() {
  const T = LT("pricing");
  const top = (ldPricing && ldPricing.plans || []).find((p) => (p.tiers || []).length)
    || (ldPricing && ldPricing.plans || [])[3];
  const vol = top && ((ldTierOf(top) || {}).volume || top.volume);
  if (!vol || !vol.engines) return "";
  const list = vol.engines.slice().reverse()
    .map((r) => `${r.title} ${tNum(r.scene_cost)}`).join(" · ");
  return tFill(T.finePrint, {
    list,
    pair: tNum(vol.frames_pair_cost),
    scenes: tNum(vol.clip_scenes),
  });
}

// ────────── шкала докупки очков ──────────
function ldPacks() {
  return (ldPricing && ldPricing.packs && ldPricing.packs.length)
    ? ldPricing.packs : LD_PACKS_FALLBACK;
}

function ldSavePct(pack, packs) {
  const base = packs[0];
  if (!base || !base.points || !pack.points) return 0;
  const basePer = base.usd / base.points;
  if (!basePer) return 0;
  return Math.max(0, Math.round(100 - 100 * (pack.usd / pack.points) / basePer));
}

function ldPackLabel(points) {
  if (points < 1000) return String(points);
  const sep = LT("topup.decimalSep") || ".";
  return (points / 1000).toString().replace(".", sep) + "k";
}

function ldRenderTopup() {
  const packs = ldPacks();
  const ticks = $("#ld-ticks");
  const readout = $("#ld-readout");
  const range = $("#ld-range");
  if (!ticks || !readout || !range) return;
  const T = LT("topup");
  const i = Math.min(Math.max(ldPackIndex, 0), packs.length - 1);
  ldPackIndex = i;
  const pack = packs[i];

  ticks.innerHTML = packs.map((p, idx) => `
    <button type="button" class="${idx === i ? "on" : ""}" data-idx="${idx}">
      <span>${escHtml(ldPackLabel(p.points))}</span>
      <span class="ld-tick-sub">${escHtml(ldMoney(p.usd))}</span>
    </button>`).join("");
  $$("button", ticks).forEach((b) => b.addEventListener("click", () => {
    ldPackIndex = Number(b.dataset.idx) || 0;
    ldRenderTopup();
  }));

  range.max = String(packs.length - 1);
  range.value = String(i);
  range.setAttribute("aria-valuetext",
    tFill(T.pointsUnit, { points: tNum(pack.points) }) + " — " + ldMoney(pack.usd));
  const pct = packs.length > 1 ? (i / (packs.length - 1)) * 100 : 0;
  range.style.background =
    `linear-gradient(90deg, var(--accent-2) 0 ${pct}%, var(--surface-2) ${pct}% 100%)`;

  const save = ldSavePct(pack, packs);
  readout.innerHTML = `
    <div class="ld-readout-price">${escHtml(ldMoney(pack.usd))}<span> ${escHtml(T.priceUnit)}</span></div>
    <div class="ld-readout-points">${escHtml(tFill(T.pointsUnit, { points: tNum(pack.points) }))}</div>
    ${save > 0 ? `<span class="ld-save-badge">${escHtml(tFill(T.save, { pct: save }))}</span>` : ""}
    <ul>
      <li>${escHtml(tFill(T.clipsTop, ldClipsVars(pack.points, "top")))}</li>
      <li class="muted">${escHtml(tFill(T.clipsGrok, ldClipsVars(pack.points, "grok")))}</li>
    </ul>
    <button type="button" class="primary ld-pack-buy" data-pack="${escHtml(pack.id)}"
      >${escHtml(tFill(T.cta, { points: tNum(pack.points) }))}</button>`;

  const off = Boolean(ldPricing && ldPricing.providers
    && !ldPricing.providers.stripe && !ldPricing.providers.yookassa);
  const buy = $(".ld-pack-buy", readout);
  if (buy) {
    if (off) {
      buy.disabled = true;
      buy.textContent = LTX("pricing.payOff");
    } else {
      buy.addEventListener("click", () => ldCheckout("topup", pack.id, buy));
    }
  }
}


// ══════════════════════ РАЗДЕЛ «ПРОМТЫ»: каталог витрины ══════════════════════
// Каталог живёт на сервере целиком (backend/prompts_catalog.py). Здесь только
// отрисовка публичной части карточки: название, «что получишь», музыка, миксы,
// счётчик применений. Текста промпта в этих данных нет — и появиться он тут
// не может, сервер отдаёт по белому списку полей.

let ldPromptTab = "viral";      // выбранная вкладка: подборка или группа

function ldPromptTabs(cat) {
  // Первой идёт подборка «Вирусные форматы»: человек приходит не за
  // «плёнкой», а за «чтобы залетело». Группы («как выглядит кадр») — следом.
  const cols = (cat.collections || []).map((c) => ({ id: `c:${c.key}`, label: c.label }));
  const groups = (cat.groups || []).map((g) => ({ id: `g:${g.key}`, label: g.label }));
  return [{ id: "presets", label: LTX("prompts.tabPresets") }].concat(cols, groups);
}

function ldPromptItems(cat, tab) {
  if (tab === "presets") return { kind: "preset", items: cat.presets || [] };
  const [type, key] = String(tab).split(":");
  if (type === "c") {
    const col = (cat.collections || []).find((c) => c.key === key);
    const keys = col ? col.styles : [];
    return { kind: "style", items: (cat.styles || []).filter((s) => keys.includes(s.key)) };
  }
  return { kind: "style", items: (cat.styles || []).filter((s) => s.group === key) };
}

function ldStyleCard(s) {
  const T = LT("prompts");
  const m = s.media || {};
  const tags = (s.tags || []).slice(0, 3).map((x) => `<span>${escHtml(x)}</span>`).join("");
  // «Закрытый» — про ров, «замок» — про деньги. Это РАЗНЫЕ вещи, и путать их
  // на карточке нельзя: dreamclad закрыт по тексту, но снимать им можно и
  // на бесплатном тарифе.
  const marks = [
    s.prompt_class === "closed" ? `<span class="ld-card-mark" title="${escHtml(T.closedHint)}">${escHtml(T.closed)}</span>` : "",
    s.locked ? `<span class="ld-card-mark ld-card-lock">${escHtml(T.pro)}</span>` : "",
  ].join("");
  return `<article class="ld-card" data-style="${escHtml(s.key)}">
    <div class="ld-card-media">
      <img src="${escHtml(m.poster || "/img/shots/step-frames.jpg")}" alt="" loading="lazy"
           width="360" height="640" onerror="this.style.visibility='hidden'" />
      ${marks}
    </div>
    <h3>${escHtml(s.label || s.key)}</h3>
    <p class="ld-card-gain">${escHtml(s.gain || s.desc || "")}</p>
    <p class="ld-card-meta muted">${escHtml((s.music && s.music.text) || "")}</p>
    <div class="ld-card-tags">${tags}</div>
    <div class="ld-card-foot">
      <span class="muted">${escHtml(s.uses ? tFill(T.uses, { n: tNum(s.uses) }) : T.usesNone)}</span>
      ${s.locked
        // Закрытый тарифом стиль НЕ прикидывается доступным: сервер такой ключ
        // молча выбросит, и кнопка «взять» на нём означала бы тихий отказ —
        // человек нажал, ничего не произошло, объяснения нет.
        ? `<button type="button" class="ld-card-plans">${escHtml(T.useLocked)}</button>`
        : `<button type="button" class="ld-card-use" data-style="${escHtml(s.key)}">${escHtml(T.use)}</button>`}
    </div>
  </article>`;
}

function ldPresetCard(p) {
  const T = LT("prompts");
  const beats = (p.beats || []).map((b) => `<li>${escHtml(b.text)}</li>`).join("");
  const kind = p.no_story ? T.kindPunch : T.kindStory;
  return `<article class="ld-card ld-card-preset" data-preset="${escHtml(p.key)}">
    <span class="ld-card-mark">${escHtml(kind)}</span>
    <h3>${escHtml(p.label || p.key)}</h3>
    <p class="ld-card-gain">${escHtml(p.logline || "")}</p>
    <details class="ld-vol-all"><summary>${escHtml(T.beats)}</summary><ol>${beats}</ol></details>
    ${p.research ? `<p class="ld-card-meta muted">${escHtml(p.research)}</p>` : ""}
    <div class="ld-card-foot">
      <span class="muted">${escHtml(tFill(T.scenes, { n: tNum((p.scenes || {}).typ || 30) }))}</span>
      <button type="button" class="ld-card-use" data-preset="${escHtml(p.key)}">${escHtml(T.use)}</button>
    </div>
  </article>`;
}

async function ldRenderPrompts() {
  const tabsBox = $("#ld-prompt-tabs");
  const cardsBox = $("#ld-prompt-cards");
  if (!tabsBox || !cardsBox) return;
  const cat = await loadStyles();
  if (cat.failed) {
    cardsBox.innerHTML = `<p class="muted">${escHtml(LTX("prompts.failed"))}</p>`;
    return;
  }
  const tabs = ldPromptTabs(cat);
  if (!tabs.some((x) => x.id === ldPromptTab)) ldPromptTab = tabs[1] ? tabs[1].id : tabs[0].id;

  tabsBox.innerHTML = tabs.map((x) => `
    <button type="button" role="tab" class="${x.id === ldPromptTab ? "on" : ""}"
            data-tab="${escHtml(x.id)}" aria-selected="${x.id === ldPromptTab}">${escHtml(x.label)}</button>`).join("");
  $$("button", tabsBox).forEach((b) => b.addEventListener("click", () => {
    ldPromptTab = b.dataset.tab;
    ldRenderPrompts();
  }));

  const { kind, items } = ldPromptItems(cat, ldPromptTab);
  cardsBox.innerHTML = items.map(kind === "style" ? ldStyleCard : ldPresetCard).join("")
    || `<p class="muted">${escHtml(LTX("prompts.empty"))}</p>`;
  // «Взять» ведёт в студию с преднастройкой: раздел обязан заканчиваться
  // действием, а не восхищением.
  $$(".ld-card-use", cardsBox).forEach((b) => b.addEventListener("click", async () => {
    ldPending = b.dataset.style ? { style: b.dataset.style } : { preset: b.dataset.preset };
    await ldStart();
  }));
  $$(".ld-card-plans", cardsBox).forEach((b) => b.addEventListener("click", () => {
    const el = $("#ld-pricing");
    if (el) el.scrollIntoView({ behavior: "smooth" });
  }));
}

// Что применить к треку сразу после входа в студию (клик «взять» на карточке).
let ldPending = null;

// ══════════════════════ РАЗДЕЛ «ШКОЛА»: уроки ══════════════════════
// Тексты — файлы docs/learn, отданные через /api/learn в маркдауне. Разметку
// рисует ldMd(): свой маленький рендерер вместо библиотеки — уроки используют
// ровно h1–h3, абзацы, списки, таблицы и жирный, а 40 КБ парсера в бандл
// ради этого не лезут.

function ldMdInline(text) {
  const code = [];
  // Код прячем ПЕРВЫМ и под непечатаемым сентинелом: экранирование не
  // должно трогать его содержимое, а текст урока не должен случайно
  // совпасть с меткой (« 0 » в тексте встречается, u0000 — нет).
  let out = text.replace(/`([^`]+)`/g, (m, c) => {
    code.push(`<code>${escHtml(c)}</code>`);
    return `\u0000${code.length - 1}\u0000`;
  });
  out = escHtml(out);
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (m, txt, href) => `<a href="${escHtml(href)}">${txt}</a>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  code.forEach((c, i) => { out = out.replace(`\u0000${i}\u0000`, c); });
  return out;
}

function ldMd(md) {
  const lines = String(md || "").split("\n");
  const out = [];
  let i = 0;
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) { i += 1; continue; }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${ldMdInline(h[2])}</h${h[1].length}>`); i += 1; continue; }
    if (/^(---|\*\*\*|___)$/.test(line)) { out.push("<hr />"); i += 1; continue; }
    if (line.startsWith("|") && /^\|[\s:|-]+\|$/.test((lines[i + 1] || "").trim())) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(cells(lines[i])); i += 1; }
      out.push(`<div class="tw"><table><thead><tr>${head.map((c) => `<th>${ldMdInline(c)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${ldMdInline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(ldMdInline(lines[i].trim().replace(/^[-*]\s+/, ""))); i += 1;
      }
      out.push(`<ul>${items.map((x) => `<li>${x}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(ldMdInline(lines[i].trim().replace(/^\d+\.\s+/, ""))); i += 1;
      }
      out.push(`<ol>${items.map((x) => `<li>${x}</li>`).join("")}</ol>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|[-*]\s|\d+\.\s|\|)/.test(lines[i].trim())) {
      para.push(lines[i].trim()); i += 1;
    }
    out.push(`<p>${ldMdInline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

let ldLearn = null;

async function ldRenderLearn() {
  const box = $("#ld-learn-list");
  if (!box) return;
  try {
    ldLearn = await api(`/api/learn?lang=${encodeURIComponent(LANG)}`);
  } catch (e) {
    box.innerHTML = `<p class="muted">${escHtml(LTX("learn.failed"))}</p>`;
    return;
  }
  const T = LT("learn");
  const byLevel = {};
  (ldLearn.lessons || []).forEach((l) => { (byLevel[l.level] = byLevel[l.level] || []).push(l); });
  box.innerHTML = (ldLearn.levels || []).map((lv) => `
    <div class="ld-learn-level">
      <h3>${escHtml(lv.title || "")}</h3>
      <ul>${(byLevel[lv.level] || []).map((l) => `
        <li class="${l.locked ? "locked" : ""}">
          <button type="button" data-slug="${escHtml(l.slug)}">
            <span class="ld-learn-t">${escHtml(l.title)}${l.done ? " ✓" : ""}</span>
            <span class="ld-learn-m muted">${escHtml(tFill(T.minutes, { n: l.minutes }))}${
              l.locked ? " · " + escHtml(T.locked) : ""}</span>
          </button>
          <a class="ld-learn-perma" href="${escHtml(l.url)}" title="${escHtml(T.openPage)}">↗</a>
        </li>`).join("")}</ul>
    </div>`).join("");
  $$(".ld-learn-level button", box).forEach((b) =>
    b.addEventListener("click", () => ldOpenLesson(b.dataset.slug)));
}

async function ldOpenLesson(slug) {
  const T = LT("learn");
  openModal(T.loading, async (body) => {
    body.innerHTML = `<p class="muted">${escHtml(T.loading)}</p>`;
    let data;
    try {
      data = await api(`/api/learn/${encodeURIComponent(slug)}?lang=${encodeURIComponent(LANG)}`);
    } catch (e) {
      body.innerHTML = `<p class="error">${escHtml(errText(e))}</p>`;
      return;
    }
    $("#modal-title").textContent = data.title || slug;
    // Закрытый урок показывает НАЧАЛО и честную причину замка, а не пустой
    // экран: человек должен видеть, что именно он не читает.
    const gate = data.full ? "" : `<div class="ld-learn-gate">
      <p>${escHtml(tFill(T.gate, { plan: (data.access || "pro").toUpperCase() }))}</p>
      <button type="button" class="primary ld-learn-plans">${escHtml(T.gateCta)}</button>
    </div>`;
    body.innerHTML = `<article class="ld-lesson">${ldMd(data.markdown || "")}</article>${gate}
      ${data.full ? `<div class="row"><button type="button" class="ld-learn-done">${
        escHtml(data.done ? T.undone : T.markDone)}</button>
        <a class="muted" href="${escHtml(data.url)}">${escHtml(T.openPage)}</a></div>` : ""}`;
    const plansBtn = $(".ld-learn-plans", body);
    if (plansBtn) plansBtn.addEventListener("click", () => {
      closeModal();
      const el = $("#ld-pricing");
      if (el) el.scrollIntoView({ behavior: "smooth" });
    });
    const doneBtn = $(".ld-learn-done", body);
    if (doneBtn) doneBtn.addEventListener("click", async () => {
      doneBtn.disabled = true;
      try {
        await api(`/api/learn/${encodeURIComponent(slug)}/done`,
                  { method: "POST", body: { done: !data.done } });
        data.done = !data.done;
        doneBtn.textContent = data.done ? T.undone : T.markDone;
        await ldRenderLearn();
      } catch (e) { fail(e); }
      doneBtn.disabled = false;
    });
  });
}


// ═════════════ ОНБОРДИНГ FREE: чеклист «первый клип» ═════════════
// Требование простое: на бесплатном тарифе должна быть инструкция. Простыня
// текста его не выполняет — инструкцию читают, когда УЖЕ застряли. Поэтому
// здесь не текст, а доведение до первого собранного клипа: четыре пункта,
// подсвеченный следующий шаг и цифра списания ДО нажатия.
//
// Состояние считается по ДАННЫМ (есть ли аудио, стиль, сцены, клип), а не по
// галочкам: галочку можно поставить и уйти, а трек либо загружен, либо нет.

let onboarding = null;

async function renderOnboarding() {
  const box = $("#onboarding");
  if (!box || !me || !me.authed) return;
  // Чеклист написан про клип целиком («загрузи трек», «кадры → оживление →
  // сборка») и в открытом сериале читается как инструкция не к этому экрану.
  // Плитка привязана к аккаунту, а не к проекту, поэтому не гасим её
  // насовсем — просто не показываем в тех режимах, про которые она не.
  if (curMode().id !== "clip") {
    box.classList.add("hidden");
    return;
  }
  try {
    onboarding = await api("/api/onboarding");
  } catch (e) {
    box.classList.add("hidden");
    return;
  }
  const marks = onboarding.marks || [];
  // Плитка исчезает после первого клипа или если её закрыли руками.
  if (onboarding.done || marks.includes("hide")) {
    if (onboarding.done && !marks.includes("winseen")) { renderOnboardingWin(box); return; }
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");

  const steps = onboarding.steps || [];
  const doneCount = steps.filter((s) => s.done).length;
  const nextIdx = steps.findIndex((s) => !s.done);
  const c = onboarding.costs || {};
  // Прогноз честный: текстовые шаги идут по нашей подписке и стоят НОЛЬ —
  // об этом надо говорить вслух, иначе человек боится нажать.
  const costOf = {
    track: t("onboarding.costFree"),
    style: t("onboarding.costFree"),
    scenes: t("onboarding.costFree"),
    clip: t("onboarding.costClip", { n: tNum(c.clip_total || 0) }),
  };

  box.innerHTML = `
    <div class="ob-head">
      <h2>${escHtml(t("onboarding.title"))}</h2>
      <span class="ob-count">${escHtml(t("onboarding.count", { done: doneCount, all: steps.length }))}</span>
      <button type="button" class="ghost ob-hide" title="${escHtml(t("onboarding.hide"))}">✕</button>
    </div>
    <div class="ob-bar"><i style="width:${Math.round((doneCount / steps.length) * 100)}%"></i></div>
    <ol class="ob-steps">
      ${steps.map((s, i) => `
        <li class="${s.done ? "done" : i === nextIdx ? "now" : ""}">
          <span class="ob-mark">${s.done ? "✓" : i + 1}</span>
          <span class="ob-text">
            <b>${escHtml(t(`onboarding.steps.${s.id}.title`))}</b>
            <em>${escHtml(t(`onboarding.steps.${s.id}.hint`))}</em>
          </span>
          ${i === nextIdx ? `<span class="ob-cost">${escHtml(costOf[s.id] || "")}</span>` : ""}
        </li>`).join("")}
    </ol>
    <p class="ob-note">${escHtml(onboarding.enough
      ? t("onboarding.enough", { n: tNum(onboarding.points) })
      : t("onboarding.short", { n: tNum(onboarding.points),
                                need: tNum(c.clip_total || 0) }))}</p>
    <div class="row ob-actions">
      <button type="button" class="ob-guide">${escHtml(t("onboarding.guide"))}</button>
      <button type="button" class="ob-lesson">${escHtml(t("onboarding.lesson"))}</button>
    </div>`;

  $(".ob-hide", box).addEventListener("click", async () => {
    await api("/api/onboarding", { method: "POST", body: { mark: "hide", on: true } });
    box.classList.add("hidden");
  });
  $(".ob-guide", box).addEventListener("click", ldOpenGuide);
  // «Как это работает» ведёт в первый урок школы, а не в отдельную простыню:
  // текст один и тот же, и держать его в двух местах нельзя.
  $(".ob-lesson", box).addEventListener("click", () => ldOpenLesson("first-clip"));
}

// Момент победы: клип собран. Апселл ровно здесь и с ТОЧНОЙ цифрой остатка —
// человек только что увидел результат, и это единственная секунда, когда
// разговор про следующий движок уместен.
function renderOnboardingWin(box) {
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="ob-head"><h2>${escHtml(t("onboarding.winTitle"))}</h2>
      <button type="button" class="ghost ob-hide" title="${escHtml(t("onboarding.hide"))}">✕</button></div>
    <p class="ob-note">${escHtml(t("onboarding.winText", { n: tNum(onboarding.points) }))}</p>
    <div class="row ob-actions">
      <button type="button" class="primary ob-plans">${escHtml(t("onboarding.winCta"))}</button>
    </div>`;
  $(".ob-hide", box).addEventListener("click", async () => {
    await api("/api/onboarding", { method: "POST", body: { mark: "winseen", on: true } });
    box.classList.add("hidden");
  });
  $(".ob-plans", box).addEventListener("click", async () => {
    await api("/api/onboarding", { method: "POST", body: { mark: "winseen", on: true } });
    openAccountModal("plan");
  });
}

// ────────── оплата ──────────
// Гость платит тем же кликом, которым заводит аккаунт: без аккаунта платёж
// не к чему привязать, а форму регистрации мы принципиально не показываем.
async function ldEnsureAccount() {
  if (me && me.authed) return;
  await api("/api/start" + (refCode ? `?ref=${encodeURIComponent(refCode)}` : ""), { method: "POST" });
  me = await api("/api/me");
  ldRenderAuth();
}

async function ldCheckout(kind, id, btn, tier) {
  const note = $("#ld-pay-note");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = kind === "plan" ? LTX("pricing.creating") : LTX("topup.creating");
  if (note) { note.textContent = ""; note.classList.remove("error"); }
  try {
    await ldEnsureAccount();
    const body = kind === "plan"
      // tier — выбранная ступень объёма. Сервер всё равно проверяет её сам:
      // неизвестную и скрытую флагом он молча понижает до базовой.
      ? { kind: "plan", plan: id, tier: tier || "", period: ldPeriod, promo: refCode || "" }
      : { kind: "topup", pack: id, promo: refCode || "" };
    const r = await api("/api/billing/create", { method: "POST", body });
    if (r && r.url) { window.location.href = r.url; return; }
    throw new Error(LTX("pricing.noUrl"));
  } catch (e) {
    btn.disabled = false;
    btn.textContent = label;
    if (note) {
      note.textContent = errText(e) || LTX("pricing.noUrl");
      note.classList.add("error");
      note.scrollIntoView({ block: "nearest" });
    }
  }
}

async function ldOpenPartner() {
  const btn = $("#ld-partner-btn");
  if (btn) btn.disabled = true;
  try {
    await ldEnsureAccount();
    showApp();
    openAccountModal("ref");
  } catch (e) {
    if (btn) btn.textContent = errText(e) || LTX("partner.cta");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ────────── состояние «гость / вернувшийся» ──────────
function ldRenderAuth() {
  const authed = Boolean(me && me.authed);
  const T = tRaw("landing");
  const start = $("#welcome-start");
  const login = $("#welcome-login");
  const navStart = $("#ld-nav-start");
  const navLogin = $("#ld-nav-login");
  const trust = $(".ld-hero-trust");
  if (start) start.textContent = authed ? T.hero.ctaOpen : T.hero.ctaStart;
  if (login) login.classList.toggle("hidden", authed);
  if (navStart) navStart.textContent = authed ? T.nav.open : T.nav.start;
  if (navLogin) navLogin.classList.toggle("hidden", authed);
  if (trust) trust.textContent = authed ? T.hero.trustBack : T.hero.trust;
  const auth = $("#ld-auth");
  if (auth) {
    auth.classList.toggle("hidden", authed);
    if (!authed) renderAuthButtons(auth, { mode: "login" });
  }
}

// ────────── сборка страницы ──────────
async function ldLoadPricing() {
  // Отдельный запрос мимо api(): гостю /api/billing/plans отвечает 401, а
  // api() на 401 уводит на форму входа — лендинг от этого рассыпался бы.
  let data = null;
  try {
    const r = await fetch("/api/billing/plans", { headers: { accept: "application/json" } });
    if (r.ok) data = await r.json();
  } catch (e) { data = null; }
  ldPricing = ldNormalizePricing(data);
  if (ldPricing.current && ldPricing.current.period === "year") ldPeriod = "year";
  ldRenderPlans();
  ldRenderTopup();
}

// Все тексты страницы одним проходом: и при первом показе, и при смене языка.
function ldRenderText() {
  if (!$("#welcome")) return;
  applyI18n($("#welcome"));
  const yearBtn = $('#ld-period button[data-period="year"]');
  if (yearBtn) {
    yearBtn.innerHTML = `${escHtml(LTX("pricing.year"))}`
      + `<span class="ld-save">${escHtml(LTX("pricing.yearSave"))}</span>`;
  }
  ldBuildSteps();
  ldBuildFeatures();
  ldBuildFaq();
  ldBuildPartner();
  ldBuildFooter();
  ldRenderPlans();
  ldRenderTopup();
  // Каталог промтов и уроки приходят с сервера уже на нужном языке, поэтому
  // при смене языка их нужно перезапросить, а не просто перерисовать.
  loadStyles(true).then(ldRenderPrompts);
  ldRenderLearn();
  ldRenderAuth();
  renderRefBanner();
}

function renderLanding() {
  if (!$("#welcome")) return;
  if (!ldBuilt) {
    ldBuilt = true;
    // Обработчики вешаем ОДИН раз: узлы статичные, перерисовка текста их не
    // пересоздаёт — иначе на каждую смену языка копился бы новый слушатель.
    $$("#ld-period button").forEach((b) => b.addEventListener("click", () => {
      ldPeriod = b.dataset.period === "year" ? "year" : "month";
      ldRenderPlans();
    }));
    const range = $("#ld-range");
    if (range) range.addEventListener("input", () => {
      ldPackIndex = Number(range.value) || 0;
      ldRenderTopup();
    });
    const navStart = $("#ld-nav-start");
    const navLogin = $("#ld-nav-login");
    if (navStart) navStart.addEventListener("click", ldStart);
    if (navLogin) navLogin.addEventListener("click", showLogin);
    const partnerBtn = $("#ld-partner-btn");
    if (partnerBtn) partnerBtn.addEventListener("click", ldOpenPartner);

    // Первая отрисовка витрины — из запасных чисел, чтобы тарифы были видны
    // мгновенно; живой ответ сервера её тут же уточняет.
    ldPricing = ldNormalizePricing(null);
  }
  ldRenderText();
  ldLoadPricing();
}

// Вернувшийся пользователь может попасть на главную по ссылке или якорю —
// тогда студию не открываем, но первый экран зовёт «Открыть студию».
function ldWantsLanding() {
  // Внутри мини-аппа лендинга нет никогда: человек уже пришёл, продавать ему
  // оффер нечем, а «Старт» и «Войти» ведут в тупик.
  if (window.TGA && TGA.active) return false;
  try {
    if (new URLSearchParams(location.search).has("home")) return true;
    const h = location.hash || "";
    return h === "#home" || h.startsWith("#ld-");
  } catch (e) {
    return false;
  }
}

// ═════════════════════════ язык интерфейса ═════════════════════════
// Статические подписи (data-i18n*) проставляем сразу, до первого показа экрана:
// шаблоны <template> переводятся при клонировании, остальное — здесь.
applyI18n(document);
syncLangSwitches();

// Переключение EN/RU без перезагрузки: разметку обновляет applyI18n, а всё,
// что собрано кодом — карточки треков и сцен, кабинет, витрина тарифов, —
// перерисовываем сами. Модалку закрываем: её содержимое построено императивно.
onLangChange(() => {
  // applyI18n(document) уже отработал в setLang — здесь только то, что рисует код.
  closeModal();
  // Каркасы форматов (названия и логлайны) сервер локализует на своей стороне
  // — после смены языка реестр надо перезабрать, иначе «Дело недели» так и
  // останется английским до перезагрузки страницы.
  loadModes(true).then(() => {
    if (project && !$("#app").classList.contains("hidden")) render();
  });
  if (!$("#app").classList.contains("hidden")) {
    renderUserBar();                  // бейдж очков и тариф тоже подписаны словами
    if (project) render();
  }
  rebuildAddTrackPicker();
  if (!$("#welcome").classList.contains("hidden")) ldRenderText();
  if (!$("#chat").classList.contains("hidden")) chatRenderAll();
  syncLangSwitches();
});


// ═════════════════════════ ЧАТ: одно окно, переключается модель ═════════════════════════
//
// Ключевое решение, ради которого это не «ещё три вкладки»: ВЫБРАННАЯ МОДЕЛЬ И
// ЕСТЬ НАМЕРЕНИЕ. Селектор живёт внутри строки ввода; текстовая модель отвечает
// текстом, движок картинок рисует по тому же промпту, движок видео оживляет
// картинку. Никаких режимов экрана.
//
// Цена видна ДО отправки и пересчитывается на каждое движение селектора. Сервер
// остаётся авторитетом по деньгам — клиент только показывает то же число.
//
// Авто-режима «сам решу, что ты хотел» здесь нет намеренно: текст стоит 2 очка,
// картинка 8, видео до 154. Автороутер, промахнувшийся в видео, стоит человеку
// месячной нормы тарифа — цена ошибки несимметрична, поэтому решает человек.

const chatState = {
  models: [],          // плоский список позиций селектора с /api/chat/models
  meta: null,          // тарифные данные ответа: очки, ретенция, дефолты
  chats: [],
  activeId: 0,
  messages: [],
  files: [],           // залитые вложения, ещё не отправленные
  sourceId: 0,         // «Оживить»: из какого сообщения берём первый кадр
  search: "",
  hasMore: false,     // есть ли сообщения РАНЬШЕ загруженного куска
  loadingMore: false,
  poll: null,
  busy: false,
};

function chatEl(id) { return document.getElementById(id); }

// Единица очков в нужной форме: «1 очко», «2 очка», «5 очков».
function chatUnit(n) { return tPlural(n, tRaw("chat.unit")); }

// Лента всегда показывает последнее сообщение. Отдельная функция нужна из-за
// картинок: они догружаются ПОСЛЕ отрисовки и меняют высоту ленты уже после
// того, как мы её пролистали, — кнопки под кадром уезжали за нижний край.
function chatScrollBottom() {
  const feed = chatEl("chat-feed");
  if (feed) feed.scrollTop = feed.scrollHeight;
}

function showChat() {
  hideScreens();
  $("#chat").classList.remove("hidden");
  // Поллинг студии гасим: пока человек в чате, дёргать проект незачем.
  clearTimeout(pollTimer);
  if (location.hash !== "#/chat") history.replaceState(null, "", "#/chat");
  chatBoot();
}

function chatLeave() {
  clearTimeout(chatState.poll);
  if (location.hash === "#/chat") history.replaceState(null, "", location.pathname);
  showApp();
}

async function chatBoot() {
  try {
    if (!chatState.models.length) await chatLoadModels();
    await chatLoadList();
    if (!chatState.activeId && chatState.chats.length) {
      await chatOpen(chatState.chats[0].id);
    } else if (chatState.activeId) {
      await chatLoadMessages();
    } else {
      chatRenderFeed();
    }
    chatRenderAll();
  } catch (e) {
    fail(e);
  }
}

async function chatLoadModels() {
  const data = await api("/api/chat/models");
  chatState.models = data.models || [];
  chatState.meta = data;
}

function chatModel(id) {
  return chatState.models.find((m) => m.id === id) || null;
}

function chatCurrentModel() {
  const sel = chatEl("cc-model");
  return chatModel(sel && sel.value) || chatState.models[0] || null;
}

// ────────── селектор модели внутри строки ввода ──────────

function chatRenderModelSelect() {
  const sel = chatEl("cc-model");
  if (!sel || !chatState.meta) return;
  const keep = sel.value;
  sel.innerHTML = "";
  const groups = [["text", "chat.optText"], ["image", "chat.optImage"], ["video", "chat.optVideo"]];
  for (const [kind, key] of groups) {
    const items = chatState.models.filter((m) => m.kind === kind);
    if (!items.length) continue;
    const g = document.createElement("optgroup");
    g.label = t(key);
    for (const m of items) {
      const o = document.createElement("option");
      o.value = m.id;
      if (!m.live) {
        // Движок не настроен ключами — честно говорим это, а не прячем.
        o.textContent = t("chat.offline", { title: m.title });
        o.disabled = true;
      } else if (!m.allowed) {
        // Закрытое тарифом ВИДНО с ценой и именем тарифа: молчаливый запрет
        // читается как поломка, а видимый замок работает витриной.
        o.textContent = t("chat.locked", {
          title: m.title, n: tNum(m.points), unit: chatUnit(m.points),
          plan: (m.plan || "pro").toUpperCase().replace("_", " "),
        });
        o.disabled = true;
      } else {
        o.textContent = t("chat.byEngine", { title: m.title, n: tNum(m.points), unit: chatUnit(m.points) });
      }
      g.appendChild(o);
    }
    sel.appendChild(g);
  }
  const wanted = chatModel(keep || chatDefaultModelId());
  sel.value = (wanted && wanted.allowed && wanted.live) ? wanted.id : "";
  if (!sel.value) {
    const first = chatState.models.find((m) => m.allowed && m.live);
    sel.value = first ? first.id : "";
  }
}

function chatDefaultModelId() {
  const active = chatState.chats.find((c) => c.id === chatState.activeId);
  if (active && active.model && chatModel(active.model)) return active.model;
  return (chatState.meta && chatState.meta.default_text) || "";
}

function chatRenderDuration() {
  const box = chatEl("cc-duration");
  const model = chatCurrentModel();
  if (!box || !chatState.meta) return;
  const on = Boolean(model && model.kind === "video");
  box.classList.toggle("hidden", !on);
  if (!on) return;
  if (!box.options.length) {
    (chatState.meta.durations || [6]).forEach((d) => {
      const o = document.createElement("option");
      o.value = String(d);
      o.textContent = t("chat.duration", { n: d });
      box.appendChild(o);
    });
    box.value = "6";
  } else {
    Array.from(box.options).forEach((o) => {
      o.textContent = t("chat.duration", { n: Number(o.value) });
    });
  }
}

// Цена ДО отправки. Видео считается пропорционально длительности — ровно так
// же, как на сервере (video_engine_usd), поэтому число совпадает с тем, что
// реально спишется.
function chatPrice() {
  const model = chatCurrentModel();
  if (!model) return 0;
  if (model.kind !== "video") return model.points;
  const dur = Number((chatEl("cc-duration") || {}).value || 6) || 6;
  return Math.max(2, Math.ceil(model.points * (dur / 6)));
}

function chatRenderPrice() {
  const price = chatEl("cc-price");
  const send = chatEl("cc-send");
  if (!price || !send) return;
  send.textContent = t("chat.send");
  const admin = chatState.meta && chatState.meta.is_admin;
  const have = chatState.meta ? Number(chatState.meta.points || 0) : 0;
  const need = chatPrice();
  price.textContent = admin
    ? ""
    : `${t("chat.price", { n: tNum(need), unit: chatUnit(need) })} · `
      + `${t("chat.priceHave", { n: tNum(have) })}`;
  price.classList.toggle("short", !admin && need > have);
}

// ────────── сайдбар: чаты по датам ──────────

function chatDayGroup(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "chat.groupOlder";
  const today = new Date();
  const days = Math.floor((today - d) / 86400000);
  if (d.toDateString() === today.toDateString()) return "chat.groupToday";
  if (days < 2) return "chat.groupYesterday";
  if (days < 7) return "chat.groupWeek";
  return "chat.groupOlder";
}

function chatRenderList() {
  const box = chatEl("chat-list");
  if (!box) return;
  box.innerHTML = "";
  const q = chatState.search.trim().toLowerCase();
  const rows = chatState.chats.filter((c) => !q || (c.title || "").toLowerCase().includes(q));
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted chat-list-empty";
    empty.textContent = t("chat.noChats");
    box.appendChild(empty);
    return;
  }
  let group = "";
  for (const c of rows) {
    const g = chatDayGroup(c.updated_at || c.created_at);
    if (g !== group) {
      group = g;
      const h = document.createElement("div");
      h.className = "chat-list-group";
      h.textContent = t(g);
      box.appendChild(h);
    }
    const row = document.createElement("div");
    row.className = "chat-row" + (c.id === chatState.activeId ? " on" : "");
    const name = document.createElement("button");
    name.type = "button";
    name.className = "chat-row-name";
    name.textContent = c.title || t("chat.untitled");
    name.addEventListener("click", () => chatOpen(c.id));
    const spent = document.createElement("span");
    spent.className = "chat-row-spent";
    spent.textContent = c.spent ? tNum(c.spent) : "";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost danger chat-row-del";
    del.textContent = "✕";
    del.title = t("chat.del");
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(t("chat.delConfirm"))) return;
      try {
        await api(`/api/chats/${c.id}`, { method: "DELETE" });
        if (chatState.activeId === c.id) {
          chatState.activeId = 0;
          chatState.messages = [];
        }
        await chatLoadList();
        chatRenderAll();
      } catch (err) { fail(err); }
    });
    row.appendChild(name);
    row.appendChild(spent);
    row.appendChild(del);
    box.appendChild(row);
  }
}

async function chatLoadList() {
  chatState.chats = await api("/api/chats");
}

async function chatOpen(id) {
  chatState.activeId = id;
  chatState.sourceId = 0;
  await chatLoadMessages();
  const active = chatState.chats.find((c) => c.id === id);
  if (active && active.model && chatModel(active.model)) {
    chatEl("cc-model").value = active.model;
  }
  chatRenderAll();
}

async function chatLoadMessages() {
  if (!chatState.activeId) {
    chatState.messages = [];
    chatState.hasMore = false;
    return;
  }
  // Тянем ровно столько, сколько уже показано (но не меньше страницы): иначе
  // поллинг статусов схлопывал бы подгруженную вверх историю обратно в 50.
  const limit = Math.max(50, chatState.messages.length);
  const data = await api(`/api/chats/${chatState.activeId}/messages?limit=${limit}`);
  chatState.messages = data.messages || [];
  chatState.hasMore = Boolean(data.has_more);
  if (chatState.meta && data.points !== undefined) chatState.meta.points = data.points;
  const idx = chatState.chats.findIndex((c) => c.id === data.chat.id);
  if (idx >= 0) chatState.chats[idx] = data.chat;
}

// Подгрузка старых сообщений вверх. Без неё длинный разговор упирался бы в
// последние 50 реплик: история лежит в базе, но человеку недоступна.
async function chatLoadOlder() {
  if (chatState.loadingMore || !chatState.messages.length) return;
  chatState.loadingMore = true;
  const feed = chatEl("chat-feed");
  const keepHeight = feed ? feed.scrollHeight : 0;
  try {
    const before = chatState.messages[0].id;
    const data = await api(
      `/api/chats/${chatState.activeId}/messages?before=${before}&limit=50`);
    chatState.messages = (data.messages || []).concat(chatState.messages);
    chatState.hasMore = Boolean(data.has_more);
    chatRenderFeed();
    // Возвращаем взгляд на то же сообщение, а не в начало ленты.
    if (feed) feed.scrollTop = feed.scrollHeight - keepHeight;
  } catch (e) {
    fail(e);
  } finally {
    chatState.loadingMore = false;
  }
}

// ────────── лента ──────────

function chatBubble(m) {
  const wrap = document.createElement("div");
  wrap.className = `chat-msg chat-${m.role}` + (m.status === "error" ? " chat-failed" : "");

  if (m.role === "assistant" && m.engine_title) {
    const head = document.createElement("div");
    head.className = "chat-msg-head";
    head.textContent = m.engine_title + (m.points ? ` · ${tNum(m.points)}` : "");
    if (m.params && m.params.provider) {
      head.title = t("chat.provider", { name: m.params.provider });
    }
    wrap.appendChild(head);
  }

  if (m.text) {
    const p = document.createElement("div");
    p.className = "chat-msg-text";
    p.textContent = m.text;
    wrap.appendChild(p);
  }

  (m.files || []).forEach((f) => {
    const img = document.createElement("img");
    img.className = "chat-attach";
    img.src = f.thumb_url || f.url;
    img.loading = "lazy";
    wrap.appendChild(img);
  });

  if (m.status === "queued" || m.status === "running") {
    const s = document.createElement("div");
    s.className = "status";
    s.textContent = t(m.status === "queued" ? "chat.queued" : "chat.running");
    wrap.appendChild(s);
  }

  if (m.status === "error") {
    const s = document.createElement("div");
    s.className = "status error";
    s.textContent = `${t("chat.failed")}: ${m.error || ""}`.slice(0, 300);
    wrap.appendChild(s);
  }

  if (m.expired) {
    // Файла нет, промпт есть: срок хранения не должен выглядеть как потеря
    // работы — повторить можно в один клик.
    const s = document.createElement("div");
    s.className = "muted chat-expired";
    s.textContent = t("chat.expired");
    wrap.appendChild(s);
  }

  if (m.url && m.kind === "image") {
    const a = document.createElement("a");
    a.href = m.url;
    a.target = "_blank";
    a.rel = "noopener";
    const img = document.createElement("img");
    img.className = "chat-media";
    img.src = m.thumb_url || m.url;
    img.loading = "lazy";
    // Миниатюру рисует ffmpeg; не срослось — показываем оригинал, а не
    // иконку битой картинки на месте оплаченной генерации.
    img.addEventListener("error", () => {
      if (m.url && img.src !== m.url) img.src = m.url;
    }, { once: true });
    img.addEventListener("load", chatScrollBottom, { once: true });
    a.appendChild(img);
    wrap.appendChild(a);
  } else if (m.url && m.kind === "video") {
    const v = document.createElement("video");
    v.className = "chat-media";
    v.src = m.url;
    v.controls = true;
    v.playsInline = true;
    v.addEventListener("loadeddata", chatScrollBottom, { once: true });
    wrap.appendChild(v);
  }

  if (m.role === "assistant") {
    const bar = document.createElement("div");
    bar.className = "chat-msg-bar";
    if (m.url && m.kind === "image") {
      // ГЛАВНЫЙ приём одного окна: картинка → ролик, не выходя из ленты.
      const anim = document.createElement("button");
      anim.className = "ghost";
      anim.textContent = t("chat.animate");
      anim.title = t("chat.animateTitle");
      anim.addEventListener("click", () => chatPickSource(m.id));
      bar.appendChild(anim);

      // Мост чат → студия: удачный кадр не остаётся в переписке.
      const save = document.createElement("button");
      save.className = "ghost";
      save.textContent = t("chat.saveTo");
      save.title = t("chat.saveToTitle");
      save.addEventListener("click", () => chatSaveModal(m));
      bar.appendChild(save);
    }
    if (m.status === "error" || m.expired) {
      const again = document.createElement("button");
      again.className = "ghost";
      again.textContent = t("chat.retry");
      again.addEventListener("click", () => chatRetry(m.id));
      bar.appendChild(again);
    }
    if (bar.childNodes.length) wrap.appendChild(bar);
  }
  return wrap;
}

function chatRenderFeed() {
  const feed = chatEl("chat-feed");
  if (!feed) return;
  feed.innerHTML = "";
  if (!chatState.messages.length) {
    const hello = document.createElement("div");
    hello.className = "chat-hello";
    const h = document.createElement("h2");
    h.textContent = t("chat.hello");
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = t("chat.helloHint");
    hello.appendChild(h);
    hello.appendChild(p);
    feed.appendChild(hello);
    return;
  }
  if (chatState.hasMore) {
    const more = document.createElement("button");
    more.className = "ghost chat-more";
    more.textContent = t("chat.older");
    more.addEventListener("click", chatLoadOlder);
    feed.appendChild(more);
  }
  chatState.messages.forEach((m) => feed.appendChild(chatBubble(m)));
  chatScrollBottom();
}

function chatRenderCompose() {
  const files = chatEl("cc-files");
  if (files) {
    files.innerHTML = "";
    chatState.files.forEach((f) => {
      const chip = document.createElement("span");
      chip.className = "cc-file";
      const img = document.createElement("img");
      img.src = f.thumb_url || f.url;
      chip.appendChild(img);
      const x = document.createElement("button");
      x.className = "ghost danger";
      x.textContent = "✕";
      x.addEventListener("click", () => {
        chatState.files = chatState.files.filter((y) => y.id !== f.id);
        chatRenderCompose();
      });
      chip.appendChild(x);
      files.appendChild(chip);
    });
  }
  const src = chatEl("cc-source");
  if (src) {
    src.classList.toggle("hidden", !chatState.sourceId);
    if (chatState.sourceId) {
      src.innerHTML = "";
      const label = document.createElement("span");
      label.textContent = t("chat.source");
      const clear = document.createElement("button");
      clear.className = "ghost";
      clear.textContent = t("chat.sourceClear");
      clear.addEventListener("click", () => { chatState.sourceId = 0; chatRenderCompose(); });
      src.appendChild(label);
      src.appendChild(clear);
    }
  }
  const meter = chatEl("cc-meter");
  const active = chatState.chats.find((c) => c.id === chatState.activeId);
  const spent = active ? Number(active.spent || 0) : 0;
  if (meter) meter.textContent = t("chat.meter", { n: tNum(spent), unit: chatUnit(spent) });
  // Под полем — про деньги, в сайдбаре — про срок хранения: две разные
  // обязанности, и дублировать одну и ту же строку дважды незачем.
  const note = chatEl("cc-note");
  if (note) note.textContent = t("chat.payNote");
  const retention = chatEl("chat-retention");
  if (retention && chatState.meta) {
    retention.textContent = chatState.meta.retention_days
      ? t("chat.retention", { n: chatState.meta.retention_days })
      : t("chat.retentionAdmin");
  }
  const points = chatEl("chat-points");
  if (points && chatState.meta) {
    points.classList.toggle("hidden", Boolean(chatState.meta.is_admin));
    points.textContent = `${tNum(chatState.meta.points || 0)} ${t("top.pointsUnit")}`;
  }
  const title = chatEl("chat-head-title");
  if (title) title.textContent = active ? (active.title || t("chat.untitled")) : t("chat.untitled");
}

function chatRenderAll() {
  chatRenderModelSelect();
  chatRenderDuration();
  chatRenderPrice();
  chatRenderList();
  chatRenderFeed();
  chatRenderCompose();
  chatSchedulePoll();
}

// ────────── действия ──────────

function chatPickSource(messageId) {
  chatState.sourceId = messageId;
  // Селектор сам переезжает на видео: человек нажал «Оживить», значит
  // намерение уже понятно, и требовать от него ещё и выбрать модель — лишнее.
  const sel = chatEl("cc-model");
  const wanted = (chatState.meta && chatState.meta.default_video) || "";
  const model = chatModel(wanted);
  if (sel && model && model.allowed && model.live) sel.value = wanted;
  else if (sel) {
    const first = chatState.models.find((m) => m.kind === "video" && m.allowed && m.live);
    if (first) sel.value = first.id;
  }
  chatRenderDuration();
  chatRenderPrice();
  chatRenderCompose();
  const ta = chatEl("cc-text");
  if (ta) ta.focus();
}

async function chatRetry(messageId) {
  try {
    await api(`/api/chats/${chatState.activeId}/messages/${messageId}/retry`, { method: "POST" });
    await chatAfterSend();
  } catch (e) { fail(e); }
}

async function chatAfterSend() {
  await chatLoadList();
  await chatLoadMessages();
  me = await api("/api/me").catch(() => me);
  if (me && me.user && chatState.meta) chatState.meta.points = me.user.gen_points;
  renderUserBar();
  chatRenderAll();
}

async function chatSend() {
  if (chatState.busy) return;
  const ta = chatEl("cc-text");
  const model = chatCurrentModel();
  if (!model) return;
  const text = (ta.value || "").trim();
  if (!text && model.kind !== "video") return;
  const send = chatEl("cc-send");
  chatState.busy = true;
  send.disabled = true;
  send.textContent = t("chat.sending");
  try {
    if (!chatState.activeId) {
      const created = await api("/api/chats", { method: "POST", body: {} });
      chatState.activeId = created.id;
    }
    const body = {
      text,
      engine: model.id,
      file_ids: chatState.files.map((f) => f.id),
      duration: Number((chatEl("cc-duration") || {}).value || 6),
    };
    if (chatState.sourceId) body.from_message_id = chatState.sourceId;
    await api(`/api/chats/${chatState.activeId}/messages`, { method: "POST", body });
    ta.value = "";
    chatState.files = [];
    chatState.sourceId = 0;
    await chatAfterSend();
  } catch (e) {
    // Нехватка очков приходит структурой (402 not_enough_points) — открываем
    // тарифы прямо отсюда, а не оставляем человека в тупике с alert'ом.
    if (e && e.code === "not_enough_points") openAccountModal("plan");
    fail(e);
  } finally {
    chatState.busy = false;
    send.disabled = false;
    chatRenderPrice();
  }
}

async function chatUpload(fileList) {
  for (const file of Array.from(fileList || []).slice(0, 8)) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api("/api/chat/upload", { method: "POST", body: fd });
      chatState.files.push(res);
    } catch (e) { fail(e); }
  }
  chatRenderCompose();
}

// «В проект»: картинка из чата уезжает персонажу — фотографией или разворотом.
function chatSaveModal(m) {
  openModal(t("chat.saveTitle"), (body) => {
    const chars = (project && project.characters) || [];
    if (!chars.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = t("chat.saveNoChars");
      body.appendChild(p);
      return;
    }
    const sel = document.createElement("select");
    chars.forEach((c) => {
      const o = document.createElement("option");
      o.value = String(c.id);
      o.textContent = c.name || t("character.noName");
      sel.appendChild(o);
    });
    body.appendChild(sel);

    const asSel = document.createElement("select");
    [["photo", "chat.saveAsPhoto"], ["model", "chat.saveAsModel"]].forEach(([v, key]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t(key); asSel.appendChild(o);
    });
    body.appendChild(asSel);

    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = t("common.save");
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await api(`/api/chats/messages/${m.id}/save-to`, {
          method: "POST",
          body: { character_id: Number(sel.value), as: asSel.value },
        });
        closeModal();
        await loadProject();
        await chatLoadMessages();
        chatRenderFeed();
        alert(t("chat.saved"));
      } catch (e) {
        go.disabled = false;
        fail(e);
      }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = t("common.cancel");
    cancel.addEventListener("click", closeModal);
    row.appendChild(go);
    row.appendChild(cancel);
    body.appendChild(row);
  });
}

// Статусы тянем тем же приёмом, что и в студии: пока что-то в работе —
// перечитываем ленту раз в три секунды. SSE ради этого не заводим.
function chatSchedulePoll() {
  clearTimeout(chatState.poll);
  const busy = chatState.messages.some((m) => m.status === "queued" || m.status === "running");
  if (!busy || $("#chat").classList.contains("hidden")) return;
  chatState.poll = setTimeout(async () => {
    try {
      await chatLoadMessages();
      me = await api("/api/me").catch(() => me);
      if (me && me.user && chatState.meta) chatState.meta.points = me.user.gen_points;
      chatRenderFeed();
      chatRenderCompose();
      // Цена под полем показывает и остаток очков: после возврата за упавшую
      // генерацию он меняется, и строка обязана это отразить.
      chatRenderPrice();
      chatSchedulePoll();
    } catch (e) { /* сеть моргнула — следующий тик разберётся */ }
  }, 3000);
}

// ────────── проводка кнопок ──────────
{
  const btn = chatEl("chat-btn");
  if (btn) btn.addEventListener("click", showChat);
  const back = chatEl("chat-to-studio");
  if (back) back.addEventListener("click", chatLeave);
  const toggle = chatEl("chat-side-toggle");
  if (toggle) toggle.addEventListener("click", () => $("#chat-side").classList.toggle("open"));

  const nw = chatEl("chat-new");
  if (nw) nw.addEventListener("click", async () => {
    chatState.activeId = 0;
    chatState.messages = [];
    chatState.files = [];
    chatState.sourceId = 0;
    chatRenderAll();
    const ta = chatEl("cc-text");
    if (ta) ta.focus();
  });

  const search = chatEl("chat-search");
  if (search) search.addEventListener("input", () => {
    chatState.search = search.value;
    chatRenderList();
  });

  const model = chatEl("cc-model");
  if (model) model.addEventListener("change", () => {
    // Сменили модель на не-видео — «Оживить» больше не в силе.
    const cur = chatCurrentModel();
    if (!cur || cur.kind !== "video") chatState.sourceId = 0;
    chatRenderDuration();
    chatRenderPrice();
    chatRenderCompose();
  });

  const dur = chatEl("cc-duration");
  if (dur) dur.addEventListener("change", chatRenderPrice);

  const send = chatEl("cc-send");
  if (send) send.addEventListener("click", chatSend);

  const ta = chatEl("cc-text");
  if (ta) ta.addEventListener("keydown", (e) => {
    // Enter отправляет, Shift+Enter переносит строку — как во всех чатах.
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      chatSend();
    }
  });

  const file = chatEl("cc-file");
  if (file) file.addEventListener("change", async () => {
    await chatUpload(file.files);
    file.value = "";
  });
}

// Вход по адресу /#/chat разбирает showApp(): к моменту его вызова сессия уже
// проверена, а до неё открывать чат нечем — /api/chat/models требует входа.
