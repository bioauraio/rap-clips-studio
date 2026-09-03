/* lolq.ai в Telegram — адаптер мини-аппа.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ВТОРАЯ КОПИЯ SPA. Студия — один поток: api(),
 * me, loadProject(), render(), словарь i18n. Второй документ означал бы второй
 * бут, вторую копию словаря и вторую копию логики очков — они разъедутся за
 * неделю. Поэтому мини-апп открывает ТОТ ЖЕ index.html, а всё телеграмное
 * живёт здесь и трогает app.js в трёх местах (вход, 401, лендинг).
 *
 * Обычный посетитель сайта за это не платит ничем: index.html подключает и
 * SDK Telegram, и этот файл только когда в адресе есть #tgWebAppData.
 *
 * ГЛАВНОЕ ПРАВИЛО РАБОТЫ С SDK. Метод новее 6.0, вызванный на старом клиенте,
 * НЕ бросает ошибку — он молча ничего не делает, и это выглядит как «кнопка
 * сломалась». Поэтому каждый такой вызов идёт через ok("7.7").
 */
(function () {
  "use strict";

  const W = window.Telegram && window.Telegram.WebApp;
  const TG = {
    // Только с живым initData: без него это обычный сайт, а не мини-апп —
    // иначе /api/auth/telegram-webapp гарантированно отвечал 403.
    active: Boolean(W && W.initData),
    raw: W || null,
    token: "",            // Bearer для Desktop/Web, где кука в iframe не едет
    startParam: "",
    user: null,
    bridge: "",           // conflict | expired — экран решения после моста
    other: null,
  };
  window.TGA = TG;
  if (!W) return;

  // ────────── версии ──────────
  function ok(v) {
    try { return W.isVersionAtLeast ? W.isVersionAtLeast(v) : false; }
    catch (e) { return false; }
  }
  TG.ok = ok;

  function safe(fn) { try { return fn(); } catch (e) { return undefined; } }

  // ────────── тема ──────────
  // Красим хром Telegram ПОД СЕБЯ — цветом НАШЕЙ темы, а не константой.
  // Пока здесь стоял жёстко светлый #faf7f2, тёмная тема давала битый экран:
  // панели тёмные, а фон страницы и хром клиента светлые, и между блоками
  // светились белые прогалы. Цвет берём из живого --bg: одно место правды,
  // и переключение темы внутри мини-аппа перекрашивает клиент вместе с
  // приложением. Цвета темы САМОГО Telegram по-прежнему не подставляем:
  // шестнадцать чужих переменных в нашу палитру — это мешанина.
  const SKIN = { btn: "#dd6a50", btnText: "#ffffff" };

  function skinBg() {
    try {
      const v = getComputedStyle(document.documentElement)
        .getPropertyValue("--bg").trim();
      return v || "#faf7f2";
    } catch (e) { return "#faf7f2"; }
  }

  function paint() {
    const bg = skinBg();
    safe(() => W.setHeaderColor(ok("6.9") ? bg : "bg_color"));
    safe(() => W.setBackgroundColor(bg));
    if (ok("7.10")) safe(() => W.setBottomBarColor(bg));
    const el = document.documentElement;
    el.style.setProperty("--tg-skin-bg", bg);
  }
  TG.repaint = paint;

  // ────────── вьюпорт и safe area ──────────
  // 100vh внутри Telegram неверен. Сетку строим на stable-высоте (она не
  // прыгает во время жестов), липкие панели — на живой: при открытии
  // клавиатуры меняется именно она.
  function sizes() {
    const el = document.documentElement;
    const set = (k, v) => el.style.setProperty(k, (Number(v) || 0) + "px");
    set("--tg-vh", W.viewportHeight || window.innerHeight);
    set("--tg-vh-stable", W.viewportStableHeight || W.viewportHeight || window.innerHeight);
    const top = W.contentSafeAreaInset || {};
    const area = W.safeAreaInset || {};
    set("--tg-top", (area.top || 0) + (top.top || 0));
    set("--tg-bottom", (area.bottom || 0) + (top.bottom || 0));
  }

  // ────────── haptic ──────────
  const H = () => (W.HapticFeedback && ok("6.1") ? W.HapticFeedback : null);
  TG.tap = () => safe(() => H() && H().selectionChanged());
  TG.hit = (style) => safe(() => H() && H().impactOccurred(style || "medium"));
  TG.done = () => safe(() => H() && H().notificationOccurred("success"));
  TG.oops = () => safe(() => H() && H().notificationOccurred("error"));

  // ────────── MainButton / BackButton ──────────
  // Одна точка правды: экран говорит, чего он хочет, а не дёргает SDK сам.
  let mainHandler = null;
  let backHandler = null;

  TG.main = function (text, onClick, opts) {
    const mb = W.MainButton;
    if (!mb) return;
    const o = opts || {};
    if (mainHandler) safe(() => mb.offClick(mainHandler));
    mainHandler = null;
    if (!text) { safe(() => mb.hide()); return; }
    safe(() => mb.setParams({
      text: text, color: SKIN.btn, text_color: SKIN.btnText,
      is_active: o.disabled !== true, is_visible: true,
    }));
    if (o.progress) safe(() => mb.showProgress(true));
    else safe(() => mb.hideProgress());
    if (typeof onClick === "function") {
      mainHandler = () => { TG.hit("light"); onClick(); };
      safe(() => mb.onClick(mainHandler));
    }
  };

  TG.back = function (onBack) {
    const bb = W.BackButton;
    if (!bb) return;
    if (backHandler) safe(() => bb.offClick(backHandler));
    backHandler = null;
    if (typeof onBack !== "function") { safe(() => bb.hide()); return; }
    backHandler = () => { TG.tap(); onBack(); };
    safe(() => bb.onClick(backHandler));
    safe(() => bb.show());
  };

  // Длинные ленты (раскадровка, витрина) закрываются случайным свайпом вниз
  // прямо посреди работы. Отпускаем свайп только там, где экран короткий.
  TG.swipeGuard = function (on) {
    if (!ok("7.7")) return;
    safe(() => (on ? W.disableVerticalSwipes() : W.enableVerticalSwipes()));
  };

  // Генерация уже списала очки — случайное закрытие стоит денег.
  TG.holdClose = function (on) {
    if (!ok("6.2")) return;
    safe(() => (on ? W.enableClosingConfirmation() : W.disableClosingConfirmation()));
  };

  TG.confirm = function (text, cb) {
    if (ok("6.2") && W.showConfirm) { safe(() => W.showConfirm(text, cb)); return; }
    cb(window.confirm(text));
  };

  TG.alert = function (text) {
    if (ok("6.2") && W.showAlert) { safe(() => W.showAlert(text)); return; }
    window.alert(text);
  };

  // Внешняя ссылка (OAuth, «открыть на компьютере»): openLink уводит в
  // системный браузер и мини-апп НЕ закрывает. Работает только из
  // обработчика клика — после await метод молча не срабатывает.
  TG.openLink = function (url) {
    if (ok("6.1") && W.openLink) { safe(() => W.openLink(url)); return; }
    window.open(url, "_blank", "noopener");
  };

  // ────────── вход ──────────
  let signing = null;

  async function post(path, body, opts) {
    const headers = { "content-type": "application/json" };
    if (TG.token) headers.authorization = "Bearer " + TG.token;
    const res = await fetch(path, {
      method: (opts && opts.method) || "POST", headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error((data && (data.detail || data.error)) || "tg_auth_failed");
      err.code = (data && data.error) || "";
      err.status = res.status;
      throw err;
    }
    return data;
  }

  /* Автовход. Ни /api/start, ни форма пароля внутри Telegram не вызываются
     никогда: человек уже опознан платформой. */
  TG.signIn = function (force) {
    if (signing && !force) return signing;
    signing = (async () => {
      const r = await post("/api/auth/telegram-webapp", {
        init_data: W.initData || "",
        start_param: TG.startParam,
      });
      TG.token = r.token || "";
      TG.user = r.user || null;
      TG.bridge = r.bridge || "";
      TG.other = r.other || null;
      TG.merged = r.merged || "";
      // Язык из профиля Telegram — готовый сигнал, экран выбора не нужен.
      // Только если человек ещё не выбирал язык руками: его выбор важнее
      // языка клиента Telegram.
      let picked = "";
      try { picked = localStorage.getItem("lolq_lang") || ""; } catch (e) { picked = ""; }
      if (r.lang && typeof setLang === "function" && !picked) {
        safe(() => setLang(r.lang));
      }
      return r;
    })().catch((e) => { signing = null; throw e; });
    return signing;
  };

  // Заголовок для api() из app.js: на телефоне работает и кука, в Desktop/Web
  // (iframe) — только это.
  TG.authHeader = function () {
    return TG.token ? { authorization: "Bearer " + TG.token } : null;
  };

  // ────────── вход через Яндекс ID / Google из мини-аппа ──────────
  // Уходить на oauth.yandex.ru ВНУТРИ webview нельзя — Telegram блокирует
  // методы Mini App с чужого origin, и window.Telegram.WebApp после редиректа
  // мёртв. Поэтому браузер снаружи + две независимые ветки возврата: ссылка
  // t.me (телефон) и поллинг статуса (десктоп, где ссылку некому обработать).
  TG.externalLogin = async function (provider, onState) {
    const r = await post("/api/tg/link-start", {
      init_data: W.initData || "", provider: provider,
      ref: (typeof refCode === "string" ? refCode : ""),
    });
    TG.openLink(r.url);            // строго синхронно после клика
    const until = Date.now() + Math.max(60, (r.expires_in || 300)) * 1000;
    while (Date.now() < until) {
      await new Promise((res) => setTimeout(res, 2000));
      if (document.hidden) continue;
      let st;
      try { st = await post("/api/tg/link-status?nonce=" + encodeURIComponent(r.nonce), null, { method: "GET" }); }
      catch (e) { continue; }
      if (st.status === "done") {
        if (st.token) TG.token = st.token;
        if (onState) onState("done", st);
        return st;
      }
      if (st.status === "conflict") { if (onState) onState("conflict", st); return st; }
      if (st.status === "expired") { if (onState) onState("expired", st); return st; }
    }
    if (onState) onState("timeout", {});
    return { status: "timeout" };
  };

  // ────────── оплата звёздами ──────────
  /* status "paid" — НЕ основание выдавать очки: выдача идёт строго по
     successful_payment на сервере. Здесь по "paid" мы только ждём и
     переспрашиваем счёт. */
  TG.pay = function (kind, id, onState) {
    return post("/api/tg/invoice", { kind: kind, plan: id, pack: id })
      .then((r) => new Promise((resolve) => {
        const link = r.url;
        if (!link) { onState("failed", { error: "no_link" }); resolve("failed"); return; }
        const done = (st) => { onState(st, r); resolve(st); };
        if (ok("6.1") && W.openInvoice) {
          safe(() => W.openInvoice(link, (status) => {
            if (status === "paid") { TG.done(); done("paid"); }
            else if (status === "pending") done("pending");
            else if (status === "cancelled") { TG.hit("light"); done("cancelled"); }
            else { TG.oops(); done("failed"); }
          }));
        } else {
          // Старый клиент: счёт открывается отдельным окном, обратной связи
          // от него нет — дальше решает поллинг кабинета.
          safe(() => W.openTelegramLink(link));
          done("pending");
        }
      }));
  };

  /* Ждём, пока оплата доедет до нас апдейтом бота. Проверяем по кабинету:
     это единственная правда о тарифе и очках. */
  TG.waitGrant = async function (before, seconds) {
    const until = Date.now() + (seconds || 90) * 1000;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 2000));
      if (document.hidden) continue;
      let a;
      try { a = await post("/api/account", null, { method: "GET" }); } catch (e) { continue; }
      if (a.plan !== before.plan || Number(a.points) !== Number(before.points)) {
        TG.done();
        return a;
      }
    }
    return null;
  };

  // ────────── жизненный цикл ──────────
  TG.startParam = safe(() => (W.initDataUnsafe && W.initDataUnsafe.start_param) || "") || "";
  TG.user = safe(() => (W.initDataUnsafe && W.initDataUnsafe.user) || null) || null;

  // Реферал из ссылки амбассадора t.me/<bot>/app?startapp=ref_КОД — тем же
  // путём, что ?ref= в вебе.
  TG.refFromStart = function () {
    return TG.startParam.indexOf("ref_") === 0 ? TG.startParam.slice(4) : "";
  };

  TG.boot = function () {
    safe(() => W.ready());
    safe(() => W.expand());
    document.documentElement.classList.add("tg");
    // Класс производительности Android: на LOW liquid glass превращает студию
    // в слайд-шоу. Правила уже есть — те же, что для reduced-transparency.
    if (/(^|[^A-Za-z])LOW($|[^A-Za-z])/.test(navigator.userAgent || "")) {
      document.documentElement.classList.add("tg-lowend");
    }
    paint();
    sizes();
    safe(() => W.onEvent("themeChanged", paint));
    safe(() => W.onEvent("viewportChanged", sizes));
    safe(() => W.onEvent("safeAreaChanged", sizes));
    safe(() => W.onEvent("contentSafeAreaChanged", sizes));
    // Хеш с tgWebApp* стираем: параметры уже разобраны SDK, а роутер студии
    // (и будущий nav.js) читает location.hash и на этой строке сойдёт с ума.
    if (String(location.hash || "").indexOf("tgWebApp") >= 0) {
      safe(() => history.replaceState(null, "", location.pathname + location.search));
    }
    // Поллинг конвейера в свёрнутом приложении жжёт батарею впустую.
    if (ok("8.0")) {
      safe(() => W.onEvent("activated", () => { TG.awake = true; }));
      safe(() => W.onEvent("deactivated", () => { TG.awake = false; }));
    }
    TG.awake = true;
  };

  // Бутим ТОЛЬКО когда Telegram действительно нас опознал. Если SDK
  // поднялся, а initData пуст (так бывает у кнопок вне личного чата), вход
  // сделать всё равно нечем — и тогда правильнее остаться обычным сайтом,
  // чем показать пустой экран с правилами мини-аппа.
  if (TG.active) TG.boot();
})();
