/* lolq.ai — админка. Одна страница, восемь разделов, ноль зависимостей.
 *
 * ПОЧЕМУ ОТДЕЛЬНАЯ СТРАНИЦА, А НЕ ВКЛАДКИ КАБИНЕТА. Админское было размазано
 * по модалке кабинета (ACC_TABS с admin:true) поверх открытого проекта: CRM
 * в модальном окне над студией. Владелец просил развести это явно.
 *
 * ДОСТУП. Файл отдаёт роут main.admin_page под проверкой is_admin; не админу
 * и гостю — 404, а не 403: существование админки посторонним не подтверждаем.
 * Поэтому в самом скрипте проверок доступа нет и быть не должно — проверка
 * на клиенте это не защита, а её имитация.
 *
 * ЯЗЫК. Админка одноязычная (русский): её видит один человек, и таскать сюда
 * словарь i18n ради него значит держать второй перевод, который никто не
 * читает. Пользовательский интерфейс — двуязычный, он в frontend/i18n.js.
 */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  const num = (n) => Number(n || 0).toLocaleString("ru-RU");
  const money = (cents) => "$" + (Number(cents || 0) / 100).toFixed(2);
  const mb = (b) => (Number(b || 0) / 1024 / 1024).toFixed(1) + " МБ";
  const when = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? "—" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  };

  async function api(path, opts) {
    const o = Object.assign({ headers: {} }, opts || {});
    if (o.body && !(o.body instanceof FormData)) {
      o.headers["Content-Type"] = "application/json";
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch(path, o);
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!r.ok) {
      const msg = (data && (data.detail || data.message)) || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function fail(where, e) {
    where.innerHTML = `<div class="adm-card adm-err">${esc(e.message || e)}</div>`;
  }

  function loading(where) {
    where.innerHTML = `<div class="adm-card muted">загружаю…</div>`;
  }

  /* ─────────────────────────── разделы ───────────────────────────
     Порядок = порядок работы: сначала «что происходит», потом люди,
     потом деньги, и только потом настройки самого сервиса. */
  const TABS = [
    { id: "stats", ico: "📊", title: "Сводка",
      sub: "Регистрации, выручка и себестоимость по дням. Себестоимость — то, что мы реально платим kie.ai и Anthropic." },
    { id: "users", ico: "👤", title: "Клиенты",
      sub: "Поиск, тариф, токены и блокировка. Каждое действие пишется в журнал." },
    { id: "broadcast", ico: "✉️", title: "Рассылка",
      sub: "Сегменты считаются в момент отправки, а не сохраняются списком: сохранённый за неделю протухает." },
    { id: "payouts", ico: "💸", title: "Выплаты",
      sub: "Заявки амбассадоров партнёрки." },
    { id: "demos", ico: "🎤", title: "Демки",
      sub: "Заявки на лейбл со страницы «Дистрибуция»: анкета, права, файлы и отчёт технических проверок." },
    { id: "ledger", ico: "🧾", title: "Журнал",
      sub: "Инвариант: сумма строк журнала против фактического баланса. Расхождение = кто-то прошёл мимо кассы." },
    { id: "prompts", ico: "🎬", title: "Промты",
      sub: "Вся настройка промтов одной страницей: слои каталога, стили, референсы, тренды и шаблоны мокапов. Второго входа в эти разделы нет — два входа означали бы два разных представления о том, что сохранено." },
    { id: "design", ico: "🎨", title: "Дизайн",
      sub: "Живые токены дизайн-системы: огненный градиент, фон, стекло. Меняются без переката — theme.css отдаёт их поверх стилей." },
    { id: "earn", ico: "💸", title: "Заработок",
      sub: "Партнёрские продукты витрины /earn: название, награда, ссылка на лендинг и фото товара. Всё, что здесь включено, сразу видно партнёрам." },
    { id: "market", ico: "🌍", title: "Рынок",
      sub: "Сводная по конкурентам: цены, фишки, модели, дизайн. Публичная копия живёт на /competitors.html." },
    { id: "pricing", ico: "📈", title: "Наценка",
      sub: "Наш токен — своя валюта и не равен токену движка. Ползунок задаёт, во сколько раз мы продаём дороже себестоимости. Действует сразу." },
    { id: "models", ico: "⚙️", title: "Модели",
      sub: "Что живо по ключам, сколько стоит нам и сколько человеку. Только чтение — движки правятся кодом." },
    { id: "settings", ico: "🔑", title: "Настройки",
      sub: "Индикация ключей и каналов. Значений здесь нет и не будет: ключи живут в infra/.env." },
  ];

  const pane = () => $(".adm-pane");
  let current = "stats";

  /* ─── ГРУППЫ МЕНЮ: 7 пунктов вместо 13 плоских ───
     Внутри группы — подвкладки той же механики, что в «Промтах» (.adm-seg).
     id вкладок СТАРЫЕ: любая закладка /admin?tab=broadcast открывает
     «Маркетинг → Рассылка» сама, без отдельных редиректов.
     «Рынок» положен в «Настройки»: его контент — справочник конкурентов для
     калибровки цен (сосед «Наценки»), а не ежедневная аналитика Сводки.
     Иконки — моно-SVG тонкой линией, цвет от currentColor. */
  const GI = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const GROUPS = [
    { id: "stats", title: "Сводка", tabs: ["stats"],
      ico: GI('<path d="M4 19V10M10 19V5M16 19v-7M21 19H3"/>') },
    { id: "users", title: "Клиенты", tabs: ["users"],
      ico: GI('<circle cx="12" cy="8" r="3.4"/><path d="M5.5 19.5c1-3.4 3.5-5.2 6.5-5.2s5.5 1.8 6.5 5.2"/>') },
    { id: "mkt", title: "Маркетинг", tabs: ["broadcast", "payouts", "earn"],
      ico: GI('<path d="M3 11v3l4 1 10 4V6L7 10l-4 1z"/><path d="M8 15.5V19a1.5 1.5 0 0 0 3 0v-2.6"/><path d="M20 10.5a2 2 0 0 1 0 4"/>') },
    { id: "music", title: "Музыка", tabs: ["demos"],
      ico: GI('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/>') },
    { id: "prompts", title: "Промты", tabs: ["prompts"],
      ico: GI('<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M3 11h18M6.5 7l2.5 4M11.5 7l2.5 4M16.5 7l2.5 4M4 7l2-2.5 15 2.5"/>') },
    { id: "cfg", title: "Настройки",
      tabs: ["settings", "design", "models", "pricing", "market", "ledger"],
      ico: GI('<circle cx="12" cy="12" r="3"/><path d="M12 2.8v3M12 18.2v3M2.8 12h3M18.2 12h3M5.5 5.5l2.1 2.1M16.4 16.4l2.1 2.1M18.5 5.5l-2.1 2.1M7.6 16.4l-2.1 2.1"/>') },
  ];
  const groupOf = (id) => GROUPS.find((g) => g.tabs.includes(id)) || GROUPS[0];

  function nav() {
    const box = $(".adm-nav");
    box.innerHTML = "";
    const on = groupOf(current);
    GROUPS.forEach((g) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "adm-tab" + (g.id === on.id ? " on" : "");
      b.innerHTML = `<span class="adm-tab-ico">${g.ico}</span>${esc(g.title)}`;
      // Возврат в группу помнит последнюю открытую подвкладку.
      b.addEventListener("click", () => open(g.id === on.id ? g.tabs[0]
        : (g.last || g.tabs[0])));
      box.appendChild(b);
    });
  }

  function open(id) {
    current = TABS.some((t) => t.id === id) ? id : "stats";
    const spec = TABS.find((t) => t.id === current);
    const g = groupOf(current);
    g.last = current;
    $(".adm-title").textContent = g.tabs.length > 1
      ? `${g.title} · ${spec.title}` : spec.title;
    $(".adm-sub").textContent = spec.sub;
    // Подраздел живёт в адресе вместе с разделом: перезагрузка страницы на
    // «Шаблонах мокапов» обязана вернуть на «Шаблоны мокапов», а не в начало.
    history.replaceState(null, "", "/admin?tab=" + current
      + (current === "prompts" ? "&sub=" + promptTab : ""));
    nav();
    const p = pane();
    if (g.tabs.length > 1) {
      // Подвкладки группы — тот же сегмент-контрол, что в «Промтах».
      p.innerHTML = `<div class="adm-seg g-seg"></div><div class="g-sub"></div>`;
      const seg = $(".g-seg", p);
      g.tabs.forEach((tid) => {
        const t = TABS.find((x) => x.id === tid);
        const b = document.createElement("button");
        b.type = "button";
        b.className = tid === current ? "on" : "";
        b.textContent = t ? t.title : tid;
        b.addEventListener("click", () => open(tid));
        seg.appendChild(b);
      });
      const sub = $(".g-sub", p);
      loading(sub);
      RENDER[current](sub);
    } else {
      loading(p);
      RENDER[current](p);
    }
  }

  /* ─────────────────────────── сводка ─────────────────────────── */
  async function renderStats(box) {
    try {
      const [d, live] = await Promise.all([
        api("/api/admin/stats?days=30"),
        api("/api/admin/dashboard").catch(() => null),
      ]);
      const margin = d.revenue_cents - d.cost_cents;
      const gwPill = (ok, name) =>
        `<span class="adm-pill ${ok ? "" : "warn"}">${name}: ${ok ? "ок" : "недоступен"}</span>`;
      const dash = !live ? "" : `
        <div class="adm-grid">
          ${(() => {
            const k = live.providers.kie || {};
            const v = k.ok ? Math.round(k.credit) : "—";
            const bad = k.ok && k.credit < 500;
            return `<div class="adm-stat ${bad ? "adm-stat-bad" : ""}">
              <b>${v}</b><span>кредиты kie.ai${bad ? " — мало!" : ""}</span></div>`;
          })()}
          <div class="adm-stat"><b style="font-size:14px;line-height:2">
            ${gwPill(live.providers.gw_chatgpt, "ChatGPT")} ${gwPill(live.providers.gw_grok, "Grok")}
          </b><span>шлюзы</span></div>
          ${stat(num(live.gen.frames_24h) + " / " + num(live.gen.frames_7d), "кадров 24ч / 7д")}
          ${stat(num(live.gen.videos_24h) + " / " + num(live.gen.videos_7d), "видео 24ч / 7д")}
          ${stat(num(live.gen.errors_24h), "ошибок за 24ч")}
          ${stat(num(live.gen.queue), "в очереди сейчас")}
          ${stat(num(live.economy.spent_7d) + " ⚡", "потрачено за 7д")}
          ${stat(pct(live.economy.saved, live.economy.users_total), "гость → аккаунт")}
          ${stat(num(live.household.characters), "персонажей")}
          ${stat(num(live.household.tracks), "треков")}
          ${live.household.disk.total ? stat(
            mb(live.household.disk.free) + " своб.",
            "диск: занято " + mb(live.household.disk.used)) : ""}
        </div>
        <div class="adm-card">
          <div class="adm-row" style="align-items:center;flex-wrap:wrap">
            <b>Топ-5 по тратам (7д):</b>
            ${(live.economy.top || []).map((u) =>
              `<span class="adm-pill">${esc(u.name)} · ${num(u.spent)} ⚡</span>`).join("") || "<span class='muted'>пусто</span>"}
          </div>
        </div>
        <div class="adm-card">
          <div class="adm-row" style="align-items:center;flex-wrap:wrap">
            <b>Превью каталога:</b>
            ${Object.entries(live.previews || {}).map(([l, c]) =>
              `<span class="adm-pill ${c.have < c.total ? "warn" : ""}">${esc(l)}: ${c.have}/${c.total}</span>`).join("")}
            <span style="flex:1"></span>
            <button type="button" class="ghost dash-go" data-go="prompts">Промты</button>
            <button type="button" class="ghost dash-go" data-go="demos">Демки</button>
            <button type="button" class="ghost dash-go" data-go="models">Модели</button>
            <button type="button" class="ghost dash-batch">Догнать превью</button>
            <span class="dash-batch-msg muted"></span>
          </div>
        </div>`;
      box.innerHTML = dash + `
        <div class="adm-grid">
          ${stat(num(d.users.total), "клиентов всего")}
          ${stat(num(d.users.paying), "на платном тарифе")}
          ${stat(num(d.users.active7), "заходили за 7 дней")}
          ${stat(num(d.signups), "регистраций за 30 дней")}
          ${stat(money(d.revenue_cents), "выручка за 30 дней")}
          ${stat(money(d.cost_cents), "себестоимость генераций")}
          ${stat(money(margin), "разница")}
          ${stat(num(d.spent) + " ⚡", "токенов потрачено")}
          ${stat(mb(d.storage.bytes), num(d.storage.files) + " файлов")}
        </div>
        <div class="adm-card">
          <h3>По дням</h3>
          <table class="adm-table">
            <thead><tr><th>дата</th><th class="num">регистраций</th>
              <th class="num">выручка</th><th class="num">себестоимость</th>
              <th class="num">токенов</th></tr></thead>
            <tbody>${d.daily.slice().reverse().map((r) => `
              <tr><td>${esc(r.date)}</td>
                <td class="num">${num(r.signups)}</td>
                <td class="num">${money(r.revenue_cents)}</td>
                <td class="num">${money(r.cost_cents)}</td>
                <td class="num">${num(r.spent)}</td></tr>`).join("")}</tbody>
          </table>
        </div>`;
      $$(".dash-go", box).forEach((b) =>
        b.addEventListener("click", () => open(b.dataset.go)));
      const batch = $(".dash-batch", box);
      if (batch) batch.addEventListener("click", async () => {
        const m = $(".dash-batch-msg", box);
        batch.disabled = true;
        m.textContent = "запускаю…";
        try {
          const r = await api("/api/admin/previews-batch", { method: "POST" });
          m.textContent = r.busy ? "батч уже идёт" : "пошёл фоном — вернись через пару минут";
        } catch (e) { m.textContent = "не вышло: " + (e.message || e); batch.disabled = false; }
      });
    } catch (e) { fail(box, e); }
  }

  const stat = (v, s) => `<div class="adm-stat"><b>${esc(v)}</b><span>${esc(s)}</span></div>`;
  const pct = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "—");

  /* ─────────────────────────── клиенты ─────────────────────────── */
  async function renderUsers(box) {
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <input type="text" class="u-q" placeholder="имя, почта, логин, telegram" style="flex:1;min-width:200px" />
          <select class="u-plan">
            <option value="">любой тариф</option>
            <option value="free">FREE</option>
            <option value="pro">PRO</option>
            <option value="pro_max">PRO MAX</option>
            <option value="studio">ULTRA</option>
          </select>
          <select class="u-sort">
            <option value="new">сначала новые</option>
            <option value="points">по токенам</option>
            <option value="seen">по последнему входу</option>
          </select>
          <button type="button" class="primary u-go">Найти</button>
        </div>
      </div>
      <div class="u-out"></div>`;
    const out = $(".u-out", box);
    const load = async () => {
      loading(out);
      try {
        const qs = new URLSearchParams({
          q: $(".u-q", box).value.trim(),
          plan: $(".u-plan", box).value,
          sort: $(".u-sort", box).value,
          limit: "50",
        });
        const d = await api("/api/admin/users?" + qs.toString());
        out.innerHTML = `<div class="adm-card">
          <table class="adm-table">
            <thead><tr><th>кто</th><th>тариф</th><th class="num">токены</th>
              <th>последний вход</th><th></th></tr></thead>
            <tbody>${d.items.map(userRow).join("")}</tbody>
          </table>
          ${d.items.length ? "" : '<p class="muted">никого не нашлось</p>'}
        </div>`;
        $$(".u-card", out).forEach((b) => b.addEventListener("click", () => userCard(b.dataset.id)));
      } catch (e) { fail(out, e); }
    };
    $(".u-go", box).addEventListener("click", load);
    $(".u-q", box).addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
    load();
  }

  function userRow(u) {
    const who = esc(u.name || u.login || u.email || ("#" + u.id));
    const contact = esc(u.email || (u.tg ? "@" + u.tg : ""));
    return `<tr>
      <td><b>${who}</b>${contact ? `<br /><span class="muted">${contact}</span>` : ""}
        ${u.is_blocked ? '<br /><span class="adm-pill warn">заблокирован</span>' : ""}</td>
      <td>${esc(u.plan_title || u.plan)}${u.plan_tier ? `<br /><span class="muted">${esc(u.plan_tier)}</span>` : ""}</td>
      <td class="num">${num(u.points)}</td>
      <td>${when(u.last_seen_at)}</td>
      <td><button type="button" class="u-card ghost" data-id="${u.id}">карточка</button></td>
    </tr>`;
  }

  async function userCard(uid) {
    const box = pane();
    loading(box);
    try {
      const [d, note] = await Promise.all([
        api("/api/admin/users/" + uid),
        api(`/api/admin/users/${uid}/note`).catch(() => ({ note: "" })),
      ]);
      const u = d.user || d;
      const mask = (v) => v ? String(v).replace(/(.{2}).+(@.+)/, "$1…$2") : "";
      const linked = Object.entries(d.linked || {})
        .filter(([, v]) => v).map(([k]) => k).join(", ") || "—";
      box.innerHTML = `
        <div class="adm-card">
          <div class="adm-row" style="align-items:center">
            <button type="button" class="ghost u-back">← к списку</button>
            ${u.avatar_url ? `<img src="${esc(u.avatar_url)}" style="width:44px;height:44px;border-radius:50%;object-fit:cover">` : ""}
            <div>
              <b style="font-size:16px">${esc(u.name || u.login || ("#" + u.id))}</b>
              ${u.is_blocked ? '<span class="adm-pill warn">заблокирован</span>' : ""}
              ${u.is_admin ? '<span class="adm-pill">админ</span>' : ""}
              <div class="muted" style="font-size:12px">
                ${esc(mask(u.email))} ${u.tg ? "· @" + esc(u.tg) : ""}
                · входы: ${esc(linked)}
                · рег. ${when(u.created_at)} · был ${when(u.last_seen_at)}
              </div>
            </div>
          </div>
          <div class="adm-grid" style="margin-top:12px">
            ${stat(esc(u.plan_title || u.plan) + (u.plan_tier ? " " + esc(u.plan_tier) : ""), "тариф · до " + when(u.plan_until))}
            ${stat(num(u.points) + " ⚡", "токенов" + (d.drip && d.drip.left ? ` (+${num(d.drip.left)} капельных)` : ""))}
            ${stat(esc(u.pay_source || "—"), "кто списывает")}
            ${stat(esc(u.ref_code || "—"), "реф-код" + (d.invited_by ? " · пригласил " + esc(d.invited_by.name) : ""))}
            ${stat(num(d.referrals || 0), "приглашено им")}
            ${stat(num((d.work || {}).projects || 0) + " / " + num((d.work || {}).tracks || 0), "проектов / треков")}
            ${stat(num((d.work || {}).scenes || 0) + " / " + num((d.work || {}).clips || 0), "сцен / клипов")}
            ${stat(money((d.money || {}).paid_cents || 0) + " + " + num(((d.money || {}).paid_kopeks || 0) / 100) + " ₽", "оплачено всего")}
            ${stat(num((d.money || {}).spent_points || 0) + " ⚡", "потрачено токенов")}
          </div>
        </div>

        <div class="adm-card">
          <h3>Действия</h3>
          <div class="adm-row" style="flex-wrap:wrap">
            <input type="number" class="p-delta" value="100" style="width:110px" />
            <input type="text" class="p-why" placeholder="за что (попадёт в журнал)" style="flex:1;min-width:160px" />
            <button type="button" class="primary p-go">Токены</button>
            <select class="pl-sel">${(d.plans || []).map((pl) =>
              `<option value="${esc(pl.id)}" ${pl.id === u.plan ? "selected" : ""}>${esc(pl.title)}</option>`).join("")}</select>
            <button type="button" class="pl-go">Сменить тариф</button>
            <button type="button" class="ghost danger b-go">${u.is_blocked ? "Разблокировать" : "Заблокировать"}</button>
            <span class="p-msg"></span>
          </div>
          <p class="adm-note">Каждое действие пишется в журнал вместе с тем, кто его сделал.</p>
        </div>

        <div class="adm-card">
          <h3>Заметки админа</h3>
          <textarea class="u-note" rows="3" style="width:100%">${esc(note.note || "")}</textarea>
          <div class="adm-row"><button type="button" class="u-note-save">Сохранить заметку</button>
            <span class="u-note-msg muted"></span></div>
        </div>

        <div class="adm-card">
          <h3>Оплаты (${num((d.payments || []).length)})</h3>
          ${(d.payments || []).length ? `<table class="adm-table">
            <thead><tr><th>когда</th><th>провайдер</th><th>что</th>
              <th class="num">сумма</th><th class="num">⚡</th></tr></thead>
            <tbody>${d.payments.map((pp) => `<tr>
              <td>${when(pp.at)}</td><td>${esc(pp.provider)}</td>
              <td>${esc(pp.kind)}${pp.plan ? " · " + esc(pp.plan) + "/" + esc(pp.period) : ""}</td>
              <td class="num">${pp.amount_cents ? money(pp.amount_cents) : num(pp.amount_kopeks / 100) + " ₽"}</td>
              <td class="num">${num(pp.points)}</td></tr>`).join("")}</tbody>
          </table>` : '<p class="muted">платежей не было</p>'}
        </div>

        <div class="adm-card">
          <h3>Лента действий</h3>
          <div class="u-timeline"><p class="muted">загружаю…</p></div>
          <div class="adm-row"><button type="button" class="u-tl-more hidden">Показать раньше</button></div>
        </div>`;

      $(".u-back", box).addEventListener("click", () => open("users"));
      const msg = $(".p-msg", box);
      const act = async (fn, okText) => {
        msg.textContent = "…"; msg.className = "p-msg muted";
        try {
          await fn();
          msg.className = "p-msg adm-ok"; msg.textContent = okText;
          userCard(uid);
        } catch (e) { msg.className = "p-msg adm-err"; msg.textContent = e.message || e; }
      };
      $(".p-go", box).addEventListener("click", () => act(() =>
        api(`/api/admin/users/${uid}/points`, { method: "POST",
          body: { delta: Number($(".p-delta", box).value || 0),
                  reason: $(".p-why", box).value.trim() } }), "готово"));
      $(".pl-go", box).addEventListener("click", () => act(() =>
        api(`/api/admin/users/${uid}/plan`, { method: "POST",
          body: { plan: $(".pl-sel", box).value } }), "тариф сменён"));
      $(".b-go", box).addEventListener("click", () => {
        const reason = u.is_blocked ? "" :
          (prompt("Причина блокировки (увидит клиент):") || "");
        if (!u.is_blocked && !reason) return;
        act(() => api(`/api/admin/users/${uid}/block`, { method: "POST",
          body: { blocked: !u.is_blocked, reason } }),
          u.is_blocked ? "разблокирован" : "заблокирован");
      });
      $(".u-note-save", box).addEventListener("click", async () => {
        const m = $(".u-note-msg", box);
        m.textContent = "…";
        try {
          await api(`/api/admin/users/${uid}/note`, { method: "POST",
            body: { note: $(".u-note", box).value } });
          m.textContent = "сохранено";
        } catch (e) { m.textContent = "не вышло: " + (e.message || e); }
      });

      // Лента: подгружаемая хронология всего по клиенту.
      const tl = $(".u-timeline", box);
      const more = $(".u-tl-more", box);
      const ICO = { signup: "→", payment: "₽", ref: "🤝", project: "▣",
                    track: "♪", admin: "⚙" };
      let cursor = "";
      const loadTl = async () => {
        const r = await api(`/api/admin/users/${uid}/timeline`
          + (cursor ? "?before=" + encodeURIComponent(cursor) : ""));
        if (!cursor) tl.innerHTML = "";
        (r.items || []).forEach((it) => {
          const row = document.createElement("div");
          row.className = "u-tl-row";
          const ico = ICO[(it.kind || "").split(":")[0]] || "⚡";
          row.innerHTML = `
            <span class="u-tl-ico">${ico}</span>
            ${it.thumb ? `<img src="${esc(it.thumb)}" loading="lazy">` : ""}
            <div class="u-tl-body">
              <b>${esc(it.title)}</b>
              ${it.sub ? `<span class="muted">${esc(it.sub)}</span>` : ""}
            </div>
            <span class="u-tl-amt">${esc(it.amount || "")}</span>
            <span class="muted u-tl-when">${when(it.at)}</span>`;
          tl.appendChild(row);
        });
        if (!tl.childElementCount) tl.innerHTML = '<p class="muted">пока пусто</p>';
        cursor = r.next_before || "";
        more.classList.toggle("hidden", !cursor);
      };
      more.addEventListener("click", loadTl);
      loadTl().catch((e) => { tl.innerHTML = `<p class="adm-err">${esc(e.message || e)}</p>`; });
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── рассылка ─────────────────────────── */
  async function renderBroadcast(box) {
    try {
      const [seg, camps] = await Promise.all([
        api("/api/admin/segments"), api("/api/admin/campaigns"),
      ]);
      box.innerHTML = `
        <div class="adm-card">
          <h3>Сегменты</h3>
          <table class="adm-table">
            <thead><tr><th>сегмент</th><th class="num">людей</th>
              <th class="num">в приложение</th><th class="num">telegram</th>
              <th class="num">почта</th></tr></thead>
            <tbody>${seg.segments.map((s) => `<tr>
              <td><b>${esc(s.id)}</b><br /><span class="muted">${esc(s.title)}</span></td>
              <td class="num">${num(s.count)}</td>
              <td class="num">${num(s.reach.inapp)}</td>
              <td class="num">${num(s.reach.tg)}</td>
              <td class="num">${num(s.reach.email)}</td></tr>`).join("")}</tbody>
          </table>
          <p class="adm-note">Второе число важнее первого: «сегмент 900 человек» ничего
            не значит, если почты нет ни у кого.</p>
        </div>
        <div class="adm-card">
          <h3>Написать</h3>
          <div class="adm-fields">
            <div class="adm-row">
              <input type="text" class="c-title" placeholder="название (видно только тебе)" style="flex:1;min-width:200px" />
              <select class="c-channel">${(camps.channels
                ? Object.keys(camps.channels) : ["inapp"]).map((ch) => {
                  const st = (camps.channels || {})[ch] || {};
                  return `<option value="${esc(ch)}" ${st.enabled ? "" : "disabled"}
                    >${esc(ch)}${st.enabled ? "" : " — не подключён"}</option>`;
                }).join("")}</select>
              <select class="c-segment">${seg.segments.map((sg) =>
                `<option value="${esc(sg.id)}">${esc(sg.id)} — ${num(sg.count)}</option>`).join("")}</select>
            </div>
            <div class="adm-field">
              <label>Тема (для почты)</label>
              <input type="text" class="c-subject" style="width:100%" />
            </div>
            <div class="adm-field">
              <label>Текст. {name} подставит имя человека.</label>
              <textarea class="c-body" rows="6"></textarea>
            </div>
            <div class="adm-row">
              <label class="adm-pill"><input type="checkbox" class="c-trans" />
                по делу (отписки не глушат)</label>
              <button type="button" class="c-test">Сначала себе</button>
              <button type="button" class="primary c-send">Отправить сегменту</button>
              <span class="c-msg"></span>
            </div>
            <p class="adm-note">Порядок кнопок = порядок работы: сначала посмотреть на
              себе, потом отправлять людям. Канал без транспорта не притворяется
              отправленным — он честно говорит, чего не хватает.</p>
          </div>
        </div>
        <div class="adm-card">
          <h3>Рассылки</h3>
          ${(camps.items || camps.campaigns || []).length ? `<table class="adm-table">
            <thead><tr><th>что</th><th>канал</th><th>сегмент</th><th>статус</th>
              <th class="num">ушло</th><th class="num">прочитано</th></tr></thead>
            <tbody>${(camps.items || camps.campaigns || []).map((c) => `<tr>
              <td><b>${esc(c.title)}</b><br /><span class="muted">${when(c.at)}</span></td>
              <td>${esc(c.channel)}</td><td>${esc(c.segment)}</td>
              <td>${esc(c.status)}${c.error ? `<br /><span class="adm-err">${esc(c.error)}</span>` : ""}</td>
              <td class="num">${num(c.sent)} / ${num(c.total)}</td>
              <td class="num">${num(c.read)}</td></tr>`).join("")}</tbody></table>`
            : '<p class="muted">рассылок ещё не было</p>'}
        </div>`;
      const msg = $(".c-msg", box);
      const make = async () => {
        const c = await api("/api/admin/campaigns", {
          method: "POST",
          body: {
            title: $(".c-title", box).value.trim(),
            channel: $(".c-channel", box).value,
            segment: $(".c-segment", box).value,
            subject: $(".c-subject", box).value.trim(),
            body: $(".c-body", box).value,
            transactional: $(".c-trans", box).checked,
          },
        });
        return c.id;
      };
      const run = async (test) => {
        msg.className = "c-msg muted";
        msg.textContent = "…";
        try {
          const id = await make();
          const r = await api(`/api/admin/campaigns/${id}/send${test ? "?test=1" : ""}`,
                              { method: "POST" });
          msg.className = "c-msg adm-ok";
          msg.textContent = test ? "ушло тебе" : `поставлено в очередь: ${num(r.total || 0)}`;
          if (!test) setTimeout(() => open("broadcast"), 1200);
        } catch (e) {
          msg.className = "c-msg adm-err";
          msg.textContent = e.message;
        }
      };
      $(".c-test", box).addEventListener("click", () => run(true));
      $(".c-send", box).addEventListener("click", () => {
        const seg = $(".c-segment", box).value;
        if (!confirm(`Отправить всем в сегменте «${seg}»?`)) return;
        run(false);
      });
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── выплаты ─────────────────────────── */
  async function renderPayouts(box) {
    try {
      const d = await api("/api/admin/payouts");
      const items = d.items || d.payouts || [];
      box.innerHTML = `<div class="adm-card">
        ${items.length ? `<table class="adm-table">
          <thead><tr><th>когда</th><th>кому</th><th class="num">сумма</th>
            <th>реквизиты</th><th>статус</th></tr></thead>
          <tbody>${items.map((p) => `<tr>
            <td>${when(p.created_at)}</td>
            <td>${esc((p.ambassador && p.ambassador.name) || ("#" + (p.ambassador_id || "")))}
              ${p.ambassador && p.ambassador.tg ? `<br /><span class="muted">@${esc(p.ambassador.tg)}</span>` : ""}</td>
            <td class="num">${num(Math.round((p.amount_kopeks || 0) / 100))} ₽</td>
            <td>${esc(p.details || "")}</td>
            <td>${esc(p.status)}</td></tr>`).join("")}</tbody></table>`
          : '<p class="muted">заявок нет</p>'}
      </div>`;
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── журнал ─────────────────────────── */
  async function renderLedger(box) {
    try {
      const d = await api("/api/admin/ledger/audit?limit=200");
      box.innerHTML = `
        <div class="adm-grid">
          ${stat(num(d.checked), "проверено человек")}
          ${stat(num(d.mismatch), "расхождений")}
          ${stat(num(d.free_start) + " ⚡", "стартовая норма FREE")}
        </div>
        <div class="adm-card">
          ${d.items.length ? `<table class="adm-table">
            <thead><tr><th>кто</th><th class="num">баланс</th>
              <th class="num">по журналу</th><th class="num">разница</th></tr></thead>
            <tbody>${d.items.map((r) => `<tr>
              <td>${esc(r.name)}</td><td class="num">${num(r.points)}</td>
              <td class="num">${num(r.journal)}</td>
              <td class="num">${num(r.diff)}</td></tr>`).join("")}</tbody></table>`
            : '<p class="adm-ok">Расхождений нет: каждый токен прошёл через журнал.</p>'}
        </div>`;
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── стили ───────────────────────────
     Промпт, референсы, файлы с промптами и сценарная база — то, что просил
     владелец. Закрытые поля приезжают ТОЛЬКО сюда и только по явному
     запросу карточки; списком они не ходят. */
  /* ─────────── партнёрские продукты (витрина «Заработок») ───────────
     Тот же TrendPreset, но kind="earn": продукт встроен в шаблон, партнёр
     подставляет только свой стиль. Фото товара — постер шаблона. */
  async function renderEarnAdmin(box) {
    earnHost = box;
    let d;
    try { d = await api("/api/admin/trends"); }
    catch (e) { return fail(box, e); }
    const rows = (d.presets || []).filter((x) => (x.kind || "trend") === "earn");
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <h3>Партнёрские продукты <span class="adm-count">${rows.length}</span></h3>
          <span style="flex:1"></span>
          <button type="button" class="primary e-new">+ продукт</button>
        </div>
        <p class="adm-note">Награда партнёру считается от заказа и платится за ПЕРВУЮ
          покупку клиента. Фото товара = постер карточки на витрине.</p>
        <div class="e-list"></div>
      </div>`;
    const list = $(".e-list", box);
    const draw = (t) => {
      const card = document.createElement("div");
      card.className = "adm-card e-item";
      card.innerHTML = `
        <div class="adm-row">
          <div class="e-shot">${t.poster_url
            ? `<img src="${esc(t.poster_url)}" alt="" />`
            : '<span class="e-ph">📦</span>'}</div>
          <div class="adm-fields" style="flex:1">
            <div class="adm-row">
              <div class="adm-field" style="flex:1"><label>Название</label>
                <input class="e-title" value="${esc(t.title || "")}" /></div>
              <div class="adm-field"><label>Награда, %</label>
                <input class="e-pct" type="number" min="1" max="50"
                       value="${Number(t.reward_pct || 10)}" style="width:90px" /></div>
              <div class="adm-field"><label>Показывать</label>
                <input class="e-on" type="checkbox" ${t.enabled ? "checked" : ""} /></div>
            </div>
            <div class="adm-row">
              <div class="adm-field" style="flex:1"><label>Подпись награды</label>
                <input class="e-note" value="${esc(t.reward_note || "")}" /></div>
              <div class="adm-field" style="flex:1"><label>Ссылка на товар</label>
                <input class="e-url" value="${esc(t.landing_url || "")}" /></div>
            </div>
            <div class="adm-row">
              <label class="e-upload">фото товара
                <input type="file" class="e-file" accept="image/*" hidden /></label>
              <button type="button" class="primary e-save">Сохранить</button>
              <button type="button" class="ghost danger e-del">Удалить</button>
              <span class="e-msg"></span>
            </div>
          </div>
        </div>`;
      const msg = $(".e-msg", card);
      $(".e-save", card).addEventListener("click", async () => {
        msg.textContent = "сохраняю…";
        try {
          await api("/api/admin/trends", { method: "POST", body: {
            id: t.id, kind: "earn",
            title: $(".e-title", card).value,
            reward_note: $(".e-note", card).value,
            reward_pct: Number($(".e-pct", card).value || 10),
            landing_url: $(".e-url", card).value,
            enabled: $(".e-on", card).checked } });
          msg.className = "e-msg adm-ok";
          msg.textContent = "сохранено";
        } catch (e) { msg.className = "e-msg adm-err"; msg.textContent = e.message; }
      });
      $(".e-file", card).addEventListener("change", async (e) => {
        const f = e.target.files && e.target.files[0];
        if (!f) return;
        msg.className = "e-msg";
        msg.textContent = "загружаю фото…";
        const fd = new FormData();
        fd.append("file", f);
        try {
          await api(`/api/admin/trends/${t.id}/media?kind=poster`,
                    { method: "POST", body: fd });
          renderEarnAdmin(earnHost || pane());
        } catch (err) { msg.className = "e-msg adm-err"; msg.textContent = err.message; }
      });
      $(".e-del", card).addEventListener("click", async () => {
        if (!confirm("Удалить продукт с витрины?")) return;
        try {
          await api(`/api/admin/trends/${t.id}`, { method: "DELETE" });
          renderEarnAdmin(earnHost || pane());
        } catch (e) { alert(e.message); }
      });
      return card;
    };
    rows.forEach((t) => list.appendChild(draw(t)));
    if (!rows.length) list.innerHTML = '<p class="muted">продуктов пока нет</p>';
    $(".e-new", box).addEventListener("click", async () => {
      const title = prompt("Название продукта");
      if (!title) return;
      try {
        await api("/api/admin/trends", { method: "POST", body: {
          kind: "earn", title, reward_pct: 10, reward_note: "10% с заказа",
          landing_url: "https://bioura.io", enabled: true,
          image_prompt: "the exact product from the reference photo, held by the "
            + "person from the second reference, lifestyle shot, natural light",
          motion_prompt: "the person shows the product to the camera, subtle motion",
          duration_sec: 6, aspect: "9:16" } });
        renderEarnAdmin(earnHost || pane());
      } catch (e) { alert(e.message); }
    });
  }

  /* ══════════════════════════ ПРОМТЫ ══════════════════════════
     ОДНА страница на всю настройку промтов. Раньше «Стили» и «Референсы»
     были отдельными пунктами меню, тренды правились из «Заработка», а
     шаблоны мокапов не правились вообще — и на вопрос «где меняется текст,
     который уходит в модель» было четыре разных ответа. Теперь ответ один.

     ВКЛАДКИ — СЕГМЕНТ-КАПСУЛА, А НЕ ОГОНЬ. Огненная заливка в каноне
     означает «главное действие листа», и пять огненных вкладок сверху
     означали бы пять главных действий, то есть ни одного. Активный сегмент
     показан вставкой и яркой волосяной рамкой. */
  const P_TABS = [
    ["layers", "Слои промтов"], ["styles", "Стили"], ["refs", "Референсы"],
    ["trends", "Тренды"], ["mockups", "Шаблоны мокапов"],
  ];
  let promptTab = "layers";

  function renderPrompts(box) {
    box.innerHTML = `<div class="adm-seg p-seg"></div><div class="p-sub"></div>`;
    const seg = $(".p-seg", box);
    P_TABS.forEach(([id, title]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = id === promptTab ? "on" : "";
      b.textContent = title;
      b.addEventListener("click", () => {
        promptTab = id;
        open("prompts");
      });
      seg.appendChild(b);
    });
    const sub = $(".p-sub", box);
    loading(sub);
    if (promptTab === "styles") renderStyles(sub, "style");
    else if (promptTab === "refs") renderStyles(sub, "reference");
    else if (promptTab === "trends") renderTrends(sub);
    else if (promptTab === "mockups") renderMockups(sub);
    else renderLayers(sub);
  }

  /* ───────────────── слои каталога: сценарии · сцены · движение · свет ─────
     Каталог остаётся кодом (backend/prompts_library.py): его видно в
     git-истории и проверяет validate(). Здесь правится НАЛОЖЕНИЕ поверх
     файла — оно лежит в app_settings и подмешивается в выдачу пользователю.
     Поэтому у заводской карточки есть «вернуть заводскую», а не «удалить». */

  //: Человеческие подписи полей. Ключи приходят с сервера (EDITABLE),
  //: и незнакомое поле не ломает форму — оно подписывается своим ключом.
  const P_FIELD = {
    label: "Название", desc: "Описание", tier: "Тариф", group: "Группа",
    cut: "Темп монтажа (slow / mid / fast)", level: "Уровень (scene / grade)",
    shot: "Крупность плана", camera: "Движение камеры",
    bracket: "Команда в скобках (MiniMax)", note: "Подпись кадра",
    music: "Под какую музыку", logline: "Логлайн", hero: "Герой",
    motif: "Сквозной мотив", opens: "Открывается", closes: "Закрывается",
    physics: "Физика движения",
    first: "Первый кадр", last: "Последний кадр", motion: "Движение",
    solo: "Grok — один кадр", negative: "Запреты",
    text: "Движение (пара кадров)", add: "Хвост света",
    story: "Сюжет — уходит в модель", dnote: "Заметка режиссёру",
    anim: "Анимация карточки (slider / vehicle / drone / truck / orbit / crane)",
  };

  let layerKind = "boards";
  let layerKey = null;
  let layerQuery = "";

  async function renderLayers(box) {
    let d;
    try { d = await api("/api/admin/prompts"); }
    catch (e) { return fail(box, e); }
    const spec = (d.layers || []).find((l) => l.key === layerKind) || d.layers[0];
    layerKind = spec.key;
    let list;
    try { list = await api("/api/admin/prompts/" + layerKind); }
    catch (e) { return fail(box, e); }

    box.innerHTML = `
      <div class="adm-seg l-kinds" style="margin-bottom:12px"></div>
      <div class="adm-split">
        <div class="adm-card">
          <h3>${esc(spec.title)} <span class="adm-count">${(list.items || []).length}</span></h3>
          <input type="search" class="l-search" placeholder="поиск по названию и ключу…"
                 value="${esc(layerQuery)}" />
          <div class="adm-list l-list"></div>
          <button type="button" class="ghost l-new" style="margin-top:8px">+ карточка</button>
          <div class="adm-row" style="margin-top:8px;flex-wrap:wrap">
            ${layerKind === "cameras" ? '<button type="button" class="ghost l-cam-seed">📥 Разложить кадры Тони</button>' : ""}
            <button type="button" class="ghost l-cam-gen">🖼 Догенерить превью всем (⚡0)</button>
            <span class="l-cam-msg muted"></span>
          </div>
        </div>
        <div class="l-editor"></div>
      </div>`;

    const kinds = $(".l-kinds", box);
    (d.layers || []).forEach((l) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = l.key === layerKind ? "on" : "";
      b.textContent = `${l.title} · ${l.count}`;
      b.addEventListener("click", () => {
        layerKind = l.key; layerKey = null; layerQuery = "";
        renderLayers(box);
      });
      kinds.appendChild(b);
    });

    const listBox = $(".l-list", box);
    const paint = () => {
      const q = layerQuery.trim().toLowerCase();
      listBox.innerHTML = "";
      (list.items || [])
        .filter((it) => !q || (it.label || "").toLowerCase().includes(q)
          || it.key.toLowerCase().includes(q))
        .forEach((it) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "adm-item" + (it.key === layerKey ? " on" : "");
          b.innerHTML = `<span>${esc(it.label || it.key)}</span>
            <span class="adm-mark">${it.hidden ? "скрыт" : it.overridden ? "изменён" : ""}${
              it.builtin ? "" : " свой"}</span>`;
          b.addEventListener("click", () => { layerKey = it.key; renderLayers(box); });
          listBox.appendChild(b);
        });
      if (!listBox.children.length)
        listBox.innerHTML = '<span class="muted" style="padding:8px">ничего не нашлось</span>';
    };
    paint();
    const search = $(".l-search", box);
    search.addEventListener("input", () => { layerQuery = search.value; paint(); });

    $(".l-new", box).addEventListener("click", async () => {
      const key = prompt("Ключ новой карточки (латиница, цифры, подчёркивание)");
      if (!key) return;
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(key.trim().toLowerCase())}`,
                  { method: "PUT", body: { label: { ru: key, en: key } } });
        layerKey = key.trim().toLowerCase();
        renderLayers(box);
      } catch (e) { alert(e.message); }
    });

    const camSeed = $(".l-cam-seed", box);
    if (camSeed) camSeed.addEventListener("click", async () => {
      const m = $(".l-cam-msg", box);
      m.textContent = "раскладываю…";
      try {
        const r = await api("/api/admin/cameras/previews".replace("previews", "seed-refs"), { method: "POST" });
        m.textContent = "готово: " + JSON.stringify(r.seeded);
        renderLayers(box);
      } catch (e) { m.textContent = "не вышло: " + (e.message || e); }
    });
    const camGen = $(".l-cam-gen", box);
    if (camGen) camGen.addEventListener("click", async () => {
      const m = $(".l-cam-msg", box);
      m.textContent = "генерю недостающие превью слоя (минуты)…";
      try {
        const r = await api(`/api/admin/prompts/${layerKind}/previews`, { method: "POST" });
        m.textContent = `готово: ${(r.done || []).length}, ошибок: ${(r.failed || []).length}`;
        renderLayers(box);
      } catch (e) { m.textContent = "не вышло: " + (e.message || e); }
    });

    const ed = $(".l-editor", box);
    if (layerKey && (list.items || []).some((it) => it.key === layerKey)) {
      layerEditor(ed, box);
    } else {
      layerKey = null;
      ed.innerHTML = `<div class="adm-card s-empty">
        <span class="s-empty-ico">🎬</span><b>Выбери карточку слева</b>
        <span class="muted">Правки ложатся наложением поверх файла каталога:
          заводской текст никуда не девается и возвращается одной кнопкой.</span>
      </div>`;
    }
  }

  // Движки с живыми ценами — один раз на сессию админки. Кадровые идут в
  // «Сгенерировать превью», видео — в «Анимировать превью».
  let admEnginesCache = null;
  async function admEngines() {
    if (admEnginesCache) return admEnginesCache;
    try {
      const d = await api("/api/providers");
      admEnginesCache = {
        images: (d.images || []).filter((e) => e.live),
        videos: (d.engines || []).filter((e) => e.live),
      };
    } catch (e) {
      admEnginesCache = { images: [], videos: [] };
    }
    return admEnginesCache;
  }

  async function layerEditor(box, host) {
    loading(box);
    let c, eng;
    try {
      c = await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}`);
      eng = await admEngines();
    } catch (e) { return fail(box, e); }

    const isPrompt = (f) => (c.prompt_fields || []).includes(f);
    const isBi = (f) => (c.bilingual || []).includes(f);
    const field = (f) => {
      const v = c.value[f];
      const lab = esc(P_FIELD[f] || f);
      if (isBi(f)) {
        const o = v && typeof v === "object" ? v : {};
        return `<div class="adm-field"><label>${lab}</label>
          <div class="adm-row">
            <textarea class="l-f" data-f="${f}" data-lang="ru" rows="2"
              placeholder="RU">${esc(o.ru || "")}</textarea>
            <textarea class="l-f" data-f="${f}" data-lang="en" rows="2"
              placeholder="EN">${esc(o.en || "")}</textarea>
          </div></div>`;
      }
      if (f === "tier") {
        return `<div class="adm-field"><label>${lab}</label>
          <select class="l-f" data-f="tier">
            <option value="free" ${v === "free" ? "selected" : ""}>FREE</option>
            <option value="pro" ${v === "pro" ? "selected" : ""}>PRO</option>
          </select></div>`;
      }
      if (f === "group" && (c.groups || []).length) {
        return `<div class="adm-field"><label>${lab}</label>
          <select class="l-f" data-f="group">${c.groups.map((g) =>
            `<option value="${esc(g.key)}" ${g.key === v ? "selected" : ""}
              >${esc(g.label)}</option>`).join("")}</select></div>`;
      }
      if (isPrompt(f)) {
        // Русский исходник — главное поле, английский под ним и сворачиваемый.
        // В модель уходит английский, но правит владелец по-русски.
        const rv = (c.ru || {})[f] || "";
        return `<div class="adm-field l-pf" data-pf="${f}">
          <label>${lab} — по-русски</label>
          <textarea class="l-ru" data-f="${f}" rows="4"
            placeholder="правь по-русски — переведём сами">${esc(rv)}</textarea>
          <div class="adm-row" style="margin:6px 0">
            <button type="button" class="ghost l-toggle" data-f="${f}">Показать английский</button>
            <span class="l-tr-msg muted" data-f="${f}"></span>
          </div>
          <div class="l-en-box hidden" data-f="${f}">
            <label>Английский — этот текст уходит в модель</label>
            <textarea class="l-f" data-f="${f}" rows="4">${esc(v || "")}</textarea>
          </div></div>`;
      }
      return `<div class="adm-field"><label>${lab}</label>
        <input type="text" class="l-f" data-f="${f}" value="${esc(v || "")}" /></div>`;
    };

    // БАГ-ФИКС: filter без map(field) выводил СКЛЕЕННЫЕ ключи полей
    // («labeldesctiergroup…») вместо полей с подписями и инпутами.
    const card = (c.fields || []).filter((f) => !isPrompt(f)).map(field);
    const prompts = (c.fields || []).filter(isPrompt).map(field);
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <b>${esc((c.value.label && c.value.label.ru) || c.key)}</b>
          <span class="adm-pill">${esc(c.key)}</span>
          ${c.builtin ? '<span class="adm-pill">заводская</span>'
                      : '<span class="adm-pill">своя</span>'}
          ${c.overridden ? '<span class="adm-pill warn">изменена</span>' : ""}
          ${c.hidden ? '<span class="adm-pill warn">скрыта с витрины</span>' : ""}
          <span style="flex:1"></span>
          <span class="l-msg"></span>
        </div>
        <div class="adm-fields" style="margin-top:12px">
          <div class="adm-field"><label>Превью карточки</label>
            <div class="adm-row" style="align-items:center">
              ${c.anim_url
                ? `<video src="${esc(c.anim_url)}" muted loop autoplay playsinline
                     style="width:96px;aspect-ratio:3/4;object-fit:cover;border-radius:10px"></video>`
                : c.preview_url
                  ? `<img src="${esc(c.preview_url)}" style="width:96px;aspect-ratio:3/4;object-fit:cover;border-radius:10px">`
                  : '<span class="muted">превью нет — загрузи или сгенерируй</span>'}
              <label class="ghost" style="cursor:pointer;padding:6px 10px;border:1px solid var(--adm-border,#ccc);border-radius:8px">
                Загрузить свою картинку
                <input type="file" class="l-prev-up" accept="image/*" style="display:none">
              </label>
              <span class="l-prev-msg muted"></span>
            </div>
            <div class="adm-row" style="align-items:center;margin-top:6px;flex-wrap:wrap">
              <select class="l-eng-img" title="движок кадра">
                <option value="chatgpt">кадр: шлюз ⚡0</option>
                ${eng.images.filter((e) => e.id !== "chatgpt").map((e) =>
                  `<option value="${esc(e.id)}">кадр: ${esc(e.title)} ⚡${e.frames_cost || 0}</option>`).join("")}
              </select>
              <button type="button" class="ghost l-prev-gen">Сгенерировать превью ⚡0</button>
            </div>
            <div class="adm-row" style="align-items:center;margin-top:6px;flex-wrap:wrap">
              <select class="l-eng-vid" title="видео-движок">
                <option value="grok">видео: шлюз Grok ⚡0</option>
                ${eng.videos.filter((e) => e.id !== "grok").map((e) =>
                  `<option value="${esc(e.id)}">видео: ${esc(e.title)} ⚡${e.video_cost || 0}</option>`).join("")}
              </select>
              <button type="button" class="ghost l-prev-anim" ${c.preview_url ? "" : "disabled title=\"сначала нужна превью-картинка\""}>Анимировать превью ⚡0</button>
            </div>
            ${(c.preview_gallery || []).length > 1 ? `
            <div class="adm-row l-gallery" style="flex-wrap:wrap;margin-top:6px">
              ${c.preview_gallery.map((g) =>
                `<img src="${esc(g.url)}" data-fn="${esc(g.filename)}"
                   title="сделать главной"
                   style="width:54px;aspect-ratio:3/4;object-fit:cover;border-radius:8px;cursor:pointer;${
                     c.preview_url === g.url ? "outline:2px solid #c1401b;" : "opacity:.75;"}">`).join("")}
            </div>` : ""}
          </div>
          ${card.join("")}
          <div class="adm-field"><label>Текст, который уходит в модель</label></div>
          ${prompts.join("")}
          <div class="adm-row">
            <button type="button" class="primary l-save">Сохранить</button>
            ${c.builtin && c.overridden
              ? '<button type="button" class="ghost l-reset">Вернуть заводскую</button>' : ""}
            ${c.builtin
              ? `<button type="button" class="ghost l-hide">${
                  c.hidden ? "Вернуть на витрину" : "Скрыть с витрины"}</button>`
              : '<button type="button" class="ghost danger l-del">Удалить карточку</button>'}
          </div>
          <p class="adm-note">Правится наложение, а не файл: связи карточек
            (сочетается / конфликтует / подходящие стили) наложением не трогаются —
            разъехавшаяся ссылка ломает сборку молча. Их меняют коммитом.</p>
        </div>
      </div>`;

    const msg = $(".l-msg", box);
    const prevUp = $(".l-prev-up", box);
    if (prevUp) prevUp.addEventListener("change", async () => {
      const f = prevUp.files && prevUp.files[0];
      prevUp.value = "";
      if (!f) return;
      const m = $(".l-prev-msg", box);
      m.textContent = "загружаю…";
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await fetch(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}/preview`,
                              { method: "POST", body: fd });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
        layerEditor(box, host);
      } catch (e) { m.textContent = "не вышло: " + (e.message || e); }
    });
    // Цена на кнопках живёт от выбранного движка; дефолт — бесплатный шлюз.
    const engImg = $(".l-eng-img", box);
    const engVid = $(".l-eng-vid", box);
    const prevGen = $(".l-prev-gen", box);
    const prevAnim = $(".l-prev-anim", box);
    const engCost = (sel, list, field) => {
      const e = (list || []).find((x) => x.id === sel.value);
      return e ? (e[field] || 0) : 0;
    };
    const syncCosts = () => {
      if (engImg && prevGen)
        prevGen.textContent = `Сгенерировать превью ⚡${engCost(engImg, eng.images, "frames_cost")}`;
      if (engVid && prevAnim)
        prevAnim.textContent = `Анимировать превью ⚡${engCost(engVid, eng.videos, "video_cost")}`;
    };
    if (engImg) engImg.addEventListener("change", syncCosts);
    if (engVid) engVid.addEventListener("change", syncCosts);
    syncCosts();
    if (prevGen) prevGen.addEventListener("click", async () => {
      const m = $(".l-prev-msg", box);
      prevGen.disabled = true;
      m.textContent = "генерю по промпту карточки…";
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}/preview-generate`,
                  { method: "POST", body: { engine: engImg ? engImg.value : "chatgpt" } });
        layerEditor(box, host);
      } catch (e) { m.textContent = "не вышло: " + (e.message || e); prevGen.disabled = false; }
    });
    if (prevAnim) prevAnim.addEventListener("click", async () => {
      const m = $(".l-prev-msg", box);
      prevAnim.disabled = true;
      m.textContent = "оживляю промптом движения карточки… (до пары минут)";
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}/preview-animate`,
                  { method: "POST", body: { engine: engVid ? engVid.value : "grok" } });
        layerEditor(box, host);
      } catch (e) { m.textContent = "не вышло: " + (e.message || e); prevAnim.disabled = false; }
    });
    $$(".l-gallery img", box).forEach((im) => im.addEventListener("click", async () => {
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}/preview-main`,
                  { method: "POST", body: { filename: im.dataset.fn } });
        layerEditor(box, host);
      } catch (e) { alert(e.message || e); }
    }));
    const enTouched = new Set();
    $$(".l-en-box .l-f", box).forEach((el) =>
      el.addEventListener("input", () => enTouched.add(el.dataset.f)));
    $$(".l-toggle", box).forEach((b) => b.addEventListener("click", () => {
      const bx = $(`.l-en-box[data-f="${b.dataset.f}"]`, box);
      bx.classList.toggle("hidden");
      b.textContent = bx.classList.contains("hidden")
        ? "Показать английский" : "Скрыть английский";
    }));
    // «Перевести → EN» убрана: перевод происходит сам при сохранении,
    // когда русский текст правлен, а английский не трогали руками.
    const collect = () => {
      const out = { translate: [] };
      $$(".l-f", box).forEach((el) => {
        const f = el.dataset.f;
        if (el.dataset.lang) {
          out[f] = out[f] || { ru: "", en: "" };
          out[f][el.dataset.lang] = el.value;
        } else {
          out[f] = el.value;
        }
      });
      $$(".l-ru", box).forEach((el) => {
        const f = el.dataset.f;
        out[f + "_ru"] = el.value;
        if (el.value.trim() && !enTouched.has(f)) out.translate.push(f);
      });
      return out;
    };
    $(".l-save", box).addEventListener("click", async () => {
      const patch = collect();
      msg.className = "l-msg muted";
      msg.textContent = patch.translate.length ? "перевожу и сохраняю…" : "сохраняю…";
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}`,
                  { method: "PUT", body: patch });
        renderLayers(host);
      } catch (e) { msg.className = "l-msg adm-err"; msg.textContent = e.message; }
    });
    const reset = $(".l-reset", box);
    if (reset) reset.addEventListener("click", async () => {
      if (!confirm("Снять все правки и вернуть заводскую карточку?")) return;
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}`,
                  { method: "DELETE" });
        renderLayers(host);
      } catch (e) { alert(e.message); }
    });
    const hide = $(".l-hide", box);
    if (hide) hide.addEventListener("click", async () => {
      try {
        if (c.hidden) {
          await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}`,
                    { method: "PUT", body: {} });
        } else {
          if (!confirm("Убрать карточку из каталога у пользователей?")) return;
          await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}?hide=1`,
                    { method: "DELETE" });
        }
        renderLayers(host);
      } catch (e) { alert(e.message); }
    });
    const del = $(".l-del", box);
    if (del) del.addEventListener("click", async () => {
      if (!confirm("Удалить свою карточку совсем?")) return;
      try {
        await api(`/api/admin/prompts/${layerKind}/${encodeURIComponent(layerKey)}`,
                  { method: "DELETE" });
        layerKey = null;
        renderLayers(host);
      } catch (e) { alert(e.message); }
    });
  }

  /* ─────────────────────────── тренды ───────────────────────────
     Тот же TrendPreset, что и в «Заработке», но kind != "earn": шаблон,
     в который человек подставляет своё фото. Партнёрские продукты сюда не
     попадают — у них своя вкладка со своими полями награды. */
  async function renderTrends(box) {
    let d;
    try { d = await api("/api/admin/trends"); }
    catch (e) { return fail(box, e); }
    const rows = (d.presets || []).filter((x) => (x.kind || "trend") !== "earn");
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <h3>Шаблоны трендов <span class="adm-count">${rows.length}</span></h3>
          <span style="flex:1"></span>
          <button type="button" class="ghost t-ru-all">Перевести каталог на русский</button>
          <button type="button" class="primary t-new">+ тренд</button>
        </div>
        <p class="adm-note">Правь ПО-РУССКИ: в модель уходит английский перевод,
          он пересобирается сам при сохранении. Постер — то, что человек видит
          на витрине /trends до генерации: прикрепи свой или сгенерируй по
          промпту кадра.</p>
        <div class="t-list"></div>
      </div>`;
    const list = $(".t-list", box);
    rows.forEach((t) => {
      const card = document.createElement("div");
      card.className = "adm-card";
      card.innerHTML = `
        <div class="adm-row" style="align-items:flex-start">
          <div class="e-side">
            <div class="e-shot">${t.sample_url
              ? `<video src="${esc(t.sample_url)}" muted loop autoplay playsinline></video>`
              : t.poster_url
                ? `<img src="${esc(t.poster_url)}" alt="" />`
                : '<span class="e-ph">🎞️</span>'}</div>
            <button type="button" class="ghost t-anim" ${t.poster_url ? "" : "disabled"}
              title="оживить превью его motion-промптом через бесплатный шлюз">
              Анимировать ⚡0</button>
          </div>
          <div class="adm-fields" style="flex:1">
            <div class="adm-row">
              <div class="adm-field" style="flex:1"><label>Название</label>
                <input class="t-title" value="${esc(t.title || "")}" /></div>
              <div class="adm-field"><label>Секунд</label>
                <input class="t-dur" type="number" min="2" max="12"
                       value="${Number(t.duration_sec || 6)}" style="width:80px" /></div>
              <div class="adm-field"><label>Кадр</label>
                <input class="t-aspect" value="${esc(t.aspect || "9:16")}" style="width:80px" /></div>
              <div class="adm-field"><label>Показывать</label>
                <input class="t-on" type="checkbox" ${t.enabled ? "checked" : ""} /></div>
            </div>
            <div class="adm-field"><label>Промпт кадра — по-русски</label>
              <textarea class="t-img-ru" rows="3"
                placeholder="${t.image_prompt && !t.image_prompt_ru ? "русского ещё нет — нажми «Перевести каталог на русский» сверху" : "правь по-русски — переведём сами"}">${esc(t.image_prompt_ru || "")}</textarea></div>
            <div class="adm-field"><label>Промпт движения — по-русски</label>
              <textarea class="t-mot-ru" rows="3"
                placeholder="${t.motion_prompt && !t.motion_prompt_ru ? "русского ещё нет — нажми «Перевести каталог на русский» сверху" : "правь по-русски — переведём сами"}">${esc(t.motion_prompt_ru || "")}</textarea></div>
            <details class="t-en-box">
              <summary class="muted">английский (уходит в модель) — для сверки</summary>
              <div class="adm-field"><label>Название (EN, витрина)</label>
                <input class="t-title-en" value="${esc(t.title_en || "")}" /></div>
              <div class="adm-field"><label>Промпт кадра (EN)</label>
                <textarea class="t-img" rows="3">${esc(t.image_prompt || "")}</textarea></div>
              <div class="adm-field"><label>Промпт движения (EN)</label>
                <textarea class="t-mot" rows="3">${esc(t.motion_prompt || "")}</textarea></div>
            </details>
            <div class="adm-row">
              <label class="e-upload">прикрепить постер
                <input type="file" class="t-file" accept="image/*" hidden /></label>
              <button type="button" class="ghost t-prev">Сгенерировать превью ⚡0</button>
              <button type="button" class="primary t-save">Сохранить</button>
              <button type="button" class="ghost danger t-del">Удалить</button>
              <span class="t-msg"></span>
            </div>
          </div>
        </div>`;
      const msg = $(".t-msg", card);
      let tEnTouched = false;
      [$(".t-img", card), $(".t-mot", card)].forEach((el) =>
        el.addEventListener("input", () => { tEnTouched = true; }));
      $(".t-save", card).addEventListener("click", async () => {
        msg.className = "t-msg muted";
        const ruEdited = $(".t-img-ru", card).value.trim() || $(".t-mot-ru", card).value.trim();
        const body = {
          id: t.id, kind: t.kind || "trend",
          title: $(".t-title", card).value,
          title_en: $(".t-title-en", card).value,
          image_prompt_ru: $(".t-img-ru", card).value,
          motion_prompt_ru: $(".t-mot-ru", card).value,
          duration_sec: Number($(".t-dur", card).value || 6),
          aspect: $(".t-aspect", card).value,
          enabled: $(".t-on", card).checked,
        };
        // Английский правили руками — уважаем; нет — пересоберём из русского.
        if (tEnTouched || !ruEdited) {
          body.image_prompt = $(".t-img", card).value;
          body.motion_prompt = $(".t-mot", card).value;
        } else {
          body.translate = true;
        }
        msg.textContent = body.translate ? "перевожу и сохраняю…" : "сохраняю…";
        try {
          await api("/api/admin/trends", { method: "POST", body });
          msg.className = "t-msg adm-ok";
          msg.textContent = "сохранено";
        } catch (e) { msg.className = "t-msg adm-err"; msg.textContent = e.message; }
      });
      $(".t-anim", card).addEventListener("click", async () => {
        msg.className = "t-msg muted";
        msg.textContent = "оживляю превью (до 3 минут)…";
        $(".t-anim", card).disabled = true;
        try {
          await api(`/api/admin/trends/${t.id}/preview-animate`, { method: "POST" });
          renderTrends(box);
        } catch (e) {
          msg.className = "t-msg adm-err"; msg.textContent = e.message;
          $(".t-anim", card).disabled = false;
        }
      });
      $(".t-prev", card).addEventListener("click", async () => {
        msg.className = "t-msg muted";
        msg.textContent = "рисую превью по промпту кадра (до минуты)…";
        try {
          await api(`/api/admin/trends/${t.id}/preview-generate`, { method: "POST" });
          renderTrends(box);
        } catch (e) { msg.className = "t-msg adm-err"; msg.textContent = e.message; }
      });
      $(".t-file", card).addEventListener("change", async (e) => {
        const f = e.target.files && e.target.files[0];
        if (!f) return;
        msg.className = "t-msg muted";
        msg.textContent = "загружаю постер…";
        const fd = new FormData();
        fd.append("file", f);
        try {
          await api(`/api/admin/trends/${t.id}/media?kind=poster`, { method: "POST", body: fd });
          renderTrends(box);
        } catch (err) { msg.className = "t-msg adm-err"; msg.textContent = err.message; }
      });
      $(".t-del", card).addEventListener("click", async () => {
        if (!confirm("Удалить шаблон тренда?")) return;
        try {
          await api(`/api/admin/trends/${t.id}`, { method: "DELETE" });
          renderTrends(box);
        } catch (e) { alert(e.message); }
      });
      list.appendChild(card);
    });
    if (!rows.length) list.innerHTML = '<p class="muted">трендов пока нет</p>';
    $(".t-ru-all", box).addEventListener("click", async () => {
      const btn = $(".t-ru-all", box);
      btn.disabled = true;
      btn.textContent = "перевожу каталог (минуты)…";
      try {
        const r = await api("/api/admin/trends/translate-ru", { method: "POST" });
        alert(`Переведено записей: ${r.translated}${(r.failed || []).length ? "; ошибок: " + r.failed.length : ""}`);
        renderTrends(box);
      } catch (e) { alert(e.message); btn.disabled = false; btn.textContent = "Перевести каталог на русский"; }
    });
    $(".t-new", box).addEventListener("click", async () => {
      const title = prompt("Название тренда");
      if (!title) return;
      try {
        await api("/api/admin/trends", { method: "POST", body: {
          kind: "trend", title, enabled: false, duration_sec: 6, aspect: "9:16",
          image_prompt: "the person from the reference photo, ",
          motion_prompt: "subtle natural motion, the camera holds still" } });
        renderTrends(box);
      } catch (e) { alert(e.message); }
    });
  }

  /* ───────────────────── шаблоны мокапов ─────────────────────
     mockup_catalog.py — тот же приём наложения, что и у слоёв: файл остаётся
     источником, правки лежат в базе. Превью генерится НЕЙТРАЛЬНОЙ бутылкой:
     витрина показывает сцену, а не чей-то товар. */
  let mockQuery = "";
  let mockCat = "";

  async function renderMockups(box) {
    let d;
    try { d = await api("/api/admin/mockups"); }
    catch (e) { return fail(box, e); }
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <h3>Шаблоны мокапов <span class="adm-count">${(d.items || []).length}</span></h3>
          <input type="search" class="m-search" placeholder="поиск…"
                 value="${esc(mockQuery)}" style="flex:1;min-width:160px" />
          <select class="m-cat">
            <option value="">все категории</option>
            ${(d.categories || []).map((c) =>
              `<option value="${esc(c)}" ${c === mockCat ? "selected" : ""}>${esc(c)}</option>`).join("")}
          </select>
          <button type="button" class="primary m-new">+ шаблон</button>
        </div>
        <p class="adm-note">Промпт сцены уходит в модель после жёсткой охраны этикетки
          («тот самый товар с фото») — её дописывает сервер, сюда вставлять не нужно.</p>
        <div class="m-list"></div>
      </div>`;
    const list = $(".m-list", box);
    const q = mockQuery.trim().toLowerCase();
    const rows = (d.items || []).filter((t) => (!mockCat || t.category === mockCat)
      && (!q || (t.ru || "").toLowerCase().includes(q) || t.id.toLowerCase().includes(q)));
    rows.forEach((t) => {
      const card = document.createElement("div");
      card.className = "adm-card";
      card.innerHTML = `
        <div class="adm-row">
          <div class="e-shot">${t.preview_url
            ? `<img src="${esc(t.preview_url)}" alt="" />`
            : `<span class="e-ph">${esc(t.emoji || "🖼️")}</span>`}</div>
          <div class="adm-fields" style="flex:1">
            <div class="adm-row">
              <div class="adm-field" style="flex:1"><label>Название (RU)</label>
                <input class="m-name-ru" value="${esc(t.ru || "")}" /></div>
              <div class="adm-field" style="flex:1"><label>Название (EN)</label>
                <input class="m-name-en" value="${esc(t.en || "")}" /></div>
              <div class="adm-field"><label>Категория</label>
                <select class="m-c">${(d.categories || []).map((c) =>
                  `<option value="${esc(c)}" ${c === t.category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
            </div>
            <div class="adm-row">
              <div class="adm-field"><label>Тара</label>
                <input class="m-tara" value="${esc(t.tara || "any")}" style="width:90px" /></div>
              <div class="adm-field"><label>Эмодзи</label>
                <input class="m-emoji" value="${esc(t.emoji || "")}" style="width:70px" /></div>
              <div class="adm-field"><label>Просится в анимацию</label>
                <input class="m-motion" type="checkbox" ${t.motion ? "checked" : ""} /></div>
              <div class="adm-field"><label>В витрине превью</label>
                <input class="m-show" type="checkbox" ${t.showcase ? "checked" : ""} /></div>
              <span style="flex:1"></span>
              <span class="adm-pill">${esc(t.id)}</span>
              ${t.builtin ? "" : '<span class="adm-pill">свой</span>'}
              ${t.overridden ? '<span class="adm-pill warn">изменён</span>' : ""}
              ${t.hidden ? '<span class="adm-pill warn">скрыт</span>' : ""}
            </div>
            <div class="adm-field"><label>Промпт сцены по-русски — правь здесь</label>
              <textarea class="m-ru" rows="3"
                placeholder="правь по-русски — переведём сами">${esc(t.prompt_ru || "")}</textarea>
              <div class="adm-row" style="margin:6px 0">
                <button type="button" class="ghost m-toggle">Показать английский</button>
              </div>
            </div>
            <div class="adm-field m-en-box hidden">
              <label>Английский — этот текст уходит в модель</label>
              <textarea class="m-prompt" rows="4">${esc(t.prompt || "")}</textarea></div>
            <div class="adm-row">
              <button type="button" class="primary m-save">Сохранить</button>
              <button type="button" class="ghost m-prev">Сгенерировать превью</button>
              ${t.builtin
                ? `<button type="button" class="ghost m-hide">${
                    t.hidden ? "Вернуть в каталог" : "Скрыть из каталога"}</button>`
                : '<button type="button" class="ghost danger m-del">Удалить</button>'}
              <span class="m-msg"></span>
            </div>
          </div>
        </div>`;
      const msg = $(".m-msg", card);
      let mEnTouched = false;
      $(".m-prompt", card).addEventListener("input", () => { mEnTouched = true; });
      $(".m-toggle", card).addEventListener("click", (e) => {
        const bx = $(".m-en-box", card);
        bx.classList.toggle("hidden");
        e.target.textContent = bx.classList.contains("hidden")
          ? "Показать английский" : "Скрыть английский";
      });
      // перевод — сам при сохранении (см. body().translate)
      const body = () => {
        const promptRu = $(".m-ru", card).value;
        const out = {
          ru: $(".m-name-ru", card).value, en: $(".m-name-en", card).value,
          category: $(".m-c", card).value, tara: $(".m-tara", card).value,
          emoji: $(".m-emoji", card).value, prompt_ru: promptRu,
          motion: $(".m-motion", card).checked, showcase: $(".m-show", card).checked,
        };
        if (mEnTouched || !promptRu.trim()) out.prompt = $(".m-prompt", card).value;
        else out.translate = true;
        return out;
      };
      $(".m-save", card).addEventListener("click", async () => {
        msg.className = "m-msg muted";
        msg.textContent = "сохраняю…";
        try {
          await api(`/api/admin/mockups/${encodeURIComponent(t.id)}`,
                    { method: "PUT", body: body() });
          msg.className = "m-msg adm-ok";
          msg.textContent = "сохранено";
        } catch (e) { msg.className = "m-msg adm-err"; msg.textContent = e.message; }
      });
      $(".m-prev", card).addEventListener("click", async () => {
        msg.className = "m-msg muted";
        msg.textContent = "рисую превью, это до минуты…";
        try {
          await api(`/api/admin/mockups/${encodeURIComponent(t.id)}/preview`, { method: "POST" });
          renderMockups(box);
        } catch (e) { msg.className = "m-msg adm-err"; msg.textContent = e.message; }
      });
      const hide = $(".m-hide", card);
      if (hide) hide.addEventListener("click", async () => {
        try {
          if (t.hidden) {
            await api(`/api/admin/mockups/${encodeURIComponent(t.id)}`,
                      { method: "PUT", body: {} });
          } else {
            if (!confirm("Убрать шаблон из каталога у пользователей?")) return;
            await api(`/api/admin/mockups/${encodeURIComponent(t.id)}?hide=1`,
                      { method: "DELETE" });
          }
          renderMockups(box);
        } catch (e) { alert(e.message); }
      });
      const del = $(".m-del", card);
      if (del) del.addEventListener("click", async () => {
        if (!confirm("Удалить свой шаблон совсем?")) return;
        try {
          await api(`/api/admin/mockups/${encodeURIComponent(t.id)}`, { method: "DELETE" });
          renderMockups(box);
        } catch (e) { alert(e.message); }
      });
      list.appendChild(card);
    });
    if (!rows.length) list.innerHTML = '<p class="muted">ничего не нашлось</p>';
    const search = $(".m-search", box);
    search.addEventListener("change", () => { mockQuery = search.value; renderMockups(box); });
    $(".m-cat", box).addEventListener("change", (e) => {
      mockCat = e.target.value; renderMockups(box);
    });
    $(".m-new", box).addEventListener("click", async () => {
      const id = prompt("Ключ шаблона (латиница, цифры, подчёркивание)");
      if (!id) return;
      try {
        await api(`/api/admin/mockups/${encodeURIComponent(id.trim().toLowerCase())}`,
                  { method: "PUT", body: { ru: id, en: id, category: "product" } });
        renderMockups(box);
      } catch (e) { alert(e.message); }
    });
  }

  let stylesHost = null;   // куда рисуется каталог стилей: своя панель вкладки «Промты»
  let earnHost = null;
  let styleKey = null;
  let styleCatalog = null;
  let styleSkind = "style";   // "style" | "reference" — какой раздел открыт
  let styleQuery = "";

  async function renderStyles(box, skind) {
    stylesHost = box;
    if (skind) styleSkind = skind;
    const isRef = styleSkind === "reference";
    try {
      styleCatalog = await api("/api/admin/styles");
      const rows = styleCatalog.styles
        .filter((s) => (s.skind || "style") === styleSkind);
      box.innerHTML = `
        ${styleCatalog.problems.length ? `<div class="adm-card adm-err">
          Каталог не сходится: ${esc(styleCatalog.problems.slice(0, 4).join("; "))}
        </div>` : ""}
        <div class="adm-split s-split">
          <div class="adm-card s-side">
            <h3>${isRef ? "Референсы" : "Стили"} <span class="adm-count">${rows.length}</span></h3>
            <input type="search" class="s-search" placeholder="поиск…" value="${esc(styleQuery)}" />
            <div class="adm-list s-list"></div>
          </div>
          <div class="s-editor"></div>
        </div>`;
      const list = $(".s-list", box);
      const paintList = () => {
        list.innerHTML = "";
        const q = styleQuery.trim().toLowerCase();
        rows.filter((s) => !q
          || ((s.label && s.label.ru) || "").toLowerCase().includes(q)
          || ((s.label && s.label.en) || "").toLowerCase().includes(q)
          || s.key.toLowerCase().includes(q))
          .forEach((s) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "adm-item" + (s.key === styleKey ? " on" : "");
            b.innerHTML = `<span>${esc((s.label && s.label.ru) || s.key)}</span>
              <span class="adm-mark">${s.overridden ? "изменён" : ""}${s.builtin ? "" : " свой"}${
                s.assets ? ` · ${s.assets}📎` : ""}</span>`;
            b.addEventListener("click", () => { styleKey = s.key; renderStyles(box); });
            list.appendChild(b);
          });
        if (!list.children.length)
          list.innerHTML = '<span class="muted" style="padding:8px">ничего не нашлось</span>';
      };
      paintList();
      const search = $(".s-search", box);
      search.addEventListener("input", () => { styleQuery = search.value; paintList(); });
      if (styleKey && rows.some((s) => s.key === styleKey)) {
        styleEditor($(".s-editor", box), styleKey);
      } else {
        styleKey = null;
        $(".s-editor", box).innerHTML = `
          <div class="adm-card s-empty">
            <span class="s-empty-ico">${isRef ? "📎" : "🎨"}</span>
            <b>${isRef ? "Выбери референс слева" : "Выбери стиль слева"}</b>
            <span class="muted">${isRef
              ? "Авторские пресеты по чужим роликам: промпт, миниатюры-референсы и ссылка на исходник."
              : "Промпт, описание, референсы сеткой и сценарная база — всё в одной карточке."}</span>
          </div>`;
      }
    } catch (e) { fail(box, e); }
  }

  /* ═══════════════ КАРТОЧКА СТИЛЯ / РЕФЕРЕНСА — ОДНА СТРАНИЦА ═══════════════
     Вкладок здесь больше нет. Промпт, референсы и база для сценариев — не
     четыре разных экрана, а четыре стороны одной вещи, и владелец правил их
     по очереди, каждый раз теряя из виду остальные три. Теперь всё видно
     сразу: слева мета и промпт, справа сетка кадров, внизу база сценариев.

     ПРОМПТ ПРАВИТСЯ ПО-РУССКИ. В модель уходит английский — русский промпт
     рисует заметно хуже. Но думать на двух языках при каждой правке значит
     не править вовсе, поэтому русское поле главное, английское — второе и
     сворачиваемое, а между ними кнопка «Перевести → EN» и автоперевод при
     сохранении, если английский не трогали руками.

     РЕФЕРЕНС — ЭТО АВТОР. «Референсы» это каталог людей с узнаваемой
     манерой: ник, ссылки на его аккаунты и выгруженные кадры его роликов.
     Кадры размечаются подписью «что происходит», и из этих подписей сама
     собирается база для сценариев автора. */

  //: Пример в placeholder'е, а не в подписи под полем: владелец прочитал
  //: «база для сценариев» как «описания кадров» и заполнял её раскадровкой.
  const STORY_PLACEHOLDER =
    "Истории тихие и бытовые. Герой чаще в пути, чем на месте. "
    + "Конфликт не с врагом, а с обстоятельствами. "
    + "Кульминация — решение героя, а не экшн.\n\n"
    + "Пиши как режиссёру: места, поступки, темп, чего в этих историях не бывает.";

  async function styleEditor(box, key) {
    loading(box);
    let d;
    try { d = await api("/api/admin/styles/" + encodeURIComponent(key)); }
    catch (e) { return fail(box, e); }
    const isRef = d.skind === "reference";

    const save = async (patch) => {
      const msg = $(".s-msg", box);
      msg.className = "s-msg muted";
      msg.textContent = patch.translate ? "перевожу и сохраняю…" : "сохраняю…";
      try {
        await api("/api/admin/styles/" + encodeURIComponent(key),
                  { method: "PUT", body: patch });
        styleEditor(box, key);
      } catch (e) {
        msg.className = "s-msg adm-err";
        msg.textContent = e.message;
      }
    };

    const c = d.card;
    const lab = c.label || {}, desc = c.desc || {}, gain = c.gain || {};
    const links = d.links && d.links.length ? d.links : (d.source_url ? [d.source_url] : []);
    const refs = (d.assets || []).filter((a) => a.kind === "ref");
    const files = (d.assets || []).filter((a) => a.kind === "promptfile");
    const showcase = (d.assets || []).filter((a) => ["poster", "loop", "shot"].includes(a.kind));

    box.innerHTML = `
      <div class="adm-card s-card">
        <div class="adm-row s-head">
          <b>${esc(lab.ru || key)}</b>
          <span class="adm-pill">${esc(c.group || "")}</span>
          <span class="adm-pill">${c.tier === "pro" ? "PRO" : "FREE"}</span>
          ${d.overridden ? '<span class="adm-pill warn">изменён</span>' : ""}
          <select class="s-skind adm-pill" title="Стиль — наш пресет. Референс — карточка автора с узнаваемой манерой.">
            <option value="style" ${!isRef ? "selected" : ""}>стиль</option>
            <option value="reference" ${isRef ? "selected" : ""}>референс (автор)</option>
          </select>
          <span style="flex:1"></span>
          ${d.overridden && d.builtin ? '<button type="button" class="s-reset ghost">Вернуть заводской</button>' : ""}
        </div>

        <div class="s-grid">
          <div class="s-col">
            <div class="adm-fields">
              <div class="adm-row">
                <div class="adm-field" style="flex:1;min-width:150px">
                  <label>${isRef ? "Никнейм автора (RU)" : "Название (RU)"}</label>
                  <input type="text" class="f-lab-ru" value="${esc(lab.ru || "")}" />
                </div>
                <div class="adm-field" style="flex:1;min-width:150px">
                  <label>${isRef ? "Никнейм автора (EN)" : "Название (EN)"}</label>
                  <input type="text" class="f-lab-en" value="${esc(lab.en || "")}" />
                </div>
                <div class="adm-field"><label>Группа</label>
                  <select class="f-group">${(styleCatalog.groups || []).map((g) =>
                    `<option value="${esc(g.key)}" ${g.key === c.group ? "selected" : ""}
                      >${esc((g.label && g.label.ru) || g.key)}</option>`).join("")}</select></div>
                <div class="adm-field"><label>Тариф</label>
                  <select class="f-tier">
                    <option value="free" ${c.tier === "free" ? "selected" : ""}>FREE</option>
                    <option value="pro" ${c.tier === "pro" ? "selected" : ""}>PRO</option>
                  </select></div>
              </div>

              <div class="adm-field">
                <label>Ссылки на аккаунты автора — инста, тикток, ютуб. Откуда выгружены кадры.</label>
                <textarea class="f-links" rows="2"
                  placeholder="https://instagram.com/…&#10;https://tiktok.com/@…">${esc(links.join("\n"))}</textarea>
                <div class="s-links">${links.map((u) =>
                  `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, "").slice(0, 40))} ↗</a>`).join("")}</div>
              </div>

              <div class="adm-field">
                <label>Промпт по-русски — правь здесь. В генерацию уйдёт английский перевод.</label>
                <textarea class="f-prompt-ru" rows="10"
                  placeholder="правь по-русски — переведём сами">${esc(d.prompt_ru || "")}</textarea>
                <div class="adm-row" style="margin-top:6px">
                  <button type="button" class="ghost f-toggle-en">Показать английский</button>
                  <span class="f-tr-msg muted"></span>
                </div>
              </div>
              <div class="adm-field f-en-box hidden">
                <label>Английский промпт — ровно этот текст уходит в модель первым блоком каждого кадра.
                  Правь руками только для тонкой доводки.</label>
                <textarea class="f-prompt" rows="10">${esc(d.prompt || "")}</textarea>
                <p class="adm-note"><span class="p-len"></span>${d.builtin
                  ? " · заводской: " + (d.builtin_prompt || "").length + " симв." : ""}</p>
                ${files.length ? `<div class="adm-row">${files.map((f) =>
                  `<button type="button" class="f-from ghost" data-id="${f.id}">взять из ${esc(f.title || f.filename)}</button>`).join("")}</div>` : ""}
                ${d.builtin ? '<button type="button" class="ghost f-orig">Показать заводской</button>' : ""}
              </div>

              <div class="adm-row">
                <div class="adm-field" style="flex:1;min-width:180px">
                  <label>Краткое описание для витрины пользователю (RU) — не разметка кадров</label>
                  <textarea class="f-desc-ru" rows="2">${esc(desc.ru || "")}</textarea></div>
                <div class="adm-field" style="flex:1;min-width:180px">
                  <label>То же для витрины (EN)</label>
                  <textarea class="f-desc-en" rows="2">${esc(desc.en || "")}</textarea></div>
              </div>
              <div class="adm-field"><label>Что даёт (RU) — строка выгоды на карточке</label>
                <textarea class="f-gain-ru" rows="2">${esc(gain.ru || "")}</textarea></div>
            </div>
          </div>

          <div class="s-col s-refs-col">
            <div class="adm-field">
              <label>Кадры автора — <b class="ref-count">${refs.length}</b>.
                Клик по кадру: подпись «что происходит». Галочка — уходит в генерацию.</label>
              <div class="ref-drop" data-kind="ref">
                <b>Перетащи кадры сюда</b>
                <span class="muted">или выбери файлы — можно сразу десятки; сервер ужмёт до 1536px</span>
                <input type="file" class="a-file ref-multi" data-kind="ref" multiple
                       accept="image/jpeg,image/png,image/webp" />
              </div>
              <div class="ref-queue"></div>
              <div class="adm-assets s-refgrid">${refs.map((a) => assetCard(a, true)).join("")
                || '<span class="muted">кадров пока нет</span>'}</div>
            </div>
            <details class="s-showcase">
              <summary>Витрина карточки: постер, петля, примеры кадров, файлы промптов</summary>
              ${["poster", "loop", "shot", "promptfile"].map((kind) => {
                const items = (d.assets || []).filter((a) => a.kind === kind);
                const title = { poster: "Постер", loop: "Петля 2с", shot: "Примеры кадров",
                                promptfile: "Файлы .txt/.md" }[kind];
                return `<div class="adm-field"><label>${title}</label>
                  <div class="adm-assets">${items.map((a) => assetCard(a)).join("")
                    || '<span class="muted">пусто</span>'}</div>
                  <input type="file" class="a-file" data-kind="${kind}" /></div>`;
              }).join("")}
            </details>
          </div>
        </div>

        <div class="adm-field s-story">
          <label>База для сценариев</label>
          <textarea class="f-story" rows="7"
            placeholder="${esc(STORY_PLACEHOLDER)}">${esc(d.story_manual || "")}</textarea>
          <p class="adm-note">Влияет на <b>СЮЖЕТ и раскадровку</b>, не на картинку.
            Картинка — в Промпте и Референсах.</p>
          ${(d.story_auto || []).length ? `<div class="s-auto">
            <b>Сцены из роликов автора — собираются сами из подписей кадров:</b>
            <ul>${d.story_auto.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
          </div>` : ""}
        </div>

        <div class="s-sticky">
          <span class="s-msg"></span>
          <span style="flex:1"></span>
          <button type="button" class="primary f-save">Сохранить</button>
        </div>
      </div>`;

    // ── промпт: длина, перевод, заводской ──
    const ru = $(".f-prompt-ru", box);
    const en = $(".f-prompt", box);
    const enBox = $(".f-en-box", box);
    const len = $(".p-len", box);
    let enTouched = false;
    const paintLen = () => { len.textContent = en.value.length + " символов"; };
    paintLen();
    en.addEventListener("input", () => { enTouched = true; paintLen(); });
    $(".f-toggle-en", box).addEventListener("click", (e) => {
      enBox.classList.toggle("hidden");
      e.target.textContent = enBox.classList.contains("hidden")
        ? "Показать английский" : "Скрыть английский";
    });
    const trMsg = $(".f-tr-msg", box);
    // перевод — сам при сохранении
    const orig = $(".f-orig", box);
    if (orig) orig.addEventListener("click", () => {
      en.value = d.builtin_prompt || ""; enTouched = true; paintLen();
    });
    $$(".f-from", box).forEach((b) => b.addEventListener("click", async () => {
      try {
        const r = await api(`/api/admin/styles/assets/${b.dataset.id}/text`);
        en.value = r.text || ""; enTouched = true; enBox.classList.remove("hidden"); paintLen();
      } catch (e) { alert(e.message); }
    }));

    // ── вид и ссылки ──
    $(".s-skind", box).addEventListener("change", async (e) => {
      try {
        await api(`/api/admin/styles/${encodeURIComponent(key)}/meta`,
                  { method: "POST", body: { kind: e.target.value } });
        renderStyles(stylesHost || pane());
      } catch (err) { alert(err.message); }
    });

    // ── сохранение: одно на всю карточку ──
    $(".f-save", box).addEventListener("click", async () => {
      const urls = $(".f-links", box).value.split("\n")
        .map((x) => x.trim()).filter(Boolean);
      try {
        await api(`/api/admin/styles/${encodeURIComponent(key)}/meta`,
                  { method: "POST", body: { links: urls, source_url: urls[0] || "" } });
      } catch (e) { /* ссылки не важнее промпта: молча дальше */ }
      const patch = {
        label: { ru: $(".f-lab-ru", box).value, en: $(".f-lab-en", box).value },
        desc: { ru: $(".f-desc-ru", box).value, en: $(".f-desc-en", box).value },
        gain: { ru: $(".f-gain-ru", box).value, en: (c.gain || {}).en || "" },
        group: $(".f-group", box).value,
        tier: $(".f-tier", box).value,
        prompt_ru: ru.value,
        story_manual: $(".f-story", box).value,
      };
      // Английский правили руками — оставляем как есть; не правили —
      // пересобираем из русского, чтобы поле и генерация не разъехались.
      if (enTouched) patch.prompt = en.value;
      else if (ru.value.trim()) patch.translate = true;
      else patch.prompt = en.value;
      save(patch);
    });

    const reset = $(".s-reset", box);
    if (reset) reset.addEventListener("click", async () => {
      if (!confirm("Снять все правки этого стиля и вернуть заводской?")) return;
      try {
        await api("/api/admin/styles/" + encodeURIComponent(key), { method: "DELETE" });
        renderStyles(stylesHost || pane());
      } catch (e) { alert(e.message); }
    });

    styleAssetHandlers(box, key, d);
  }

  /* ── файлы карточки: загрузка, подписи кадров, удаление ── */
  function styleAssetHandlers(box, key, d) {
    const reload = () => styleEditor(box, key);

    const uploadMany = async (files, kind) => {
      const queue = $(".ref-queue", box);
      const rows = new Map();
      if (queue) {
        queue.innerHTML = "";
        files.forEach((f, i) => {
          const r = document.createElement("div");
          r.className = "ref-q-row";
          r.innerHTML = `<span>${esc(f.name.slice(0, 40))}</span><i>в очереди…</i>`;
          queue.appendChild(r);
          rows.set(i, r.querySelector("i"));
        });
      }
      for (let i = 0; i < files.length; i += 1) {
        const st = rows.get(i);
        if (st) st.textContent = "загружаю…";
        const fd = new FormData();
        fd.append("file", files[i]);
        fd.append("kind", kind);
        if (kind === "ref") fd.append("in_generation", "1");
        try {
          await api(`/api/admin/styles/${encodeURIComponent(key)}/assets`,
                    { method: "POST", body: fd });
          if (st) { st.textContent = "✓"; st.className = "adm-ok"; }
        } catch (e) {
          if (st) { st.textContent = "✗ " + (e.message || ""); st.className = "adm-err"; }
        }
      }
      reload();
    };

    $$(".a-file", box).forEach((inp) => inp.addEventListener("change", () => {
      const files = Array.from(inp.files || []);
      if (files.length) uploadMany(files, inp.dataset.kind);
    }));
    const drop = $(".ref-drop", box);
    if (drop) {
      ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault(); drop.classList.add("over");
      }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault(); drop.classList.remove("over");
      }));
      drop.addEventListener("drop", (e) => {
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || [])
          .filter((f) => /^image\//.test(f.type));
        if (files.length) uploadMany(files, "ref");
      });
    }
    $$(".a-del", box).forEach((b) => b.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Удалить файл?")) return;
      try {
        await api("/api/admin/styles/assets/" + b.dataset.id, { method: "DELETE" });
        reload();
      } catch (err) { alert(err.message); }
    }));
    $$(".a-gen", box).forEach((cb) => cb.addEventListener("change", async (e) => {
      e.stopPropagation();
      try {
        await api("/api/admin/styles/assets/" + cb.dataset.id,
                  { method: "PATCH", body: { in_generation: cb.checked } });
      } catch (err) { alert(err.message); cb.checked = !cb.checked; }
    }));
    // Подпись кадра: открывается кликом по миниатюре. Из подписей сама
    // собирается база для сценариев автора — поэтому поле не спрятано в
    // «дополнительно», а лежит в одном клике от картинки.
    $$(".a-shot", box).forEach((el) => el.addEventListener("click", async () => {
      const id = el.dataset.id;
      const a = (d.assets || []).find((x) => String(x.id) === String(id)) || {};
      const note = prompt(
        "Что происходит в кадре: кто, что делает, где, каким приёмом снято",
        a.note || "");
      if (note === null) return;
      try {
        await api("/api/admin/styles/assets/" + id, { method: "PATCH", body: { note } });
        reload();
      } catch (e) { alert(e.message); }
    }));
  }

  function assetCard(a, withNote) {
    const isVideo = /\.mp4$/i.test(a.filename);
    const isText = /\.(txt|md)$/i.test(a.filename);
    const media = isText
      ? `<div class="adm-asset-file">📄 ${esc(a.filename.slice(0, 22))}</div>`
      : isVideo
        ? `<video src="${esc(a.url)}" muted></video>`
        : `<img src="${esc(a.url)}" alt="" loading="lazy" />`;
    const noted = withNote && (a.note || "").trim();
    return `<div class="adm-asset${noted ? " noted" : ""}">
      <div class="a-shot" data-id="${a.id}" title="${esc(a.note || "подписать кадр")}">${media}
        ${withNote ? `<span class="a-cap">${noted
          ? esc(a.note.slice(0, 60)) : "+ что в кадре"}</span>` : ""}</div>
      <div class="adm-asset-body">
      ${a.kind === "ref" ? `<label><input type="checkbox" class="a-gen"
        data-id="${a.id}" ${a.in_generation ? "checked" : ""} /> в генерацию</label>` : ""}
      <button type="button" class="a-del ghost" data-id="${a.id}"
        style="height:26px;font-size:11px">Удалить</button>
    </div></div>`;
  }


  /* ─────────────────────────── модели ─────────────────────────── */
  async function renderModels(box) {
    try {
      const d = await api("/api/admin/models");
      // Тумблер, использования и заработок — прямо в строках цен: решение
      // «выключить модель» принимается на тех же цифрах, что его оправдывают.
      const tgl = (e) => e.toggle_id
        ? `<label class="adm-sw"><input type="checkbox" data-toggle="${esc(e.toggle_id)}"
             ${e.enabled ? "checked" : ""} /><i></i></label>` : "";
      const stat = (e) => `<td class="num">${num(e.uses || 0)}</td>
        <td class="num">${num(e.earned || 0)}</td>`;
      const table = (title, rows, extra) => `<div class="adm-card">
        <h3>${esc(title)}</h3>
        <table class="adm-table">
          <thead><tr><th>вкл</th><th>модель</th><th>канал</th><th>жив</th>
            <th class="num">цена, ток</th><th class="num">себес</th>
            <th class="num">использ.</th><th class="num">принесли ток</th>
            ${extra ? `<th>${esc(extra)}</th>` : ""}</tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
      box.innerHTML =
        table("Текстовые модели (блок сценария)", d.text.map((e) => `<tr>
          <td>${tgl(e)}</td>
          <td><b>${esc(e.title)}</b><br /><span class="muted">${esc(e.note || "")}</span></td>
          <td>${esc(e.channel)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd || 0).toFixed(3)}</td>
          ${stat(e)}
          <td>с ${esc(e.min_plan)}</td></tr>`).join(""), "тариф")
        + table("Движки кадров", d.images.map((e) => `<tr>
          <td>${tgl(e)}</td>
          <td><b>${esc(e.title)}</b><br /><span class="muted">${esc(e.note || "")}</span></td><td>${esc(e.channel)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd).toFixed(3)}</td>
          ${stat(e)}</tr>`).join(""))
        + table("Движки видео", d.videos.map((e) => `<tr>
          <td>${tgl(e)}</td>
          <td><b>${esc(e.title)}</b><br /><span class="muted">${esc(e.note || "")}</span></td><td>${esc(e.family)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd).toFixed(3)}</td>
          ${stat(e)}</tr>`).join(""))
        + table("Звук и голос", (d.audio || []).map((e) => `<tr>
          <td>${tgl(e)}</td>
          <td><b>${esc(e.title)}</b><br /><span class="muted">${esc(e.note || "")}</span></td>
          <td>${esc(e.channel)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd || 0).toFixed(3)}</td>
          ${stat(e)}</tr>`).join(""))
        + `<p class="adm-note">Один токен = $${d.point_usd} себестоимости. Цена в токенах
           выводится из этой константы и долларовых цен движков, поэтому разойтись с
           реальными расходами она не может.</p>`;
      box.addEventListener("change", async (ev) => {
        const t = ev.target.closest("[data-toggle]");
        if (!t) return;
        const off = [...box.querySelectorAll("[data-toggle]:not(:checked)")]
          .map((i) => i.dataset.toggle);
        try {
          await api("/api/admin/models-toggle", { method: "POST", body: { disabled: off } });
        } catch (e) { t.checked = !t.checked; alert(String(e.message || e)); }
      });
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── настройки ─────────────────────────── */
  async function renderSettings(box) {
    try {
      const d = await api("/api/admin/settings");
      box.innerHTML = `
        <div class="adm-card">
          <h3>Ключи и каналы</h3>
          <table class="adm-table">
            <thead><tr><th>ключ</th><th>состояние</th><th>что от него зависит</th></tr></thead>
            <tbody>${d.keys.map((k) => `<tr>
              <td><code>${esc(k.key)}</code></td>
              <td>${k.set ? '<span class="adm-pill on">задан</span>'
                          : '<span class="adm-pill off">не задан</span>'}</td>
              <td class="muted">${esc(k.note)}</td></tr>`).join("")}</tbody>
          </table>
          <p class="adm-note">Значений здесь нет и не будет. Ключи живут в
            <code>infra/.env</code>: редактор ключей в вебе — это способ увести их
            одним XSS. Чтобы добавить ключ, положи его в .env и перезапусти сервис.</p>
        </div>
        <div class="adm-card">
          <h3>Каналы</h3>
          <table class="adm-table">
            <tbody>
              <tr><td>Мост внешнего выхода</td><td><code>${esc(d.egress_proxy || "нет")}</code></td></tr>
              <tr><td>Шлюз подписки</td><td><code>${esc(d.gateway_url)}</code></td></tr>
              <tr><td>Текстовая модель по умолчанию</td><td><code>${esc(d.text_default)}</code></td></tr>
              <tr><td>Файлы стилей</td><td><code>${esc(d.style_assets_dir)}</code></td></tr>
              <tr><td>Снимков кадров у сцены</td><td>${num(d.scene_versions_keep)}</td></tr>
            </tbody>
          </table>
        </div>`;
    } catch (e) { fail(box, e); }
  }

  /* ─────────────────────────── наценка ─────────────────────────── */
  async function renderPricing(box) {
    let d;
    try { d = await api("/api/admin/pricing"); } catch (e) { return fail(box, e); }
    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-price-head">
          <span class="adm-price-k">×<b>${d.markup.toFixed(2)}</b></span>
          <span class="muted">к себестоимости движков</span>
        </div>
        <input type="range" class="adm-markup" min="${d.min}" max="${d.max}"
               step="0.05" value="${d.markup}" />
        <div class="adm-price-scale muted">
          <span>×${d.min} — по себестоимости</span><span>×${d.max}</span>
        </div>
        <div class="adm-price-facts">
          <div><b class="adm-f-scene">${d.scene_cost}</b><span>токенов за сцену<br>Seedance 2 Mini</span></div>
          <div><b class="adm-f-frames">${d.frame_pair_cost}</b><span>за пару кадров<br>Nano Banana</span></div>
          <div><b class="adm-f-usd">$${d.point_usd_now}</b><span>себестоимости<br>в одном токене</span></div>
        </div>
        <p class="muted adm-price-note">
          Чем выше коэффициент, тем больше токенов списывается за ту же
          генерацию — цена тарифов не меняется, меняется то, сколько работы
          человек получает за свои токены. Бонусы за друзей:
          <b>${d.signup_bonus}</b> токенов за регистрацию и
          <b>${d.cashback_pct}%</b> кэшбэка с каждой его оплаты.
        </p>
        <div class="adm-price-acts">
          <button type="button" class="adm-price-save primary" disabled>Применить</button>
          <span class="adm-price-msg muted"></span>
        </div>
      </div>`;
    const slider = $(".adm-markup", box);
    const save = $(".adm-price-save", box);
    const msg = $(".adm-price-msg", box);
    const kEl = $(".adm-price-k b", box);
    // Пока ползунок ведут, показываем ПРЕДПОЛАГАЕМУЮ цену: считаем её из той
    // же пропорции, что и сервер, чтобы решение принималось до нажатия.
    const base = { scene: d.scene_cost, frames: d.frame_pair_cost,
                   usd: d.point_usd_now, k: d.markup };
    slider.addEventListener("input", () => {
      const k = Number(slider.value);
      const ratio = k / base.k;
      kEl.textContent = k.toFixed(2);
      $(".adm-f-scene", box).textContent = Math.ceil(base.scene * ratio);
      $(".adm-f-frames", box).textContent = Math.ceil(base.frames * ratio);
      $(".adm-f-usd", box).textContent = "$" + (base.usd / ratio).toFixed(5);
      save.disabled = Math.abs(k - base.k) < 0.001;
      msg.textContent = "";
    });
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api("/api/admin/pricing", { method: "POST",
                                          body: { markup: Number(slider.value) } });
      } catch (e) { msg.textContent = String(e.message || e); save.disabled = false; return; }
      msg.textContent = "наценка применена";
      await renderPricing(box);
    });
  }

  /* ─────────────────────────── рынок ─────────────────────────── */
  function renderMarket(box) {
    // Страница сравнения встраивается как есть: одна правда в одном файле,
    // и она же доступна команде по публичной ссылке.
    box.innerHTML = `
      <div class="adm-market-bar">
        <input type="search" class="adm-market-q" placeholder="фильтр по сервису или модели…" />
        <a href="/competitors.html" target="_blank" class="ghost">открыть отдельно ↗</a>
      </div>
      <iframe class="adm-market-frame" src="/competitors.html"></iframe>`;
    const q = box.querySelector(".adm-market-q");
    const frame = box.querySelector(".adm-market-frame");
    q.addEventListener("input", () => {
      // Интерактив: фильтр прячет строки таблиц внутри встроенной страницы.
      const doc = frame.contentDocument;
      if (!doc) return;
      const needle = q.value.trim().toLowerCase();
      doc.querySelectorAll("tbody tr").forEach((tr) => {
        tr.style.display = !needle
          || tr.textContent.toLowerCase().includes(needle) ? "" : "none";
      });
    });
  }

  /* ─────────── дизайн-токены ─────────── */
  /* Тумблеры моделей внутри вкладки «Модели» рисует renderModels ниже;
     здесь — общий блок сохранения. */
  async function modelsToggleBlock(box) {
    let d;
    try { d = await api("/api/admin/models-toggle"); } catch (e) { return; }
    const wrap = document.createElement("div");
    wrap.className = "adm-card";
    wrap.innerHTML = `<b>Показ моделей клиентам</b>
      <p class="muted">Выключенная модель пропадает из выбора у клиентов —
      для экономии токенов и управления ассортиментом. Действует сразу.</p>
      <div class="adm-mt-list"></div>
      <div class="adm-price-acts">
        <button type="button" class="adm-mt-save primary">Применить</button>
        <span class="adm-price-msg muted"></span></div>`;
    const list = wrap.querySelector(".adm-mt-list");
    d.models.forEach((m) => {
      const l = document.createElement("label");
      l.className = "adm-mt-row";
      l.innerHTML = `<input type="checkbox" ${m.enabled ? "checked" : ""}
        data-id="${m.id}" /> <span>${m.title}</span>
        <i class="muted">${m.kind}</i>`;
      list.appendChild(l);
    });
    wrap.querySelector(".adm-mt-save").addEventListener("click", async () => {
      const off = [...list.querySelectorAll("input:not(:checked)")].map((i) => i.dataset.id);
      const msg = wrap.querySelector(".adm-price-msg");
      try { await api("/api/admin/models-toggle", { method: "POST", body: { disabled: off } }); }
      catch (e) { msg.textContent = String(e.message || e); return; }
      msg.textContent = "применено";
    });
    box.prepend(wrap);
  }

  async function renderDesign(box) {
    let d;
    try { d = await api("/api/admin/design"); } catch (e) { return fail(box, e); }
    const t = d.tokens;
    box.innerHTML = `
      <div class="adm-card adm-design">
        <div class="adm-d-row"><label>Градиент огня</label>
          <input type="color" data-k="accent_from" value="${t.accent_from}" />
          <input type="color" data-k="accent_mid" value="${t.accent_mid}" />
          <input type="color" data-k="accent_to" value="${t.accent_to}" />
          <span class="adm-d-fire" style="background:linear-gradient(95deg,${t.accent_from},${t.accent_mid},${t.accent_to})"></span>
        </div>
        <div class="adm-d-row"><label>Фон бумаги</label>
          <input type="color" data-k="bg" value="${t.bg}" /></div>
        <div class="adm-d-row"><label>Размытие стекла</label>
          <input type="range" data-k="glass_blur" min="0" max="40" value="${t.glass_blur}" />
          <b>${t.glass_blur}px</b></div>
        <div class="adm-price-acts">
          <button type="button" class="adm-d-save primary">Применить</button>
          <button type="button" class="adm-d-reset ghost">Сбросить к канону</button>
          <span class="adm-price-msg muted"></span>
        </div>
        <p class="muted">Действует на весь сервис сразу: /api/theme.css кладёт
        токены поверх стилей. Канон — в docs/DESIGN_SYSTEM.md.</p>
      </div>`;
    const msg = box.querySelector(".adm-price-msg");
    const collect = () => {
      const out = {};
      box.querySelectorAll("[data-k]").forEach((i) => { out[i.dataset.k] = i.value; });
      return out;
    };
    box.querySelector(".adm-d-save").addEventListener("click", async () => {
      try { await api("/api/admin/design", { method: "POST", body: collect() }); }
      catch (e) { msg.textContent = String(e.message || e); return; }
      msg.textContent = "применено — обнови любую страницу сервиса";
    });
    box.querySelector(".adm-d-reset").addEventListener("click", async () => {
      try { await api("/api/admin/design", { method: "POST", body: d.defaults }); }
      catch (e) { msg.textContent = String(e.message || e); return; }
      await renderDesign(box);
    });
  }

  /* ─────────────────────────── демки лейбла ─────────────────────────── */
  async function renderDemos(box) {
    try {
      const d = await api("/api/music/demos?limit=200");
      const items = d.items || [];
      if (!items.length) {
        box.innerHTML = `<div class="adm-card muted">демок пока нет</div>`;
        return;
      }
      const rows = items.map((x) => {
        const checks = (x.checks || []).filter((c) => c.level !== "ok")
          .map((c) => `<div class="muted">• ${esc(c.text || c.key)}</div>`).join("");
        return `<div class="adm-card">
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            ${x.cover_url ? `<img src="${esc(x.cover_url)}" style="width:56px;height:56px;border-radius:8px;object-fit:cover">` : ""}
            <div style="flex:1;min-width:220px">
              <b>${esc(x.artist)} — ${esc(x.track_title)}</b>
              <div class="muted">${esc(x.genre || "жанр не указан")} · ${esc((x.created_at || "").slice(0, 16).replace("T", " "))} · ИИ: ${esc(x.ai_disclosure || "—")}${x.isrc ? " · ISRC " + esc(x.isrc) : ""}</div>
              <div class="muted">контакт: ${esc(x.contact)}${x.socials ? " · " + esc(x.socials.split("\n")[0]) : ""}</div>
              ${x.comment ? `<div class="muted">${esc(x.comment)}</div>` : ""}
              ${checks}
            </div>
            <div style="display:flex;gap:6px;align-items:center">
              ${x.audio_url ? `<audio controls preload="none" src="${esc(x.audio_url)}" style="max-width:240px"></audio>` : ""}
              <select data-demo="${x.id}">
                ${["new", "seen", "accepted", "declined"].map((st) => `<option value="${st}"${st === x.status ? " selected" : ""}>${st}</option>`).join("")}
              </select>
            </div>
          </div>
        </div>`;
      }).join("");
      box.innerHTML = rows;
      box.querySelectorAll("select[data-demo]").forEach((sel) => {
        sel.addEventListener("change", async () => {
          try {
            await api(`/api/music/demos/${sel.dataset.demo}/status`, {
              method: "POST", body: { status: sel.value } });
          } catch (e) { alert("не сохранилось: " + (e.message || e)); }
        });
      });
    } catch (e) {
      box.innerHTML = `<div class="adm-card">не загрузилось: ${esc(String(e.message || e))}</div>`;
    }
  }

  const RENDER = {
    stats: renderStats, users: renderUsers, broadcast: renderBroadcast,
    payouts: renderPayouts, demos: renderDemos, ledger: renderLedger, earn: renderEarnAdmin,
    // «Стили» и «Референсы» больше НЕ пункты меню: они вкладки страницы
    // «Промты». Два входа в один каталог означали бы два разных ответа на
    // вопрос «что сейчас сохранено».
    prompts: renderPrompts,
    models: renderModels, pricing: renderPricing, market: renderMarket,
    design: renderDesign,
    settings: renderSettings,
  };

  async function boot() {
    try {
      const me = await api("/api/me");
      const u = (me && me.user) || {};
      $(".adm-who").textContent = u.name || u.login || u.email || "владелец";
    } catch (e) { /* страницу и так отдали только админу */ }
    const qs = new URLSearchParams(location.search);
    const want = qs.get("tab");
    const sub = qs.get("sub");
    if (sub && P_TABS.some(([id]) => id === sub)) promptTab = sub;
    // Старые закладки на отдельные разделы не должны отдавать пустую
    // страницу: они ведут на те же панели, теперь вкладками.
    if (want === "journal") return open("ledger");
    if (want === "styles" || want === "refstyles") {
      promptTab = want === "refstyles" ? "refs" : "styles";
      return open("prompts");
    }
    open(want || "stats");
  }

  boot();
})();
