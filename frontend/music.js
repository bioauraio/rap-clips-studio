// ══════════════════════ qlolmusic — витрина и приём заявок ══════════════════════
// Страница музыкального направления lolq.ai: лейбл, дистрибуция через Zvonko
// Digital, мастеринг, клип к релизу, договор и выплаты через юрлицо.
//
// Что здесь ЕСТЬ: рендер секций из общего словаря (i18n.js, раздел music.*),
// переключение языка без перезагрузки, валидация и отправка заявки.
// Чего здесь НЕТ и не будет до договора: любой интеграции с Zvonko Digital —
// ни их API, ни личного кабинета, ни статусов релизов. Страница только
// принимает заявку; всё остальное делается людьми и на бумаге.
//
// Зависимости: i18n.js (t, tRaw, applyI18n, onLangChange, syncLangSwitches,
// ERR_RU_TO_EN) — подключается ПЕРЕД этим файлом.

// ────────── что должен заполнить владелец ──────────
// Почта и телеграм для заявок. Пока пусто — страница НЕ выдумывает адрес:
// при недоступном роуте она честно говорит, что заявка не ушла, и предлагает
// скопировать текст. Как только адрес заведён — впиши сюда, и в том же
// сообщении появится живая ссылка «написать нам».
const MUSIC_CONTACT = { email: "", telegram: "" };

// Договор с дистрибьютором. false — на первом экране висит плашка «направление
// запускается, договор на подписании». Подписан — ставь true, плашка уходит.
const MUSIC_LAUNCH = { distributorSigned: false };

// Роут заявок пишет бэкенд-агент. Пока его нет, StaticFiles отвечает на POST
// 405 — этот случай разобран отдельно и показывается честно.
const MUSIC_LEAD_URL = "/api/music/lead";
const MUSIC_TIMEOUT_MS = 15000;

// ────────── мелочи ──────────
const $ = (sel, root = document) => (root || document).querySelector(sel);
const $$ = (sel, root = document) => Array.from((root || document).querySelectorAll(sel));

function muEsc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Доступ к разделу music словаря: MT — сырое значение (массивы, объекты),
// MTX — строка с подстановкой {переменных}. Читаются в момент вызова, поэтому
// смена языка подхватывается без перезагрузки.
function MT(path) { return tRaw("music." + path); }
function MTX(path, vars) { return t("music." + path, vars); }

function muLang() { return document.documentElement.getAttribute("lang") || "en"; }
function muShow(el, on) { if (el) el.classList.toggle("hidden", !on); }

// ────────── статичные секции ──────────
function muRenderPack() {
  const box = $("#mu-pack-list");
  if (box) box.innerHTML = (MT("hero.packItems") || []).map((s) => `<li>${muEsc(s)}</li>`).join("");
  // Плашка «направление запускается» живёт ровно до подписи договора.
  muShow($("#mu-status"), !MUSIC_LAUNCH.distributorSigned);
}

function muRenderPlatforms() {
  const box = $("#mu-plats-list");
  if (box) box.innerHTML = (MT("platforms.items") || []).map((s) => `<li>${muEsc(s)}</li>`).join("");
}

function muRenderOffer() {
  const box = $("#mu-offer-list");
  if (!box) return;
  box.innerHTML = (MT("offer.items") || []).map((it) => {
    // Миниатюра есть только у карточки клипа — это настоящий кадр студии,
    // а не иллюстрация «из интернета».
    const img = it.img
      ? `<img src="${muEsc(it.img)}" width="${it.w}" height="${it.h}" loading="lazy"
              alt="${muEsc(it.alt || "")}" />`
      : "";
    return `<article class="mu-offer-card">
      <h3>${muEsc(it.title)}</h3>
      <div class="mu-offer-body">
        ${img}
        <p>${muEsc(it.text)}</p>
      </div>
    </article>`;
  }).join("");
}

function muRenderFlow() {
  const box = $("#mu-flow-list");
  if (!box) return;
  box.innerHTML = (MT("flow.steps") || []).map((s) => `
    <li>
      <span class="mu-flow-n">${muEsc(s.n)}</span>
      <h3>${muEsc(s.title)}</h3>
      <p>${muEsc(s.text)}</p>
      <p class="mu-flow-meta">${muEsc(s.meta)}</p>
    </li>`).join("");
}

function muRenderNeed() {
  const box = $("#mu-need-list");
  if (!box) return;
  box.innerHTML = (MT("need.groups") || []).map((g) => `
    <article class="mu-need-card">
      <h3>${muEsc(g.title)}</h3>
      <ul>${(g.items || []).map((i) => `<li>${muEsc(i)}</li>`).join("")}</ul>
      ${g.note ? `<p class="mu-need-note">${muEsc(g.note)}</p>` : ""}
    </article>`).join("");
}

function muRenderTerms() {
  const box = $("#mu-terms-list");
  // <b> в строках словаря — наша разметка, поэтому идёт как есть.
  if (box) box.innerHTML = (MT("terms.items") || []).map((s) => `<li>${s}</li>`).join("");
}

function muRenderFaq() {
  const box = $("#mu-faq-list");
  if (!box) return;
  box.innerHTML = (MT("faq.items") || []).map((it) => `
    <details class="ld-q">
      <summary>${muEsc(it.q)}</summary>
      <p class="ld-a">${muEsc(it.a)}</p>
    </details>`).join("");
}

function muRenderSide() {
  const box = $("#mu-side-list");
  if (box) box.innerHTML = (MT("form.sideItems") || []).map((s) => `<li>${muEsc(s)}</li>`).join("");
}

function muRenderFooter() {
  const cols = $("#mu-foot-cols");
  if (cols) {
    cols.innerHTML = (MT("footer.cols") || []).map((c) => `
      <div class="ld-foot-col">
        <h4>${muEsc(c.title)}</h4>
        <ul>${(c.links || []).map((l) => (
          // Пустой href = ссылки ещё нет: показываем честную метку «скоро»,
          // а не заглушку, ведущую в никуда.
          l.href
            ? `<li><a href="${muEsc(l.href)}">${muEsc(l.label)}</a></li>`
            : `<li><span class="ld-link-off">${muEsc(l.label)}<em>${muEsc(MTX("footer.soon"))}</em></span></li>`
        )).join("")}</ul>
      </div>`).join("");
  }
  const legal = $("#mu-foot-legal-text");
  if (legal) {
    legal.innerHTML = (MT("footer.legal") || []).map((s) => `<span>${muEsc(s)}</span>`).join("");
  }
}

// Радио-чипы «что нужно». Выбор переживает смену языка: сначала запоминаем.
function muRenderNeedOptions() {
  const box = $("#mu-need-opts");
  if (!box) return;
  const chosen = muPickedNeed();
  box.innerHTML = (MT("form.needOptions") || []).map((o) => `
    <label class="mu-opt">
      <input type="radio" name="need" value="${muEsc(o.id)}"${o.id === chosen ? " checked" : ""} />
      <span>${muEsc(o.label)}</span>
    </label>`).join("");
}

function muPickedNeed() {
  const on = $("#mu-need-opts input:checked");
  return on ? on.value : "";
}

function muRenderAll() {
  document.title = MTX("meta.title") || "qlolmusic";
  muRenderPack();
  muRenderPlatforms();
  muRenderOffer();
  muRenderFlow();
  muRenderNeed();
  muRenderTerms();
  muRenderFaq();
  muRenderSide();
  muRenderNeedOptions();
  muRenderFooter();
}

// ────────── форма: проверка полей ──────────
const RE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const RE_TG = /^@?[a-z0-9_]{4,32}$/i;                      // ник телеграма
const RE_TG_LINK = /^(https?:\/\/)?(t\.me|telegram\.me)\/[a-z0-9_+/]+$/i;
const RE_URL = /^https?:\/\/[^\s.]+\.[^\s]{2,}$/i;

function muFieldError(name, msg) {
  const field = $(`.mu-field[data-field="${name}"]`);
  const err = $(`[data-err="${name}"]`);
  if (field) field.classList.toggle("bad", !!msg);
  if (err) { err.textContent = msg || ""; muShow(err, !!msg); }
}

function muClearErrors() {
  ["name", "contact", "demo", "need"].forEach((n) => muFieldError(n, ""));
}

// Собирает заявку и заодно чинит мелочи ввода: «soundcloud.com/x» без схемы
// превращается в https://soundcloud.com/x прямо в поле — человек видит, что
// именно уйдёт на сервер.
function muCollect() {
  const val = (sel) => (($(sel) || {}).value || "").trim();
  const demoEl = $("#mu-demo");
  let demo = val("#mu-demo");
  if (demo && !/^[a-z][a-z0-9+.-]*:\/\//i.test(demo) && /^[^\s]+\.[^\s]{2,}$/.test(demo)) {
    demo = "https://" + demo;
    if (demoEl) demoEl.value = demo;
  }
  return {
    name: val("#mu-name"),
    contact: val("#mu-contact"),
    demo,
    need: muPickedNeed(),
    comment: val("#mu-comment"),
    company: val("#mu-company"),   // ловушка: у человека всегда пусто
  };
}

function muValidate(data) {
  muClearErrors();
  let bad = null;
  const flag = (field, msg) => { muFieldError(field, msg); if (!bad) bad = field; };

  if (data.name.length < 2) flag("name", MTX("form.errName"));

  if (!data.contact) flag("contact", MTX("form.errContact"));
  else if (!(RE_EMAIL.test(data.contact) || RE_TG.test(data.contact) || RE_TG_LINK.test(data.contact))) {
    flag("contact", MTX("form.errContactBad"));
  }

  if (!data.demo) flag("demo", MTX("form.errDemo"));
  else if (!RE_URL.test(data.demo)) flag("demo", MTX("form.errDemoBad"));

  const ids = (MT("form.needOptions") || []).map((o) => o.id);
  if (!data.need || ids.indexOf(data.need) === -1) flag("need", MTX("form.errNeed"));

  if (bad) {
    const el = $(`.mu-field[data-field="${bad}"] input, .mu-field[data-field="${bad}"] textarea`)
      || $(`.mu-field[data-field="${bad}"] .mu-opt input`);
    if (el && el.focus) el.focus();
  }
  return !bad;
}

// ────────── форма: сообщения ──────────
function muNote(kind, html) {
  const box = $("#mu-note");
  if (!box) return;
  box.className = "mu-note" + (kind ? " " + kind : "");
  box.innerHTML = html;
  muShow(box, true);
  $$("[data-mu-copy]", box).forEach((b) => b.addEventListener("click", muCopyLead));
}

function muHideNote() { muShow($("#mu-note"), false); }

// Текст заявки для буфера обмена и для письма: те же подписи, что в форме,
// чтобы человек не пересобирал её руками.
function muLeadText(data) {
  const label = (id) => {
    const o = (MT("form.needOptions") || []).find((x) => x.id === id);
    return o ? o.label : id;
  };
  const lines = [
    MTX("form.mailSubject"),
    "",
    MTX("form.nameLabel") + ": " + data.name,
    MTX("form.contactLabel") + ": " + data.contact,
    MTX("form.demoLabel") + ": " + data.demo,
    MTX("form.needLabel") + ": " + label(data.need),
  ];
  if (data.comment) lines.push(MTX("form.commentLabel") + ": " + data.comment);
  return lines.join("\n");
}

let muLastLead = null;

async function muCopyLead(e) {
  const btn = e.currentTarget;
  const text = muLeadText(muLastLead || muCollect());
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (err) { ok = false; }
  if (!ok) {
    // Запасной путь для старых браузеров и http-контекста.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (err) { ok = false; }
  }
  btn.textContent = ok ? MTX("form.copied") : MTX("form.copyFail");
  btn.disabled = ok;
}

// Роут ещё не поднят (404/405/501) — самый вероятный случай на сегодня.
// Заявка НЕ ушла, и страница говорит это прямым текстом.
function muNoteOff(data) {
  const mail = MUSIC_CONTACT.email;
  const body = mail
    ? MTX("form.offText", { email: `<a href="mailto:${muEsc(mail)}?subject=${encodeURIComponent(MTX("form.mailSubject"))}&body=${encodeURIComponent(muLeadText(data))}">${muEsc(mail)}</a>` })
    : MTX("form.offTextNoMail");
  const tg = MUSIC_CONTACT.telegram
    ? ` <a href="https://t.me/${muEsc(String(MUSIC_CONTACT.telegram).replace(/^@/, ""))}">@${muEsc(String(MUSIC_CONTACT.telegram).replace(/^@/, ""))}</a>`
    : "";
  muNote("warn", `<b>${muEsc(MTX("form.offTitle"))}</b>
    <span>${body}${tg}</span>
    <span class="mu-note-acts">
      <button type="button" data-mu-copy>${muEsc(MTX("form.offCopy"))}</button>
    </span>`);
}

function muNoteFail(msg) {
  muNote("error", `<span>${muEsc(msg)}</span>
    <span class="mu-note-acts">
      <button type="button" data-mu-copy>${muEsc(MTX("form.offCopy"))}</button>
    </span>`);
}

// Ошибка сервера: FastAPI отдаёт {"detail": "…"} по-русски, поэтому английской
// версии подставляем перевод из общей карты, а незнакомое показываем как есть —
// пустой экран хуже кривого перевода.
function muServerMsg(data, res) {
  let raw = "";
  if (data && typeof data === "object") raw = data.error || data.detail || data.message || "";
  if (raw && typeof raw === "object") raw = raw.msg || raw.error || "";
  raw = String(raw || "").trim();
  if (!raw) return "HTTP " + res.status;
  if (muLang() === "en" && typeof ERR_RU_TO_EN !== "undefined" && ERR_RU_TO_EN[raw]) {
    return ERR_RU_TO_EN[raw];
  }
  return raw;
}

function muSuccess() {
  const form = $("#mu-form");
  muHideNote();
  muShow(form, false);
  const ok = $("#mu-ok");
  if (!ok) return;
  ok.innerHTML = `<b>${muEsc(MTX("form.okTitle"))}</b>
    <p>${muEsc(MTX("form.okText"))}</p>
    <button type="button" id="mu-again">${muEsc(MTX("form.okAgain"))}</button>`;
  muShow(ok, true);
  const again = $("#mu-again");
  if (again) {
    again.addEventListener("click", () => {
      if (form) { form.reset(); muClearErrors(); muRenderNeedOptions(); }
      muShow(ok, false);
      muShow(form, true);
      const first = $("#mu-name");
      if (first) first.focus();
    });
  }
}

// ────────── форма: отправка ──────────
let muSending = false;

async function muSubmit(e) {
  e.preventDefault();
  if (muSending) return;

  const data = muCollect();
  muLastLead = data;
  if (!muValidate(data)) return;
  // Ловушку заполняют только боты: молча не отправляем и не радуем их успехом.
  if (data.company) { muNoteFail(MTX("form.failGeneric", { msg: "spam" })); return; }

  const btn = $("#mu-submit");
  const label = btn ? btn.textContent : "";
  muSending = true;
  if (btn) { btn.disabled = true; btn.textContent = MTX("form.sending"); }
  muHideNote();

  const done = () => {
    muSending = false;
    if (btn) { btn.disabled = false; btn.textContent = label || MTX("form.submit"); }
  };

  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), MUSIC_TIMEOUT_MS) : 0;

  let res;
  try {
    res = await fetch(MUSIC_LEAD_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ctrl ? ctrl.signal : undefined,
      body: JSON.stringify({
        name: data.name,
        contact: data.contact,
        demo: data.demo,
        need: data.need,
        comment: data.comment,
        lang: muLang(),
        source: "music-page",
      }),
    });
  } catch (err) {
    // Сети нет, сервер не ответил, таймаут — заявка не ушла, так и пишем.
    clearTimeout(timer);
    done();
    muNoteFail(MTX("form.failNet"));
    return;
  }
  clearTimeout(timer);

  // Роута ещё нет: статика отвечает на POST 405, у обратного прокси бывает 404.
  if (res.status === 404 || res.status === 405 || res.status === 501) {
    done();
    muNoteOff(data);
    return;
  }
  if (res.status === 429) { done(); muNoteFail(MTX("form.failBusy")); return; }

  let body = null;
  try { body = await res.json(); } catch (err) { body = null; }

  if (!res.ok) {
    done();
    muNoteFail(MTX("form.failServer", { msg: muServerMsg(body, res) }));
    return;
  }
  // 200, но сервер сказал ok:false — это отказ, а не успех.
  if (body && body.ok === false) {
    done();
    muNoteFail(MTX("form.failServer", { msg: muServerMsg(body, res) }));
    return;
  }
  done();
  muSuccess();
}

// ────────── запуск ──────────
function muGoto(sel) {
  const el = document.querySelector(sel);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  if (sel === "#mu-apply") {
    const first = $("#mu-name");
    // Фокус ставим после прокрутки, иначе браузер дёрнет её обратно.
    if (first) setTimeout(() => first.focus({ preventScroll: true }), 400);
  }
}

function muInit() {
  applyI18n(document);
  syncLangSwitches();
  muRenderAll();

  $$("[data-mu-goto]").forEach((b) => {
    b.addEventListener("click", () => muGoto(b.dataset.muGoto));
  });

  const form = $("#mu-form");
  if (form) {
    form.addEventListener("submit", muSubmit);
    // Ошибку поля гасим, как только человек начал её править.
    ["name", "contact", "demo"].forEach((n) => {
      const el = $(`#mu-${n}`);
      if (el) el.addEventListener("input", () => muFieldError(n, ""));
    });
    const opts = $("#mu-need-opts");
    if (opts) opts.addEventListener("change", () => muFieldError("need", ""));
  }

  // Смена языка: тексты по data-i18n проставит сам i18n.js, а собранные
  // кодом списки перерисовываем здесь. Введённое в форму не трогаем.
  onLangChange(() => {
    muRenderAll();
    const note = $("#mu-note");
    // Сообщение о неудаче перерисовать нечем — оно про конкретную попытку;
    // прячем, чтобы на экране не осталось строки на прошлом языке.
    if (note && !note.classList.contains("hidden")) muHideNote();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", muInit);
} else {
  muInit();
}
