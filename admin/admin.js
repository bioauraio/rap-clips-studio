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
    { id: "ledger", ico: "🧾", title: "Журнал",
      sub: "Инвариант: сумма строк журнала против фактического баланса. Расхождение = кто-то прошёл мимо кассы." },
    { id: "styles", ico: "🎨", title: "Стили",
      sub: "Промпт, референсы, файлы и сценарная база каждого стиля. Промпты закрыты: наружу уходят только подпись и описание." },
    { id: "refstyles", ico: "📎", title: "Референсы",
      sub: "Авторские пресеты по чужим роликам (инста/рилс). Те же карточки и промпты, плюс ссылка на исходник; в генерации работают как обычные стили, на витрине пользователей ничего не меняется." },
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

  function nav() {
    const box = $(".adm-nav");
    box.innerHTML = "";
    TABS.forEach((t) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "adm-tab" + (t.id === current ? " on" : "");
      b.innerHTML = `<span class="adm-tab-ico">${t.ico}</span>${esc(t.title)}`;
      b.addEventListener("click", () => open(t.id));
      box.appendChild(b);
    });
  }

  function open(id) {
    current = TABS.some((t) => t.id === id) ? id : "stats";
    const spec = TABS.find((t) => t.id === current);
    $(".adm-title").textContent = spec.title;
    $(".adm-sub").textContent = spec.sub;
    history.replaceState(null, "", "/admin?tab=" + current);
    nav();
    loading(pane());
    RENDER[current](pane());
  }

  /* ─────────────────────────── сводка ─────────────────────────── */
  async function renderStats(box) {
    try {
      const d = await api("/api/admin/stats?days=30");
      const margin = d.revenue_cents - d.cost_cents;
      box.innerHTML = `
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
    } catch (e) { fail(box, e); }
  }

  const stat = (v, s) => `<div class="adm-stat"><b>${esc(v)}</b><span>${esc(s)}</span></div>`;

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
      const d = await api("/api/admin/users/" + uid);
      const u = d.user || d;
      box.innerHTML = `
        <div class="adm-card">
          <div class="adm-row">
            <button type="button" class="ghost u-back">← к списку</button>
            <b>${esc(u.name || u.login || ("#" + u.id))}</b>
            <span class="muted">${esc(u.email || (u.tg ? "@" + u.tg : ""))}</span>
          </div>
          <div class="adm-grid" style="margin-top:12px">
            ${stat(esc(u.plan_title || u.plan), "тариф")}
            ${stat(num(u.points) + " ⚡", "токенов")}
            ${stat(when(u.plan_until), "активен до")}
            ${stat(esc(u.pay_source || "—"), "кто списывает")}
          </div>
        </div>
        <div class="adm-card">
          <h3>Начислить или списать токены</h3>
          <div class="adm-row">
            <input type="number" class="p-delta" value="100" style="width:120px" />
            <input type="text" class="p-why" placeholder="за что (попадёт в журнал)" style="flex:1;min-width:180px" />
            <button type="button" class="primary p-go">Применить</button>
            <span class="p-msg"></span>
          </div>
          <p class="adm-note">Движение токенов идёт через ту же единственную дверь,
            что и генерации, — и попадает в журнал вместе с тем, кто его сделал.</p>
        </div>`;
      $(".u-back", box).addEventListener("click", () => open("users"));
      $(".p-go", box).addEventListener("click", async () => {
        const msg = $(".p-msg", box);
        msg.textContent = "…";
        try {
          const r = await api(`/api/admin/users/${uid}/points`, {
            method: "POST",
            body: { delta: Number($(".p-delta", box).value || 0),
                    reason: $(".p-why", box).value.trim() },
          });
          msg.className = "p-msg adm-ok";
          msg.textContent = "стало " + num(r.points != null ? r.points : "");
        } catch (e) {
          msg.className = "p-msg adm-err";
          msg.textContent = e.message;
        }
      });
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
          renderEarnAdmin(pane());
        } catch (err) { msg.className = "e-msg adm-err"; msg.textContent = err.message; }
      });
      $(".e-del", card).addEventListener("click", async () => {
        if (!confirm("Удалить продукт с витрины?")) return;
        try {
          await api(`/api/admin/trends/${t.id}`, { method: "DELETE" });
          renderEarnAdmin(pane());
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
        renderEarnAdmin(pane());
      } catch (e) { alert(e.message); }
    });
  }

  let styleKey = null;
  let styleTab = "card";
  let styleCatalog = null;
  let styleSkind = "style";   // "style" | "reference" — какой раздел открыт
  let styleQuery = "";

  async function renderStyles(box, skind) {
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

  const S_TABS = [
    ["card", "Карточка"], ["prompt", "Промпт"], ["refs", "Референсы"],
    ["story", "База для сценариев"],
  ];

  async function styleEditor(box, key) {
    loading(box);
    let d;
    try { d = await api("/api/admin/styles/" + encodeURIComponent(key)); }
    catch (e) { return fail(box, e); }

    const save = async (patch) => {
      const msg = $(".s-msg", box);
      msg.className = "s-msg muted";
      msg.textContent = "сохраняю…";
      try {
        await api("/api/admin/styles/" + encodeURIComponent(key),
                  { method: "PUT", body: patch });
        msg.className = "s-msg adm-ok";
        msg.textContent = "сохранено";
        styleEditor(box, key);
      } catch (e) {
        msg.className = "s-msg adm-err";
        msg.textContent = e.message;
      }
    };

    box.innerHTML = `
      <div class="adm-card">
        <div class="adm-row">
          <b>${esc((d.card.label && d.card.label.ru) || key)}</b>
          <span class="adm-pill">${esc(d.card.group || "")}</span>
          <span class="adm-pill">${d.card.tier === "pro" ? "PRO" : "FREE"}</span>
          <span class="adm-pill">${d.card.prompt_class === "closed" ? "закрытый" : "разбираемый"}</span>
          ${d.overridden ? '<span class="adm-pill warn">изменён</span>' : ""}
          <select class="s-skind adm-pill" title="Вид: стиль или референс (организация каталога, на генерацию не влияет)">
            <option value="style" ${d.skind !== "reference" ? "selected" : ""}>стиль</option>
            <option value="reference" ${d.skind === "reference" ? "selected" : ""}>референс</option>
          </select>
          <span style="flex:1"></span>
          <span class="s-msg"></span>
          ${d.overridden && d.builtin ? '<button type="button" class="s-reset ghost">Вернуть заводской</button>' : ""}
        </div>
        <div class="adm-row s-source-row">
          <div class="adm-field" style="flex:1">
            <label>Ссылка на исходник (рилс/пост, откуда снят пресет)</label>
            <div class="adm-row">
              <input type="url" class="s-source" placeholder="https://instagram.com/reel/…"
                     value="${esc(d.source_url || "")}" style="flex:1" />
              <button type="button" class="ghost s-source-save">Сохранить</button>
              ${d.source_url ? `<a class="s-source-open" href="${esc(d.source_url)}"
                target="_blank" rel="noopener">открыть ↗</a>` : ""}
            </div>
          </div>
        </div>
        <div class="adm-tabs s-tabs" style="margin-top:12px"></div>
        <div class="s-body"></div>
      </div>`;
    $(".s-skind", box).addEventListener("change", async (e) => {
      try {
        await api(`/api/admin/styles/${encodeURIComponent(key)}/meta`,
                  { method: "POST", body: { kind: e.target.value } });
        renderStyles(pane());
      } catch (err) { alert(err.message); }
    });
    $(".s-source-save", box).addEventListener("click", async () => {
      try {
        await api(`/api/admin/styles/${encodeURIComponent(key)}/meta`,
                  { method: "POST", body: { source_url: $(".s-source", box).value } });
        styleEditor(box, key);
      } catch (err) { alert(err.message); }
    });

    const tabs = $(".s-tabs", box);
    S_TABS.forEach(([id, title]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = id === styleTab ? "on" : "";
      b.textContent = title;
      b.addEventListener("click", () => { styleTab = id; styleEditor(box, key); });
      tabs.appendChild(b);
    });
    const reset = $(".s-reset", box);
    if (reset) reset.addEventListener("click", async () => {
      if (!confirm("Снять все правки этого стиля и вернуть заводской?")) return;
      try {
        await api("/api/admin/styles/" + encodeURIComponent(key), { method: "DELETE" });
        renderStyles(pane());
      } catch (e) { alert(e.message); }
    });

    const body = $(".s-body", box);
    if (styleTab === "card") styleCard(body, d, save);
    else if (styleTab === "prompt") stylePrompt(body, d, save);
    else if (styleTab === "story") styleStory(body, d, save);
    else styleRefs(body, d, key);
  }

  function styleCard(body, d, save) {
    const c = d.card;
    const lab = c.label || {}, desc = c.desc || {}, gain = c.gain || {};
    body.innerHTML = `
      <div class="adm-fields">
        <div class="adm-row">
          <div class="adm-field" style="flex:1;min-width:180px">
            <label>Подпись (RU)</label>
            <input type="text" class="f-lab-ru" value="${esc(lab.ru || "")}" style="width:100%" />
          </div>
          <div class="adm-field" style="flex:1;min-width:180px">
            <label>Подпись (EN)</label>
            <input type="text" class="f-lab-en" value="${esc(lab.en || "")}" style="width:100%" />
          </div>
        </div>
        <div class="adm-field">
          <label>Описание (RU) — это ВИДНО НА ВИТРИНЕ, промпт сюда не вставлять</label>
          <textarea class="f-desc-ru" rows="2">${esc(desc.ru || "")}</textarea>
        </div>
        <div class="adm-field">
          <label>Описание (EN)</label>
          <textarea class="f-desc-en" rows="2">${esc(desc.en || "")}</textarea>
        </div>
        <div class="adm-field">
          <label>Что даёт (RU)</label>
          <textarea class="f-gain-ru" rows="2">${esc(gain.ru || "")}</textarea>
        </div>
        <div class="adm-row">
          <div class="adm-field">
            <label>Группа</label>
            <select class="f-group">${(styleCatalog.groups || []).map((g) =>
              `<option value="${esc(g.key)}" ${g.key === c.group ? "selected" : ""}
                >${esc((g.label && g.label.ru) || g.key)}</option>`).join("")}</select>
          </div>
          <div class="adm-field">
            <label>Тариф</label>
            <select class="f-tier">
              <option value="free" ${c.tier === "free" ? "selected" : ""}>FREE</option>
              <option value="pro" ${c.tier === "pro" ? "selected" : ""}>PRO</option>
            </select>
          </div>
        </div>
        <div class="adm-row">
          <button type="button" class="primary f-save">Сохранить карточку</button>
        </div>
        <p class="adm-note">Проверка каталога идёт ДО записи: вставленный сюда по
          ошибке промпт вернётся отказом, а не уедет на витрину.</p>
      </div>`;
    $(".f-save", body).addEventListener("click", () => save({
      label: { ru: $(".f-lab-ru", body).value, en: $(".f-lab-en", body).value },
      desc: { ru: $(".f-desc-ru", body).value, en: $(".f-desc-en", body).value },
      gain: { ru: $(".f-gain-ru", body).value, en: (d.card.gain || {}).en || "" },
      group: $(".f-group", body).value,
      tier: $(".f-tier", body).value,
    }));
  }

  function stylePrompt(body, d, save) {
    const files = (d.assets || []).filter((a) => a.kind === "promptfile");
    body.innerHTML = `
      <div class="adm-fields">
        <div class="adm-field">
          <label>Основной промпт стиля — уходит в КАЖДЫЙ кадр первым и главным блоком.
            Наружу не отдаётся ни одним публичным роутом.</label>
          <textarea class="f-prompt" rows="14">${esc(d.prompt || "")}</textarea>
          <p class="adm-note"><span class="p-len"></span>
            ${d.builtin ? " · заводской: " + (d.builtin_prompt || "").length + " симв." : ""}</p>
        </div>
        ${files.length ? `<div class="adm-field">
          <label>Взять текст из файла</label>
          <div class="adm-row">${files.map((f) =>
            `<button type="button" class="f-from" data-id="${f.id}">${esc(f.title || f.filename)}</button>`).join("")}</div>
          <p class="adm-note">Файл — источник, из которого ты переносишь текст осознанным
            нажатием. В генерацию уходит ПОЛЕ, а не файл: два места правды означали бы
            вечный вопрос «а что реально ушло в модель».</p>
        </div>` : ""}
        <div class="adm-row">
          <button type="button" class="primary f-save">Сохранить промпт</button>
          ${d.builtin ? '<button type="button" class="ghost f-orig">Показать заводской</button>' : ""}
        </div>
      </div>`;
    const ta = $(".f-prompt", body);
    const len = $(".p-len", body);
    const paint = () => { len.textContent = ta.value.length + " символов"; };
    ta.addEventListener("input", paint); paint();
    $(".f-save", body).addEventListener("click", () => save({ prompt: ta.value }));
    const orig = $(".f-orig", body);
    if (orig) orig.addEventListener("click", () => {
      ta.value = d.builtin_prompt || ""; paint();
    });
    $$(".f-from", body).forEach((b) => b.addEventListener("click", async () => {
      try {
        const r = await api(`/api/admin/styles/assets/${b.dataset.id}/text`);
        ta.value = r.text || ""; paint();
      } catch (e) { alert(e.message); }
    }));
  }

  function styleStory(body, d, save) {
    body.innerHTML = `
      <div class="adm-fields">
        <div class="adm-field">
          <label>Как этот стиль влияет на СЮЖЕТ, а не на картинку</label>
          <textarea class="f-story" rows="10">${esc(d.story_base || "")}</textarea>
          <p class="adm-note">Уходит отдельным блоком в промпты сюжета и раскадровки.
            До этого поля стиль умел влиять только на кадр: сценарий его не видел вовсе.
            Пиши тем языком, каким объяснил бы режиссёру — какие места, какие поступки,
            какая драматургия свойственна этой подаче.</p>
        </div>
        <div class="adm-row">
          <button type="button" class="primary f-save">Сохранить базу</button>
        </div>
      </div>`;
    $(".f-save", body).addEventListener("click",
      () => save({ story_base: $(".f-story", body).value }));
  }

  function styleRefs(body, d, key) {
    const groups = [
      ["poster", "Постер карточки", "один на стиль, вертикальный кадр 9:16"],
      ["loop", "Петля карточки", "2 секунды без звука"],
      ["shot", "Примеры кадров", "витрина стиля и SEO-страница"],
      ["ref", "Референсы в генерацию", "первые два уйдут в кадр ПОСЛЕ персонажей"],
      ["promptfile", "Файлы с промптами", ".txt/.md — источник для поля «Промпт»"],
    ];
    body.innerHTML = groups.map(([kind, title, hint]) => {
      const items = (d.assets || []).filter((a) => a.kind === kind);
      const massa = kind === "ref";
      return `<div class="adm-field">
        <label>${esc(title)} — ${esc(hint)}${massa
          ? ` · <b class="ref-count">${items.length}</b> референсов` : ""}</label>
        <div class="adm-assets">${items.map((a) => assetCard(a)).join("") ||
          '<span class="muted">пусто</span>'}</div>
        ${massa ? `
        <div class="ref-drop" data-kind="ref">
          <b>Перетащи сюда картинки</b>
          <span class="muted">или выбери файлы — можно сразу десятки; сервер сам ужмёт
            до 1536px / jpeg / ~800КБ</span>
          <input type="file" class="a-file ref-multi" data-kind="ref" multiple
                 accept="image/jpeg,image/png,image/webp" />
        </div>
        <div class="ref-queue"></div>` : `
        <div class="adm-row" style="margin-top:8px">
          <input type="file" class="a-file" data-kind="${kind}" />
        </div>`}
      </div>`;
    }).join("") + `<p class="adm-note">Персонаж всегда важнее стиля: стилевые референсы
      подмешиваются в кадр последними и не более двух. Иначе перерисовка вылечит один
      симптом и вернёт другой — «персонажи не похожи».</p>`;

    // Очередь массовой загрузки: файлы уходят по одному, у каждого своя
    // строка со статусом; страница перерисовывается один раз в конце.
    const uploadMany = async (files, kind) => {
      const queue = $(".ref-queue", body);
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
      styleEditor(body.closest(".s-editor") || pane(), key);
    };
    $$(".a-file", body).forEach((inp) => inp.addEventListener("change", () => {
      const files = Array.from(inp.files || []);
      if (files.length) uploadMany(files, inp.dataset.kind);
    }));
    const drop = $(".ref-drop", body);
    if (drop) {
      ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.add("over");
      }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.remove("over");
      }));
      drop.addEventListener("drop", (e) => {
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || [])
          .filter((f) => /^image\//.test(f.type));
        if (files.length) uploadMany(files, "ref");
      });
    }
    $$(".a-del", body).forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Удалить файл?")) return;
      try {
        await api("/api/admin/styles/assets/" + b.dataset.id, { method: "DELETE" });
        styleEditor(body.closest(".s-editor") || pane(), key);
      } catch (e) { alert(e.message); }
    }));
    $$(".a-gen", body).forEach((cb) => cb.addEventListener("change", async () => {
      try {
        await api("/api/admin/styles/assets/" + cb.dataset.id,
                  { method: "PATCH", body: { in_generation: cb.checked } });
      } catch (e) { alert(e.message); cb.checked = !cb.checked; }
    }));
  }

  function assetCard(a) {
    const isVideo = /\.mp4$/i.test(a.filename);
    const isText = /\.(txt|md)$/i.test(a.filename);
    const media = isText
      ? `<div class="adm-asset-file">📄 ${esc(a.filename.slice(0, 22))}</div>`
      : isVideo
        ? `<video src="${esc(a.url)}" muted></video>`
        : `<img src="${esc(a.url)}" alt="" loading="lazy" />`;
    return `<div class="adm-asset">${media}<div class="adm-asset-body">
      ${a.kind === "ref" ? `<label><input type="checkbox" class="a-gen"
        data-id="${a.id}" ${a.in_generation ? "checked" : ""} /> в генерацию</label>` : ""}
      <button type="button" class="a-del ghost" data-id="${a.id}"
        style="height:28px;font-size:12px">Удалить</button>
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
          <td><b>${esc(e.title)}</b></td><td>${esc(e.channel)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd).toFixed(3)}</td>
          ${stat(e)}</tr>`).join(""))
        + table("Движки видео", d.videos.map((e) => `<tr>
          <td>${tgl(e)}</td>
          <td><b>${esc(e.title)}</b></td><td>${esc(e.family)}</td>
          <td>${e.live ? '<span class="adm-pill on">жив</span>'
                        : '<span class="adm-pill off">ключа нет</span>'}</td>
          <td class="num">${num(e.points)}</td>
          <td class="num">$${Number(e.usd).toFixed(3)}</td>
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

  const RENDER = {
    stats: renderStats, users: renderUsers, broadcast: renderBroadcast,
    payouts: renderPayouts, ledger: renderLedger, earn: renderEarnAdmin,
    styles: (box) => renderStyles(box, "style"),
    refstyles: (box) => renderStyles(box, "reference"),
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
    const want = new URLSearchParams(location.search).get("tab");
    open(want || "stats");
  }

  boot();
})();
