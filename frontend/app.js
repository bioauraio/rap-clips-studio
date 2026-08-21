// Микс стилей: 1–3 пресета чекбокс-чипами, ПЕРВЫЙ выбранный — основа.
// 1 пресет — чистый value; 2–3 — value основы + короткие выжимки остальных.
function styleExcerpt(value) {
  // Первые ~2 предложения промпта: ими дополнительный стиль «подмешивается».
  const m = value.match(/[^.]+\./g);
  return m ? m.slice(0, 2).join("").trim() : value;
}

// Подписи пресетов живут в словаре (i18n.js), в коде ходит только key.
function styleLabel(p) { return t(`styles.${p.key}.label`) || p.key; }
function styleDesc(p) { return t(`styles.${p.key}.desc`); }

function buildFusionStyle(keys) {
  const chosen = keys
    .map((k) => STYLE_PRESETS.find((p) => p.key === k))
    .filter(Boolean);
  if (!chosen.length) return "";
  if (chosen.length === 1) return chosen[0].value;
  const extras = chosen.slice(1).map((p) => styleExcerpt(p.value));
  return chosen[0].value + "\n\nBlend in elements of: " + extras.join(" ");
}

function styleKeysFromValue(value) {
  // Восстановление выбора из сохранённого промпта: основа хранится полным
  // value (идёт первой), дополнительные — выжимками (идут следом).
  if (!value) return [];
  const base = STYLE_PRESETS.filter((p) => value.includes(p.value)).map((p) => p.key);
  const extras = STYLE_PRESETS
    .filter((p) => !value.includes(p.value) && value.includes(styleExcerpt(p.value)))
    .map((p) => p.key);
  return [...base, ...extras];
}

function buildStylePicker(container, current, onChange) {
  container.innerHTML = "";
  // Порядок выбора важен: order[0] — основа микса.
  const order = styleKeysFromValue(current);

  const chipsBox = document.createElement("div");
  chipsBox.className = "style-chips";
  const desc = document.createElement("details");
  desc.className = "style-desc";
  const descSummary = document.createElement("summary");
  descSummary.textContent = t("stylePicker.descSummary");
  const descBody = document.createElement("div");
  descBody.className = "style-desc-body muted";
  desc.append(descSummary, descBody);

  const sync = (fireChange) => {
    $$(".style-chip", chipsBox).forEach((el) => {
      const on = order.includes(el.dataset.key);
      el.classList.toggle("on", on);
      el.classList.toggle("base", order[0] === el.dataset.key);
      el.querySelector("input").checked = on;
    });
    const chosen = order
      .map((k) => STYLE_PRESETS.find((p) => p.key === k))
      .filter(Boolean);
    descBody.textContent = chosen.length
      ? chosen.map((p, i) => `${i ? "＋" : "★"} ${styleLabel(p)} — ${styleDesc(p)}`).join("\n")
      : t("stylePicker.none");
    desc.classList.toggle("hidden", !chosen.length);
    if (fireChange) onChange(buildFusionStyle(order));
  };

  for (const p of STYLE_PRESETS) {
    const label = document.createElement("label");
    label.className = "style-chip";
    label.dataset.key = p.key;
    label.title = styleDesc(p);
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.addEventListener("change", () => {
      if (cb.checked) {
        if (order.length >= 3) { cb.checked = false; return; } // максимум 3
        order.push(p.key);
      } else {
        const i = order.indexOf(p.key);
        if (i >= 0) order.splice(i, 1);
      }
      sync(true);
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(styleLabel(p)));
    chipsBox.appendChild(label);
  }
  container.appendChild(chipsBox);
  container.appendChild(desc);
  if (current && !order.length) {
    const note = document.createElement("div");
    note.className = "muted";
    note.textContent = t("stylePicker.custom");
    container.appendChild(note);
  }
  // Первичная отрисовка БЕЗ onChange: кастомный стиль не затираем пустотой.
  sync(false);
}

// Стили клипов: выбор ТОЛЬКО из пресетов — каждый промпт полностью
// срежиссирован (эстетика, свет, палитра, фактура), чтобы кадры внутри
// трека не разъезжались. value уходит в промпты as-is.
const STYLE_PRESETS = [
  {
    key: "ghibli",
    value: "Hand-painted Studio Ghibli style anime inspired by Hayao Miyazaki films, vertical 9:16. Soft watercolor backgrounds with visible brush texture, lush painterly clouds and greenery, warm golden-hour sunlight or cozy lamp glow through windows. Gentle pleasant palette: warm cream, soft sky blue, grass green, sunset amber — nothing acidic, everything nostalgic and comforting. Characters drawn in classic 2D anime cel style with simple expressive faces, natural relaxed poses, wind gently moving hair and clothes. Quiet magical realism mood: dust motes in sunbeams, steam from food, fireflies, rustling leaves. Every frame feels like a warm memory — calm, humane, a little wistful. No harsh shadows, no neon, no 3D render look, no text."
  },
  {
    key: "pixar",
    value: "High-end 3D animated feature film style like Pixar and DreamWorks, vertical 9:16, ultra HD render. Rounded appealing character design with large expressive eyes, soft subsurface scattering skin, detailed hair and fabric simulation. Rich cinematic lighting: warm key light, colorful bounce light, gentle rim light separating character from background. Vibrant but tasteful saturated palette, shallow depth of field with creamy bokeh, subtle film grain. Polished storytelling composition, emotional facial expressions. No text, no watermark."
  },
  {
    key: "shinkai",
    value: "Modern cinematic anime film style inspired by Makoto Shinkai, vertical 9:16. Breathtaking hyper-detailed backgrounds: glowing skies with layered clouds, lens flares, glittering city lights, rain droplets catching light. Emotional color grading with luminous gradients — deep blues into warm oranges and pinks. Crisp 2D character animation with delicate lighting on hair and eyes. Dramatic sense of scale: vast skies over small human figures. Melancholic-hopeful atmosphere. No text, no watermark."
  },
  {
    key: "cinema",
    value: "Photorealistic cinematic film still, vertical 9:16, shot on ARRI Alexa with anamorphic lenses. Natural skin texture and imperfections, real physical lighting: practical sources, soft window light or hard sun with true shadows. Film color grading with gentle teal-orange balance, subtle 35mm grain, shallow depth of field. Documentary-authentic staging: real locations, lived-in details, honest emotion on faces. No CGI look, no oversharpening, no text."
  },
  {
    key: "flat2d",
    value: "Bold flat 2D vector animation style, vertical 9:16. Clean geometric shapes, thick confident outlines, limited harmonious palette of 4-6 colors per scene, flat color fills with simple two-tone shading. Playful exaggerated proportions and snappy poses, minimal but expressive faces. Mid-century modern and contemporary motion-design influence: textured paper grain overlay, simple patterned backgrounds. Cheerful, graphic, poster-like compositions. No gradients overload, no 3D, no text."
  },
  {
    key: "noir",
    value: "Gritty noir graphic novel style like Sin City and Batman animated classics, vertical 9:16. High-contrast chiaroscuro: deep ink-black shadows swallowing half of every frame, stark white or single warm accent color (red neon, amber streetlight) cutting through darkness. Heavy dramatic hatching and ink texture, rain-slick streets reflecting light, cigarette smoke curling through venetian-blind shadows. Hard-boiled atmosphere: trench coats, brooding silhouettes, low camera angles. Monochrome with one accent color per scene. No text, no captions."
  },
  {
    key: "longheads",
    value: "1990s analog film street photography, scanned 35mm frame with heavy grain and slightly faded Kodak colors, candid documentary framing. Surreal characters with elongated non-human heads on long necks (ostrich-like, greyhound, pale alien with almond eyes, porcelain mannequin mask) on completely ordinary human bodies in baggy 90s streetwear: oversized denim jackets, loose white shirts, wide pants, chunky chains, plastic grocery bags, coffee cups. Deadpan poses, mundane everyday activities, nobody reacts to the surrealism. Locations: laundromats, convenience stores, crosswalks, chain-link fences, boxy 80s sedans, night streets with neon signage and wet asphalt reflections. Muted denim-blue palette with warm cream skin tones and red/neon accents, harsh daylight or direct flash by day, deep black sky and neon glow by night. Vertical 9:16, no text."
  },
  {
    key: "embroidery",
    value: "Hand-embroidered thread-art illustration: the entire image is stitched in dense chain-stitch and satin-stitch embroidery with clearly visible thread loops and fiber texture, like a lovingly hand-sewn patch. Background of warm cream felt and kraft cardboard with soft fabric grain. Bold simplified shapes with clean dark outlines; characters rendered in colored thread, directional stitching following the forms of faces, hair and clothes; flames, smoke and effects also stitched in swirling orange-red-amber threads. Cozy handcrafted feel, slightly naive proportions, saturated yarn colors against the neutral textile background, tiny loose thread ends visible. Vertical 9:16, no text."
  },
  {
    key: "spike",
    value: "Cinematic photorealistic night scene shot on vintage anamorphic lenses, warm tungsten and smoky haze, heavy 35mm film grain with teal-and-amber grade. Post-Soviet Russian setting reimagined with subtle Atomic Heart retrofuturism: khrushchyovka courtyards, cramped old Lada interiors, kiosks, snow-dusted parking lots, delivery couriers in Ozon blue jackets and yellow Yandex thermo-bag backpacks. Photorealistic larger-than-life characters and deadpan cartoon-headed cameos ride together in old cars filled with smoke, count worn banknotes in shabby ornate bedrooms, stare into the lens with calm swagger. Golden chains, tracksuit textures, cigarette smoke curling in headlight beams, wet asphalt reflections. Everyday grit filmed like an epic music video, nobody reacts to the surreal cameos. Vertical 9:16, no text."
  },
  {
    key: "munir",
    value: "Gulf street documentary photography with direct on-camera flash at night and harsh daylight, ultra-wide fisheye lens distortion, saturated 35mm film colors with crushed shadows. Middle Eastern everyday swagger played deadpan: elderly men in red-checkered ghutra headdress and white thobes grinning as they push a fist with a chunky custom name-ring straight into the lens, women in black abayas fueling a black G63 at a midnight gas station, a Doberman with a heavy chain collar lunging toward the camera, corner grocery shops with Arabic signage and packed shelves, plastic chairs, dates and spice jars. Objects thrust toward the ultra-wide lens so they loom huge in the foreground, faces close and warped at the edges, flash bleaching the foreground against deep black night. Humor and quiet confidence, mundane life shot like a rap video. Vertical 9:16, no text."
  },
  {
    key: "fanuel",
    value: "Hyperreal cinematic surreal fashion film, epic single-frame worldbuilding. One elegant figure in a sharply tailored suit of a single bold color (burnt orange, saffron yellow, deep crimson) stands or walks calmly inside an impossible landscape: on the open sea at dusk, along the rings of a giant planet, across endless dunes, under colossal celestial bodies. Recurring fire motif — burning umbrellas, floating flames, embers, fire reflected in water. Deadpan composed poses, quiet confidence, no reaction to the impossible. Painterly dusk palettes: violet-pink-orange gradient skies, deep ocean blues, warm firelight against cool darkness; volumetric cinematic lighting, anamorphic depth, ultra-detailed photorealistic rendering with epic scale contrast between the small figure and the vast world. Vertical 9:16, no text."
  },
  {
    key: "clay",
    value: "Handcrafted claymation stop-motion style (Aardman/Laika vibe): visible fingerprints in plasticine, slightly imperfect frame-to-frame jitter, miniature set with real fabric and cardboard props, warm practical lighting, shallow depth of field macro look, expressive oversized eyes, vertical 9:16, no text."
  },
  {
    key: "punkrf",
    value: "Hyperreal Russian street found-footage: night dashcam, GoPro, phone or CCTV camera look with heavy VHS grain, analog noise and motion blur; harsh headlights, red neon gas-station canopies, wet asphalt, dense traffic with glowing tail lights, grey soviet panel blocks, ruined brick factories, dusty supercars in wastelands. One absurd event unfolds in the middle of mundane Russian reality — animals rearing between cars, flying couriers, delivery drones, aliens in queues — filmed like an accidental viral video: documentary believability, realistic physics, nobody poses, aggressive dynamic framing, violent handheld shake, strobing flash by night. Muted cold palette with red neon accents. Vertical 9:16, no text."
  },
  {
    key: "dreamclad",
    value: "1990s American hood-cinema still, shot on grainy 35mm film: faded low-contrast color grade with warm orange-brown skin tones and dusty teal shadows (or deep-grain black-and-white), heavy film grain, soft halation, subtle gate weave and VHS-era imperfections. Brick-block New York / LA streets of the 90s — bodegas with graffiti, chain-link fences, stone staircases, boxy sedans and vintage Cadillacs — or night-time mansion gates and museum halls lit by warm tungsten windows and headlights. Young men in white tank tops, bandana masks, hoodies and baggy denim; crowds dressed identically like a uniform; recurring icons of money stacks, doves, crosses, candles and classical statues — sacred mixed with street. Frontal, symmetric, almost ceremonial compositions, subjects staring straight into the lens, or candid through-the-windshield documentary angles; overexposed hazy daylight or moody night backlight. Cinematic, nostalgic, quietly menacing, music-video energy. No clean digital look, no HDR, no modern cars or clothing, no neon cyberpunk, no glossy skin, no watermarks. Vertical 9:16, no text."
  },
  {
    key: "katsumi",
    value: "Hyperrealistic absurdist found-footage aesthetic: a deadpan surreal protagonist (animal or costumed figure) doing mundane human things with total seriousness, shot like accidental amateur documentary footage from the 1990s–2000s — handheld camcorder or disposable-camera look with harsh direct on-camera flash at night, or flat overcast daylight; heavy analog film grain, VHS noise, slight chromatic aberration, motion blur, fisheye or wide-angle distortion, tilted imperfect framing with the subject too close to the lens, often staring straight into the camera. Muted dirty palette of swampy olive, tobacco brown, dusty grey and desaturated flesh tones, background falling into deep black shadow, with one rare accent color (neon sign, police lights, orange robe, gold chain). Gritty tactile textures: wet fur, greasy pavement, cigarette smoke, scuffed metal, cheap floral motel interiors, cluttered convenience-store shelves. Cinematic realism, not cartoon — everything must look physically shot, grimy street-punk mood, crime-scene-snapshot lighting, deadpan comedy with zero wink. Avoid: clean digital sharpness, glossy studio light, saturated candy colors, cartoon or 3D-render look, symmetry, beauty-filter smoothness. Vertical 9:16, no text."
  },
];

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

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body && !(opts.body instanceof FormData) ? { "content-type": "application/json" } : undefined,
      body: opts.body instanceof FormData ? opts.body : opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new ApiError({ error: "network" }, 0);
  }
  if (res.status === 401) { showLogin(); throw new ApiError({ error: "unauthorized" }, 401); }
  if (!res.ok) throw new ApiError(await res.json().catch(() => ({})), res.status);
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
    const fromUrl = (new URLSearchParams(location.search).get("ref") || "").trim();
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

function showWelcome() {
  renderRefBanner();
  $("#welcome").classList.remove("hidden");
  $("#login").classList.add("hidden");
  $("#app").classList.add("hidden");
  // Главная — полноценная витрина: тексты, шаги, тарифы и шкала очков
  // собираются в renderLanding() (низ файла) из словаря I18N (i18n.js).
  renderLanding();
}
function showLogin() {
  $("#welcome").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
}
function showApp() {
  $("#welcome").classList.add("hidden");
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  renderUserBar();
  loadProject();
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
  if (me && me.authed) { showApp(); return; }
  await api("/api/start" + (refCode ? `?ref=${encodeURIComponent(refCode)}` : ""), { method: "POST" });
  me = await api("/api/me");
  showApp();
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
  $("#modal-overlay .modal-card").classList.toggle("wide", Boolean(opts.wide));
  buildBody(body);
  $("#modal-overlay").classList.remove("hidden");
}

function closeModal() {
  $("#modal-overlay").classList.add("hidden");
  $("#modal-overlay .modal-card").classList.remove("wide");
  const body = $("#modal-body");
  body.innerHTML = "";
  body.removeAttribute("data-char-id");
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
  { key: "ref" },
  { key: "payouts", admin: true },
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
      ref: renderRefPane, payouts: renderPayoutsPane,
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
  });
}

// ────────── вкладка «Аккаунт» ──────────
async function renderAccountPane(pane) {
  let a;
  try { a = await api("/api/account"); } catch (e) { return accFail(pane, e); }
  const linked = a.linked || {};
  const chips = [["telegram", "Telegram"], ["google", "Google"],
                 ["yandex", t("account.yandex")], ["password", t("account.password")]];
  const initial = (a.name || "?").trim().charAt(0).toUpperCase() || "?";
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
    <div class="acc-stats">
      <div class="acc-stat"><b>${escHtml(a.plan_title || "FREE")}</b><span>${escHtml(t("account.statPlan"))}</span></div>
      <div class="acc-stat"><b>${a.plan_until ? escHtml(fmtDate(a.plan_until)) : "—"}</b><span>${escHtml(t("account.statUntil"))}</span></div>
      <div class="acc-stat"><b>${escHtml(tNum(a.points))}</b><span>${escHtml(t("account.statPoints"))}</span></div>
      <div class="acc-stat"><b>${escHtml(tNum(a.projects))}</b><span>${escHtml(t("account.statProjects"))}</span></div>
    </div>
    ${a.plan_note ? `<p class="muted acc-note">${escHtml(a.plan_note)}</p>` : ""}
    <label>${escHtml(t("account.logins"))}</label>
    <div class="acc-chips">
      ${chips.map(([k, t]) => `<span class="acc-chip${linked[k] ? " on" : ""}">${linked[k] ? "✓" : "○"} ${t}</span>`).join("")}
    </div>
    <div class="row acc-actions"></div>
    <span class="acc-msg status"></span>`;

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
    note.textContent = t("account.autopayOffNote");
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

// ────────── вкладка «Тариф» ──────────
async function renderPlanPane(pane) {
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
  const planMoney = (p) => (p.usd != null ? ldMoney(p.usd) : fmtRub((Number(p.price) || 0) * 100));
  const planPaid = (p) => (p.usd != null ? Number(p.usd) : Number(p.price)) > 0;
  pane.innerHTML = `
    <div class="acc-plans">
      ${(data.plans || []).map((p) => {
        const isCur = p.id === current;
        return `<div class="acc-plan${isCur ? " on" : ""}">
          <div class="acc-plan-top"><b>${escHtml(p.title)}</b><span>${escHtml(planMoney(p))}</span></div>
          <p class="acc-plan-note">${escHtml(planNote(p))}</p>
          <p class="acc-plan-points">${escHtml(t("plan.pointsLine", { n: tNum(p.points) }))}</p>
          ${isCur
            ? `<span class="acc-plan-cur">${escHtml(t("plan.current"))}</span>`
            : (planPaid(p)
              ? `<button type="button" class="primary acc-pay" data-plan="${escHtml(p.id)}"
                  >${escHtml(t("plan.pay"))}</button>`
              : `<span class="acc-plan-cur muted">${escHtml(t("plan.basic"))}</span>`)}
        </div>`;
      }).join("")}
    </div>
    <label>${escHtml(t("plan.promoLabel"))}</label>
    <input class="acc-promo" placeholder="${escHtml(t("plan.promoPh"))}" />
    <span class="acc-msg status"></span>`;

  // Промокод из реферальной ссылки подставляем сам: человек уже пришёл по нему.
  const promo = $(".acc-promo", pane);
  if (refCode) promo.value = refCode;

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
        const r = await api("/api/billing/create", {
          method: "POST",
          body: { kind: "plan", plan: btn.dataset.plan, period: "month", promo: promo.value.trim() },
        });
        if (r && r.url) { window.location.href = r.url; return; }
        throw new Error(t("plan.noUrl"));
      } catch (e) {
        btn.disabled = false;
        accMsg(pane, errText(e), "error");
      }
    });
  });
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
  // До отрисовки: автосборка может поставить клип в очередь, и карточка
  // должна показать это сразу, а поллер — не погаснуть.
  await autoAssembleTick();
  render();
  schedulePoll();
}

function renderProjectBar() {
  const sel = $("#project-select");
  sel.innerHTML = "";
  for (const p of projects) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name;
    if (p.id === activeProjectId) o.selected = true;
    sel.appendChild(o);
  }
  $("#project-kind").textContent = t(project.kind === "single" ? "top.kindSingle" : "top.kindAlbum");
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
      <div class="kind-cards">
        <button type="button" class="kind-card on" data-kind="album">
          <b>${escHtml(t("modal.newProject.album"))}</b><span class="muted">${escHtml(t("modal.newProject.albumNote"))}</span>
        </button>
        <button type="button" class="kind-card" data-kind="single">
          <b>${escHtml(t("modal.newProject.single"))}</b><span class="muted">${escHtml(t("modal.newProject.singleNote"))}</span>
        </button>
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

function schedulePoll() {
  clearTimeout(pollTimer);
  const busy =
    project.story_status === "queued" || project.story_status === "running" ||
    project.tracks.some(
      (t) => ["queued", "running"].includes(t.scenes_status) ||
        ["queued", "running"].includes(t.storyboard_status) ||
        ["queued", "running"].includes(t.clip_status) ||
        (t.scenes || []).some(sceneBusy),
    );
  if (busy) pollTimer = setTimeout(loadProject, 3000);
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

function statusLabel(status, doneWord) {
  if (status === "queued") return { text: t("status.queued"), cls: "" };
  if (status === "running") return { text: t("status.running"), cls: "" };
  if (status === "error") return { text: t("status.error"), cls: "error" };
  if (status === "done") return { text: doneWord || t("status.done"), cls: "done" };
  return { text: "", cls: "" };
}

function render() {
  renderProjectBar();
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
  project.tracks.forEach((tr) => container.appendChild(renderTrack(tr)));
}

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
    setup: tr.style && tr.audio_duration_sec ? "done"
      : (tr.title || tr.style || tr.audio_duration_sec || tr.lyrics || tr.comment) ? "part" : "empty",
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
  const cur = trackStages.get(tr.id);
  return STAGES.includes(cur) ? cur : defaultStage(tr);
}

function setStage(card, key) {
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
  $(".t-style", card).value = tr.style;
  buildStylePicker($(".t-style-picker", card), tr.style, (v) => { $(".t-style", card).value = v; });
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
      await api(`/api/tracks/${tr.id}/generate-all-frames`, { method: "POST" });
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
      if (!confirm(t("track.allVideosConfirm", { n: vidTodo }))) return;
      try {
        await api(`/api/tracks/${tr.id}/generate-all-videos`, { method: "POST" });
      } catch (e) { fail(e); }
      await loadProject();
    });
  }
  const genBtn = $(".gen-scenes", card);
  const busy = tr.scenes_status === "queued" || tr.scenes_status === "running";
  genBtn.disabled = busy || !project.story;
  genBtn.title = project.story ? "" : t("track.genScenesTitle");
  genBtn.addEventListener("click", () => genScenes(tr.id));

  // ⚡ Супергенерация: весь конвейер одним нажатием (кнопка живёт в шапке).
  const superBtn = $(".s-supergen", card);
  const superBusy = ["queued", "running"].includes(tr.supergen_status);
  superBtn.disabled = superBusy || !tr.audio_duration_sec;
  superBtn.textContent = superBusy ? t("track.supergenBusy") : t("track.supergen");
  superBtn.addEventListener("click", () => openSupergenModal(tr));
  const superNote = $(".supergen-note", card);
  superNote.textContent = tr.supergen_note || "";
  superNote.className = "status supergen-note " +
    (tr.supergen_status === "error" ? "error" : tr.supergen_status === "done" ? "done" : "");
  if (superBusy && !window.__supergenPoll) {
    window.__supergenPoll = setInterval(async () => {
      const p = await api(`/api/project?project_id=${activeProjectId}`).catch(() => null);
      const tr = p && (p.tracks || []).find((x) => ["queued", "running"].includes(x.supergen_status));
      if (!tr) {
        clearInterval(window.__supergenPoll);
        window.__supergenPoll = null;
      }
      await loadProject();
    }, 15000);
  }
  const st = statusLabel(tr.scenes_status, t("track.scenesDone", { n: tr.scenes_count }));
  const stEl = $(".scenes-status", card);
  stEl.textContent = st.text || (tr.scenes_count ? t("track.scenesCount", { n: tr.scenes_count }) : "");
  stEl.className = "status " + st.cls;

  // Лист раскадровки: весь клип одной картинкой — до покадровой отрисовки.
  const sbStatus = statusLabel(tr.storyboard_status);
  const sbStatusEl = $(".sb-status", card);
  sbStatusEl.textContent = sbStatus.text;
  sbStatusEl.className = "status " + sbStatus.cls;
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

  // Сцены двумя лентами: «Раскадровка» — кадры, «Анимация» — видео.
  const boardBox = $(".scenes-board", card);
  const animBox = $(".scenes-anim", card);
  (tr.scenes || []).forEach((s) => {
    boardBox.appendChild(renderScene(s, audioEl, "board"));
    // В «Анимацию» карточка попадает только когда видео есть или генерится.
    if (s.video_url || ["queued", "running", "error"].includes(s.video_status)) {
      animBox.appendChild(renderScene(s, audioEl, "anim"));
    }
  });
  if (!animBox.children.length) {
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.padding = "8px 4px";
    hint.textContent = t("track.animEmpty");
    animBox.appendChild(hint);
  }
  $$(".strip-wrap", card).forEach(bindStrip);

  // ── витрина клипа внизу карточки: видна на любом этапе, всегда актуальна
  const clipStatus = statusLabel(tr.clip_status, t("track.clipDone"));
  const clipStatusEl = $(".clip-status", card);
  clipStatusEl.textContent = clipStatus.text;
  clipStatusEl.className = "status " + clipStatus.cls;
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

  const grid = $(".final-grid", card);
  const withVideo = (tr.scenes || []).filter((s) => s.video_url);
  if (!withVideo.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = t("track.finalEmpty");
    grid.appendChild(empty);
  }
  withVideo.forEach((s) => {
    const cell = document.createElement("div");
    cell.className = "final-cell";
    const v = document.createElement("video");
    v.src = s.video_url;
    v.controls = true;
    v.loop = true;
    v.preload = "metadata";
    const cap = document.createElement("span");
    cap.className = "muted";
    cap.textContent = t("scene.cap", { n: s.position, time: fmtTime(s.start_sec) })
      + (s.approved ? t("scene.capApproved") : "");
    cell.append(v, cap);
    grid.appendChild(cell);
  });

  return card;
}

// Подсвечивает кадр под текущей секундой трека и без дёрганий скроллит его
// в видимую область — плеер "листает" раскадровку сам, по факту звучания.
function highlightActiveScene(trackCard, currentTime) {
  const cards = $$(".scene-card", trackCard);
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
  const imgStatusEl = $(".s-image-status", card);
  imgStatusEl.textContent = imgStatus.text;
  imgStatusEl.className = "status " + imgStatus.cls;
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
  framesBtn.addEventListener("click", () => genSceneFrames(s.id));
  const firstBtn = $(".s-gen-first", card);
  const lastBtn = $(".s-gen-last", card);
  if (firstBtn) {
    firstBtn.disabled = imgBusy;
    firstBtn.addEventListener("click", () => genSceneFrames(s.id, "first"));
  }
  if (lastBtn) {
    lastBtn.disabled = imgBusy;
    lastBtn.addEventListener("click", () => genSceneFrames(s.id, "last"));
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
  // Движок видео: системный select оставлен источником правды (на его .value
  // висит генерация), а виден — сегментный переключатель в стиле студии.
  // Движок КАДРОВ: у платных тарифов это Nano Banana (до 14 отдельных
  // референсов и нативная вертикаль), у бесплатного — ChatGPT-шлюз по подписке.
  // Показываем реальную доступность: тариф может обещать платный движок,
  // а ключа не быть — тогда честно активен шлюз.
  const imgSeg = $(".s-image-seg", card);
  if (imgSeg) {
    imgSeg.innerHTML = "";
    const imgList = (providers.image_engines || []).filter((e) => e.live !== false);
    if (imgList.length > 1) {
      let curImg = s.image_engine || providers.image_engine || (imgList[0] && imgList[0].id);
      if (!imgList.some((e) => e.id === curImg)) curImg = imgList[0].id;
      const syncImg = () => $$(".img-chip", imgSeg)
        .forEach((el) => el.classList.toggle("on", el.dataset.engine === curImg));
      imgList.forEach((e) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "img-chip";
        chip.dataset.engine = e.id;
        chip.textContent = e.title;
        chip.title = `${e.title} — ${e.frames_cost} ${t("scene.pointsSuffix") || "очков за кадры"}`;
        chip.addEventListener("click", () => { curImg = e.id; imgSeg.dataset.engine = e.id; syncImg(); });
        imgSeg.appendChild(chip);
      });
      imgSeg.dataset.engine = curImg;
      syncImg();
    }
  }

  const provSel = $(".s-provider", card);
  const provSeg = $(".s-provider-seg", card);
  if (provSel) {
  provSel.innerHTML = "";
  // Источник правды — video_engines из /api/providers: там КОНКРЕТНЫЕ модели
  // с настоящими именами и ценой. Раньше рисовался список семейств, и всё,
  // что не seedance, подписывалось «Grok» — поэтому Kling выглядел вторым Grok.
  const engineList = (providers.video_engines || []).filter((e) => e.live !== false);
  if (engineList.length) {
    engineList.forEach((e) => {
      const opt = document.createElement("option");
      opt.value = e.family || "grok";
      opt.dataset.engine = e.id;
      opt.textContent = `${e.title} · ${e.scene_cost}`;
      provSel.appendChild(opt);
    });
  } else {
    (providers.video || ["grok"]).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.dataset.engine = "";
      opt.textContent = t(p === "seedance" ? "scene.providerSeedance" : "scene.providerGrok");
      provSel.appendChild(opt);
    });
  }
  provSel.value = s.video_provider;
  // Сцену сняли на движке, который сейчас недоступен (free-тариф оставляет
  // только Grok): у select'а не осталось выбранного пункта, и .value пуст.
  // Ставим первый доступный — на нём сервер и сгенерирует (_allowed_provider
  // всё равно понижает недоступный), а чип показывает это честно, а не пустым.
  if (!provSel.value && provSel.options.length) provSel.value = provSel.options[0].value;
  if (provSeg) {
    const list = engineList.length
      ? engineList
      : (providers.video || ["grok"]).map((p) => ({
          id: p, family: p, scene_cost: null,
          title: t(p === "seedance" ? "scene.providerSeedanceShort" : "scene.providerGrokShort"),
        }));
    // Активна та модель, что выбрана сценой; если сцена помнит только семейство —
    // подсвечиваем первую модель этого семейства.
    let curEngine = s.video_engine || "";
    if (!curEngine || !list.some((e) => e.id === curEngine)) {
      const byFam = list.find((e) => e.family === provSel.value);
      curEngine = byFam ? byFam.id : (list[0] && list[0].id) || "";
    }
    const applyEngine = (e) => {
      curEngine = e.id;
      provSel.value = e.family || "grok";
      const opt = $$("option", provSel).find((o) => o.dataset.engine === e.id);
      if (opt) provSel.value = opt.value;
      provSel.dataset.engine = e.id;
      syncSeg();
    };
    const syncSeg = () => $$(".prov-chip", provSeg)
      .forEach((el) => el.classList.toggle("on", el.dataset.engine === curEngine));
    list.forEach((e) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "prov-chip";
      chip.dataset.prov = e.family || e.id;
      chip.dataset.engine = e.id;
      // В чипе — короткое имя модели; цена сцены в очках уходит в подсказку,
      // иначе чипы не помещаются в узкой карточке кадра.
      chip.textContent = e.title;
      chip.title = e.scene_cost != null
        ? `${e.title} — ${e.scene_cost} ${t("scene.pointsSuffix") || "очков за сцену"}`
        : e.title;
      if (list.length > 1) {
        chip.addEventListener("click", () => applyEngine(e));
      } else {
        chip.classList.add("single");
      }
      provSeg.appendChild(chip);
    });
    provSel.dataset.engine = curEngine;
    syncSeg();
  }
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
  await api(`/api/tracks/${id}`, {
    method: "PATCH",
    body: {
      title: $(".t-title", card).value,
      style: $(".t-style", card).value,
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
      [Boolean((tr.style || "").trim()), t("modal.supergen.styleOk"), t("modal.supergen.styleBad")],
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

async function genSceneFrames(id, which = "both") {
  try {
    await api(`/api/scenes/${id}/generate-frames?which=${which}`, { method: "POST" });
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
  fd.append("style", form.style.value);
  fd.append("lyrics", form.lyrics.value);
  fd.append("comment", form.comment.value);
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
  const photo = (c.photos || [])[0];
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
  const photosBox = $(".char-photos", card);
  (c.photos || []).forEach((ph) => {
    const wrap = document.createElement("div");
    wrap.className = "char-photo";
    const img = document.createElement("img");
    img.src = ph.url + `?t=${ph.id}`;
    const del = document.createElement("button");
    del.className = "ghost danger char-photo-del";
    del.textContent = "✕";
    del.title = t("character.photoDel");
    del.addEventListener("click", async () => {
      await api(`/api/characters/photos/${ph.id}`, { method: "DELETE" });
      await back();
    });
    wrap.appendChild(img);
    wrap.appendChild(del);
    photosBox.appendChild(wrap);
  });
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
  const current = form.style.value || "";
  buildStylePicker(form.querySelector(".style-picker"), current, (v) => { form.style.value = v; });
}
rebuildAddTrackPicker();

(async () => {
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

// Генерация модельки персонажа: разворот в 4 ракурсах по описанию + фото-рефам.
function openModelModal(c, onDone = null) {
  openModal(t("modal.model.title", { name: c.name || t("modal.model.someone") }), (body) => {
    const info = document.createElement("p");
    info.className = "muted";
    info.style.margin = "0 0 10px";
    info.textContent = (c.photos || []).length
      ? t("modal.model.withPhotos", { n: Math.min(3, c.photos.length) })
      : t("modal.model.noPhotos");
    body.appendChild(info);

    const lab = document.createElement("label");
    lab.textContent = t("modal.model.descLabel");
    body.appendChild(lab);
    const ta = document.createElement("textarea");
    ta.rows = 5;
    ta.value = c.description || "";
    ta.placeholder = t("modal.model.descPh");
    body.appendChild(ta);

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

    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = t("common.generate");
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = t("modal.model.busy");
      try {
        await api(`/api/characters/${c.id}/generate-model`, {
          method: "POST", body: { description: ta.value, kind: sel.value },
        });
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
const LD_SCENE_COST = { grok: 4, seedance: 10, top: 16 };
const LD_SCENES_PER_CLIP = 30;          // трёхминутный трек ≈ 30 сцен по 6 сек
const LD_REF = { discount: 10, reward: 30 };  // REF_DISCOUNT_PCT / REF_REWARD_PCT

// Запасная витрина: гость не авторизован, а GET /api/billing/plans требует
// сессию — лендинг обязан рисоваться и без ответа сервера. Числа держим
// синхронными с PLANS/TOPUP_PACKS бэкенда; живой ответ их перезаписывает.
const LD_PLANS_FALLBACK = [
  { id: "free", points: 120, usd: 0 },
  { id: "pro", points: 700, usd: 20 },
  { id: "pro_max", points: 2400, usd: 100 },
  { id: "studio", points: 6000, usd: 299 },
];
const LD_PACKS_FALLBACK = [
  { id: "p400", points: 400, usd: 9 },
  { id: "p1000", points: 1000, usd: 19 },
  { id: "p2500", points: 2500, usd: 39 },
  { id: "p6000", points: 6000, usd: 79 },
  { id: "p15000", points: 15000, usd: 169 },
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
      ? { plan: data.current, period: data.current_period || "month" }
      : { plan: data.current.plan || "", period: data.current.period || "month" };
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

function ldPlanCard(plan) {
  const T = LT("pricing");
  const copy = (T.plans || {})[plan.id] || {};
  const paid = plan.usd > 0;
  const year = paid ? ldYearMonthly(plan) : null;
  const yearMode = paid && ldPeriod === "year";
  const price = paid ? ldMoney(yearMode ? year.mo : plan.usd) : T.free;
  const per = paid ? T.perMonth : T.forever;
  const hint = !paid ? "&nbsp;"
    : yearMode ? escHtml(tFill(T.yearNote, { total: ldMoney(year.total) }))
               : escHtml(tFill(T.yearHint, { mo: ldMoney(year.mo) }));
  const badge = copy.badge || plan.badge || "";
  const engine = copy.engine || "grok";
  const clips = plan.movies && !copy.engine ? plan.movies : ldClipsLine(plan.points, engine);
  const isCur = Boolean(me && me.authed && ldPricing && ldPricing.current
                        && ldPricing.current.plan === plan.id);
  const feats = (copy.features || []).map((f) => `<li>${escHtml(f)}</li>`).join("");

  let action;
  if (isCur) {
    action = `<div class="ld-plan-cur">${escHtml(T.current)}</div>`;
  } else if (!paid) {
    action = `<button type="button" class="ld-plan-start">${escHtml(T.ctaFree)}</button>`;
  } else {
    action = `<button type="button" class="primary ld-plan-pay" data-plan="${escHtml(plan.id)}"
      >${escHtml(tFill(T.cta, { plan: copy.title || plan.id.toUpperCase() }))}</button>`;
  }

  // Рамкой выделяем РОВНО один тариф — рекомендованный (hi в словаре).
  // Бейдж может быть и у других, но второй жирной рамки на экране не будет.
  return `<article class="ld-plan${copy.hi ? " ld-plan-hi" : ""}">
    ${badge ? `<span class="ld-plan-badge">${escHtml(badge)}</span>` : ""}
    <span class="ld-plan-name">${escHtml(copy.title || plan.id.toUpperCase())}</span>
    <div class="ld-plan-price">${escHtml(price)}<span> ${escHtml(per)}</span></div>
    <div class="ld-plan-year muted">${hint}</div>
    <span class="ld-plan-clips">${escHtml(clips)}</span>
    <p class="ld-plan-note">${escHtml(tFill(T.pointsLine, { points: tNum(plan.points) }))}${
      copy.note ? " · " + escHtml(copy.note) : ""}</p>
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
    btn.addEventListener("click", () => ldCheckout("plan", btn.dataset.plan, btn));
  });
  $$(".ld-plan-start", box).forEach((btn) => btn.addEventListener("click", ldStart));

  // Годовой тумблер держим в актуальном состоянии вместе с карточками.
  $$("#ld-period button").forEach((b) => b.classList.toggle("on", b.dataset.period === ldPeriod));
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

// ────────── оплата ──────────
// Гость платит тем же кликом, которым заводит аккаунт: без аккаунта платёж
// не к чему привязать, а форму регистрации мы принципиально не показываем.
async function ldEnsureAccount() {
  if (me && me.authed) return;
  await api("/api/start" + (refCode ? `?ref=${encodeURIComponent(refCode)}` : ""), { method: "POST" });
  me = await api("/api/me");
  ldRenderAuth();
}

async function ldCheckout(kind, id, btn) {
  const note = $("#ld-pay-note");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = kind === "plan" ? LTX("pricing.creating") : LTX("topup.creating");
  if (note) { note.textContent = ""; note.classList.remove("error"); }
  try {
    await ldEnsureAccount();
    const body = kind === "plan"
      ? { kind: "plan", plan: id, period: ldPeriod, promo: refCode || "" }
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
  if (!$("#app").classList.contains("hidden")) {
    renderUserBar();                  // бейдж очков и тариф тоже подписаны словами
    if (project) render();
  }
  rebuildAddTrackPicker();
  if (!$("#welcome").classList.contains("hidden")) ldRenderText();
  syncLangSwitches();
});
