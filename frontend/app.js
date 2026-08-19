// Микс стилей: 1–3 пресета чекбокс-чипами, ПЕРВЫЙ выбранный — основа.
// 1 пресет — чистый value; 2–3 — value основы + короткие выжимки остальных.
function styleExcerpt(value) {
  // Первые ~2 предложения промпта: ими дополнительный стиль «подмешивается».
  const m = value.match(/[^.]+\./g);
  return m ? m.slice(0, 2).join("").trim() : value;
}

function buildFusionStyle(labels) {
  const chosen = labels
    .map((l) => STYLE_PRESETS.find((p) => p.label === l))
    .filter(Boolean);
  if (!chosen.length) return "";
  if (chosen.length === 1) return chosen[0].value;
  const extras = chosen.slice(1).map((p) => styleExcerpt(p.value));
  return chosen[0].value + "\n\nBlend in elements of: " + extras.join(" ");
}

function styleLabelsFromValue(value) {
  // Восстановление выбора из сохранённого промпта: основа хранится полным
  // value (идёт первой), дополнительные — выжимками (идут следом).
  if (!value) return [];
  const base = STYLE_PRESETS.filter((p) => value.includes(p.value)).map((p) => p.label);
  const extras = STYLE_PRESETS
    .filter((p) => !value.includes(p.value) && value.includes(styleExcerpt(p.value)))
    .map((p) => p.label);
  return [...base, ...extras];
}

function buildStylePicker(container, current, onChange) {
  container.innerHTML = "";
  // Порядок выбора важен: order[0] — основа микса.
  const order = styleLabelsFromValue(current);

  const chipsBox = document.createElement("div");
  chipsBox.className = "style-chips";
  const desc = document.createElement("details");
  desc.className = "style-desc";
  const descSummary = document.createElement("summary");
  descSummary.textContent = "описание выбранных стилей";
  const descBody = document.createElement("div");
  descBody.className = "style-desc-body muted";
  desc.append(descSummary, descBody);

  const sync = (fireChange) => {
    $$(".style-chip", chipsBox).forEach((el) => {
      const on = order.includes(el.dataset.label);
      el.classList.toggle("on", on);
      el.classList.toggle("base", order[0] === el.dataset.label);
      el.querySelector("input").checked = on;
    });
    const chosen = order
      .map((l) => STYLE_PRESETS.find((p) => p.label === l))
      .filter(Boolean);
    descBody.textContent = chosen.length
      ? chosen.map((p, i) => `${i ? "＋" : "★"} ${p.label} — ${p.desc}`).join("\n")
      : "стиль не выбран";
    desc.classList.toggle("hidden", !chosen.length);
    if (fireChange) onChange(buildFusionStyle(order));
  };

  for (const p of STYLE_PRESETS) {
    const label = document.createElement("label");
    label.className = "style-chip";
    label.dataset.label = p.label;
    label.title = p.desc;
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.addEventListener("change", () => {
      if (cb.checked) {
        if (order.length >= 3) { cb.checked = false; return; } // максимум 3
        order.push(p.label);
      } else {
        const i = order.indexOf(p.label);
        if (i >= 0) order.splice(i, 1);
      }
      sync(true);
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(p.label));
    chipsBox.appendChild(label);
  }
  container.appendChild(chipsBox);
  container.appendChild(desc);
  if (current && !order.length) {
    const note = document.createElement("div");
    note.className = "muted";
    note.textContent = "(у трека свой кастомный стиль — выбор пресета заменит его)";
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
    label: "Хаяо Миядзаки (ламповое аниме)",
    desc: "Тёплое рисованное аниме с акварельными фонами и уютным светом — как кадр из Гибли.",
    value: "Hand-painted Studio Ghibli style anime inspired by Hayao Miyazaki films, vertical 9:16. Soft watercolor backgrounds with visible brush texture, lush painterly clouds and greenery, warm golden-hour sunlight or cozy lamp glow through windows. Gentle pleasant palette: warm cream, soft sky blue, grass green, sunset amber — nothing acidic, everything nostalgic and comforting. Characters drawn in classic 2D anime cel style with simple expressive faces, natural relaxed poses, wind gently moving hair and clothes. Quiet magical realism mood: dust motes in sunbeams, steam from food, fireflies, rustling leaves. Every frame feels like a warm memory — calm, humane, a little wistful. No harsh shadows, no neon, no 3D render look, no text."
  },
  {
    label: "3D мультяшный (Pixar-style)",
    desc: "Глянцевый 3D-мультфильм: выразительные герои, сочный кинематографичный свет.",
    value: "High-end 3D animated feature film style like Pixar and DreamWorks, vertical 9:16, ultra HD render. Rounded appealing character design with large expressive eyes, soft subsurface scattering skin, detailed hair and fabric simulation. Rich cinematic lighting: warm key light, colorful bounce light, gentle rim light separating character from background. Vibrant but tasteful saturated palette, shallow depth of field with creamy bokeh, subtle film grain. Polished storytelling composition, emotional facial expressions. No text, no watermark."
  },
  {
    label: "Кинематографичное аниме (Синкай)",
    desc: "Современное аниме с гиперкрасивыми небесами, бликами и эмоциональными градиентами.",
    value: "Modern cinematic anime film style inspired by Makoto Shinkai, vertical 9:16. Breathtaking hyper-detailed backgrounds: glowing skies with layered clouds, lens flares, glittering city lights, rain droplets catching light. Emotional color grading with luminous gradients — deep blues into warm oranges and pinks. Crisp 2D character animation with delicate lighting on hair and eyes. Dramatic sense of scale: vast skies over small human figures. Melancholic-hopeful atmosphere. No text, no watermark."
  },
  {
    label: "Реализм (кино)",
    desc: "Фотореалистичное кино на плёнке: честный свет, фактура кожи, лёгкое зерно.",
    value: "Photorealistic cinematic film still, vertical 9:16, shot on ARRI Alexa with anamorphic lenses. Natural skin texture and imperfections, real physical lighting: practical sources, soft window light or hard sun with true shadows. Film color grading with gentle teal-orange balance, subtle 35mm grain, shallow depth of field. Documentary-authentic staging: real locations, lived-in details, honest emotion on faces. No CGI look, no oversharpening, no text."
  },
  {
    label: "2D плоская анимация",
    desc: "Яркая плоская векторная анимация: простые формы, смелые контуры, постерные композиции.",
    value: "Bold flat 2D vector animation style, vertical 9:16. Clean geometric shapes, thick confident outlines, limited harmonious palette of 4-6 colors per scene, flat color fills with simple two-tone shading. Playful exaggerated proportions and snappy poses, minimal but expressive faces. Mid-century modern and contemporary motion-design influence: textured paper grain overlay, simple patterned backgrounds. Cheerful, graphic, poster-like compositions. No gradients overload, no 3D, no text."
  },
  {
    label: "Нуарный комикс",
    desc: "Чёрно-белый нуар с одним цветовым акцентом: глубокие тени, дождь, неон.",
    value: "Gritty noir graphic novel style like Sin City and Batman animated classics, vertical 9:16. High-contrast chiaroscuro: deep ink-black shadows swallowing half of every frame, stark white or single warm accent color (red neon, amber streetlight) cutting through darkness. Heavy dramatic hatching and ink texture, rain-slick streets reflecting light, cigarette smoke curling through venetian-blind shadows. Hard-boiled atmosphere: trench coats, brooding silhouettes, low camera angles. Monochrome with one accent color per scene. No text, no captions."
  },
  {
    label: "Длинные бошки (аналоговый сюр 90-х)",
    desc: "Аналоговая плёнка 90-х: сюрреалистичные длинноголовые персонажи в обычной уличной жизни.",
    value: "1990s analog film street photography, scanned 35mm frame with heavy grain and slightly faded Kodak colors, candid documentary framing. Surreal characters with elongated non-human heads on long necks (ostrich-like, greyhound, pale alien with almond eyes, porcelain mannequin mask) on completely ordinary human bodies in baggy 90s streetwear: oversized denim jackets, loose white shirts, wide pants, chunky chains, plastic grocery bags, coffee cups. Deadpan poses, mundane everyday activities, nobody reacts to the surrealism. Locations: laundromats, convenience stores, crosswalks, chain-link fences, boxy 80s sedans, night streets with neon signage and wet asphalt reflections. Muted denim-blue palette with warm cream skin tones and red/neon accents, harsh daylight or direct flash by day, deep black sky and neon glow by night. Vertical 9:16, no text."
  },
  {
    label: "Картон (вышивка нитью)",
    value: "Hand-embroidered thread-art illustration: the entire image is stitched in dense chain-stitch and satin-stitch embroidery with clearly visible thread loops and fiber texture, like a lovingly hand-sewn patch. Background of warm cream felt and kraft cardboard with soft fabric grain. Bold simplified shapes with clean dark outlines; characters rendered in colored thread, directional stitching following the forms of faces, hair and clothes; flames, smoke and effects also stitched in swirling orange-red-amber threads. Cozy handcrafted feel, slightly naive proportions, saturated yarn colors against the neutral textile background, tiny loose thread ends visible. Vertical 9:16, no text."
  },
  {
    label: "СПАЙК (русский кино-сюр, камео)",
    desc: "Ночной русский кино-сюр: хрущёвки, Лады, дым и мультяшные камео на серьёзных щах.",
    value: "Cinematic photorealistic night scene shot on vintage anamorphic lenses, warm tungsten and smoky haze, heavy 35mm film grain with teal-and-amber grade. Post-Soviet Russian setting reimagined with subtle Atomic Heart retrofuturism: khrushchyovka courtyards, cramped old Lada interiors, kiosks, snow-dusted parking lots, delivery couriers in Ozon blue jackets and yellow Yandex thermo-bag backpacks. Photorealistic larger-than-life characters and deadpan cartoon-headed cameos ride together in old cars filled with smoke, count worn banknotes in shabby ornate bedrooms, stare into the lens with calm swagger. Golden chains, tracksuit textures, cigarette smoke curling in headlight beams, wet asphalt reflections. Everyday grit filmed like an epic music video, nobody reacts to the surreal cameos. Vertical 9:16, no text."
  },
  {
    label: "МУНИР (залив, вспышка, фиш-ай)",
    desc: "Уличная съёмка Залива со вспышкой и фиш-аем: кольца в объектив, G63, доберманы.",
    value: "Gulf street documentary photography with direct on-camera flash at night and harsh daylight, ultra-wide fisheye lens distortion, saturated 35mm film colors with crushed shadows. Middle Eastern everyday swagger played deadpan: elderly men in red-checkered ghutra headdress and white thobes grinning as they push a fist with a chunky custom name-ring straight into the lens, women in black abayas fueling a black G63 at a midnight gas station, a Doberman with a heavy chain collar lunging toward the camera, corner grocery shops with Arabic signage and packed shelves, plastic chairs, dates and spice jars. Objects thrust toward the ultra-wide lens so they loom huge in the foreground, faces close and warped at the edges, flash bleaching the foreground against deep black night. Humor and quiet confidence, mundane life shot like a rap video. Vertical 9:16, no text."
  },
  {
    label: "ФАНУЕЛ (кино-сюрреализм, огонь)",
    desc: "Сюрреалистичный fashion-фильм: одинокая фигура в костюме среди невозможных пейзажей и огня.",
    value: "Hyperreal cinematic surreal fashion film, epic single-frame worldbuilding. One elegant figure in a sharply tailored suit of a single bold color (burnt orange, saffron yellow, deep crimson) stands or walks calmly inside an impossible landscape: on the open sea at dusk, along the rings of a giant planet, across endless dunes, under colossal celestial bodies. Recurring fire motif — burning umbrellas, floating flames, embers, fire reflected in water. Deadpan composed poses, quiet confidence, no reaction to the impossible. Painterly dusk palettes: violet-pink-orange gradient skies, deep ocean blues, warm firelight against cool darkness; volumetric cinematic lighting, anamorphic depth, ultra-detailed photorealistic rendering with epic scale contrast between the small figure and the vast world. Vertical 9:16, no text."
  },
  {
    label: "Клеймация (пластилин)",
    desc: "Пластилиновая стоп-моушен анимация: отпечатки пальцев, миниатюрные декорации, тёплый свет.",
    value: "Handcrafted claymation stop-motion style (Aardman/Laika vibe): visible fingerprints in plasticine, slightly imperfect frame-to-frame jitter, miniature set with real fabric and cardboard props, warm practical lighting, shallow depth of field macro look, expressive oversized eyes, vertical 9:16, no text."
  },
  {
    label: "ПАНКРФ (найденное видео, дичь РФ)",
    desc: "Гиперреалистичное «случайное видео» ночной России: регистраторы и VHS, красный неон, абсурд среди пробок и панелек.",
    value: "Hyperreal Russian street found-footage: night dashcam, GoPro, phone or CCTV camera look with heavy VHS grain, analog noise and motion blur; harsh headlights, red neon gas-station canopies, wet asphalt, dense traffic with glowing tail lights, grey soviet panel blocks, ruined brick factories, dusty supercars in wastelands. One absurd event unfolds in the middle of mundane Russian reality — animals rearing between cars, flying couriers, delivery drones, aliens in queues — filmed like an accidental viral video: documentary believability, realistic physics, nobody poses, aggressive dynamic framing, violent handheld shake, strobing flash by night. Muted cold palette with red neon accents. Vertical 9:16, no text."
  },
  {
    label: "ДРИМКЛАД (hood-кино 90-х)",
    desc: "Плёночное hood-кино 90-х: зерно, белые майки и банданы, деньги, голуби и кресты, иконописные фронтальные композиции.",
    value: "1990s American hood-cinema still, shot on grainy 35mm film: faded low-contrast color grade with warm orange-brown skin tones and dusty teal shadows (or deep-grain black-and-white), heavy film grain, soft halation, subtle gate weave and VHS-era imperfections. Brick-block New York / LA streets of the 90s — bodegas with graffiti, chain-link fences, stone staircases, boxy sedans and vintage Cadillacs — or night-time mansion gates and museum halls lit by warm tungsten windows and headlights. Young men in white tank tops, bandana masks, hoodies and baggy denim; crowds dressed identically like a uniform; recurring icons of money stacks, doves, crosses, candles and classical statues — sacred mixed with street. Frontal, symmetric, almost ceremonial compositions, subjects staring straight into the lens, or candid through-the-windshield documentary angles; overexposed hazy daylight or moody night backlight. Cinematic, nostalgic, quietly menacing, music-video energy. No clean digital look, no HDR, no modern cars or clothing, no neon cyberpunk, no glossy skin, no watermarks. Vertical 9:16, no text."
  },
  {
    label: "КАТСУМИ (найденная плёнка, сюр)",
    desc: "Гиперреалистичная «найденная плёнка»: крысы, монахи и алиены на полном серьёзе живут бытовухой под камкордер со вспышкой из 90-х.",
    value: "Hyperrealistic absurdist found-footage aesthetic: a deadpan surreal protagonist (animal or costumed figure) doing mundane human things with total seriousness, shot like accidental amateur documentary footage from the 1990s–2000s — handheld camcorder or disposable-camera look with harsh direct on-camera flash at night, or flat overcast daylight; heavy analog film grain, VHS noise, slight chromatic aberration, motion blur, fisheye or wide-angle distortion, tilted imperfect framing with the subject too close to the lens, often staring straight into the camera. Muted dirty palette of swampy olive, tobacco brown, dusty grey and desaturated flesh tones, background falling into deep black shadow, with one rare accent color (neon sign, police lights, orange robe, gold chain). Gritty tactile textures: wet fur, greasy pavement, cigarette smoke, scuffed metal, cheap floral motel interiors, cluttered convenience-store shelves. Cinematic realism, not cartoon — everything must look physically shot, grimy street-punk mood, crime-scene-snapshot lighting, deadpan comedy with zero wink. Avoid: clean digital sharpness, glossy studio light, saturated candy colors, cartoon or 3D-render look, symmetry, beauty-filter smoothness. Vertical 9:16, no text."
  },
];

function fillStyleSelect(sel, current) {
  sel.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "— выбери стиль —";
  sel.appendChild(empty);
  let matched = false;
  for (const p of STYLE_PRESETS) {
    const o = document.createElement("option");
    o.value = p.value;
    o.textContent = p.label;
    if (current && current === p.value) { o.selected = true; matched = true; }
    sel.appendChild(o);
  }
  // Старый/кастомный стиль трека не теряем: показываем его отдельным пунктом.
  if (current && !matched) {
    const o = document.createElement("option");
    o.value = current;
    o.textContent = "(текущий стиль трека)";
    o.selected = true;
    sel.appendChild(o);
  }
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body && !(opts.body instanceof FormData) ? { "content-type": "application/json" } : undefined,
    body: opts.body instanceof FormData ? opts.body : opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// me — текущий пользователь: бейдж очков и кнопка «Сохранить аккаунт» живут от него.
let me = { authed: false, user: null };

function showWelcome() {
  $("#welcome").classList.remove("hidden");
  $("#login").classList.add("hidden");
  $("#app").classList.add("hidden");
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

// Бейдж «⚡ N» (не-админу) и «Сохранить аккаунт» (гостю без логина) в топбаре.
function renderUserBar() {
  const u = me && me.user;
  const badge = $("#points-badge");
  const saveBtn = $("#save-account-btn");
  if (!u) {
    badge.classList.add("hidden");
    saveBtn.classList.add("hidden");
    return;
  }
  badge.classList.toggle("hidden", Boolean(u.is_admin));
  badge.textContent = `⚡ ${u.gen_points}`;
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
    $("#login-error").textContent = "неверный логин или пароль";
    $("#login-error").classList.remove("hidden");
  }
});

$("#welcome-start").addEventListener("click", async () => {
  // «Старт» = гостевой аккаунт сразу: без формы, регистрация — потом, по желанию.
  await api("/api/start", { method: "POST" });
  me = await api("/api/me");
  showApp();
});

$("#welcome-login").addEventListener("click", showLogin);

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  me = { authed: false, user: null };
  showWelcome();
});

// Гость превращается в постоянный аккаунт: тот же user id, проекты остаются.
$("#save-account-btn").addEventListener("click", () => {
  openModal("Сохранить аккаунт", (body) => {
    body.innerHTML = `
      <p class="muted" style="margin:0 0 10px">Логин и пароль закрепят этот аккаунт:
        проекты и файлы останутся при тебе на любом устройстве.</p>
      <label>Логин</label>
      <input class="ra-login" autocomplete="username" placeholder="логин" />
      <label>Пароль (от 6 символов)</label>
      <input class="ra-pass" type="password" autocomplete="new-password" placeholder="пароль" />
      <label>Имя (необязательно)</label>
      <input class="ra-name" placeholder="как к тебе обращаться" />
      <div class="row">
        <button type="button" class="primary ra-save">Сохранить</button>
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
        errEl.textContent = e.message;
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
function openModal(title, buildBody) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  body.innerHTML = "";
  buildBody(body);
  $("#modal-overlay").classList.remove("hidden");
}

function closeModal() {
  $("#modal-overlay").classList.add("hidden");
  $("#modal-body").innerHTML = "";
}

$("#modal-close").addEventListener("click", closeModal);
$("#modal-overlay").addEventListener("click", (e) => {
  if (e.target === $("#modal-overlay")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal-overlay").classList.contains("hidden")) closeModal();
});

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
  render();
  schedulePoll();
}

function renderProjectBar() {
  const sel = $("#project-select");
  sel.innerHTML = "";
  for (const p of projects) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.kind === "single" ? "🎵" : "💿"} ${p.name}`;
    if (p.id === activeProjectId) o.selected = true;
    sel.appendChild(o);
  }
  $("#project-kind").textContent = project.kind === "single" ? "сингл" : "альбом";
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
  openModal("Новый проект", (body) => {
    body.innerHTML = `
      <label>Название</label>
      <input class="np-name" placeholder="Название проекта" />
      <label>Тип проекта</label>
      <div class="kind-cards">
        <button type="button" class="kind-card on" data-kind="album">
          <span class="kind-emoji">💿</span><b>Альбом</b><span class="muted">несколько треков</span>
        </button>
        <button type="button" class="kind-card" data-kind="single">
          <span class="kind-emoji">🎵</span><b>Сингл</b><span class="muted">один трек</span>
        </button>
      </div>
      <label>Обложка (необязательно)</label>
      <label class="cover-drop">
        <input type="file" class="np-cover hidden" accept="image/jpeg,image/png,image/webp" />
        <img class="np-cover-preview hidden" alt="" />
        <span class="np-cover-hint">＋ выбрать файл (jpg / png / webp)</span>
      </label>
      <div class="row">
        <button type="button" class="primary np-create">Создать</button>
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
        errEl.textContent = "введи название";
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
        errEl.textContent = e.message;
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

function fmtTime(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function statusLabel(status, doneWord = "готово") {
  if (status === "queued") return { text: "в очереди…", cls: "" };
  if (status === "running") return { text: "генерирую…", cls: "" };
  if (status === "error") return { text: "ошибка", cls: "error" };
  if (status === "done") return { text: doneWord, cls: "done" };
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

  const container = $("#tracks");
  container.innerHTML = "";
  project.tracks.forEach((t) => container.appendChild(renderTrack(t)));
}

// ────────── степпер трека: 5 этапов, никакой автогенерации при переключении ──────────
const STAGES = [
  ["setup", "Настройка"],
  ["plot", "Сюжет"],
  ["board", "Раскадровка"],
  ["anim", "Анимация"],
  ["final", "Готовое"],
];
// Активный этап на трек — переживает пере-рендеры поллинга.
const trackStages = new Map();

function stageStates(t) {
  const scenes = t.scenes || [];
  const busy = (st) => ["queued", "running"].includes(st);
  const anyImgBusy = scenes.some((s) => busy(s.image_status) || midframesBusy(s));
  const anyVidBusy = scenes.some((s) => busy(s.video_status));
  const framesDone = scenes.length > 0 && scenes.every((s) => s.image_url && s.image_last_url);
  const videosDone = scenes.length > 0 && scenes.every((s) => s.video_url);
  return {
    setup: t.style && t.audio_duration_sec ? "done"
      : (t.title || t.style || t.audio_duration_sec || t.lyrics || t.comment) ? "part" : "empty",
    plot: project.story_status === "error" ? "error"
      : busy(project.story_status) ? "busy"
      : (project.story || "").trim() ? "done" : "empty",
    board: (t.scenes_status === "error" || t.storyboard_status === "error" ||
        scenes.some((s) => s.image_status === "error")) ? "error"
      : (busy(t.scenes_status) || busy(t.storyboard_status) || anyImgBusy) ? "busy"
      : framesDone ? "done" : scenes.length ? "part" : "empty",
    anim: scenes.some((s) => s.video_status === "error") ? "error"
      : anyVidBusy ? "busy"
      : videosDone ? "done" : scenes.some((s) => s.video_url) ? "part" : "empty",
    final: t.clip_status === "error" ? "error"
      : busy(t.clip_status) ? "busy"
      : t.clip_url ? "done" : "empty",
  };
}

function defaultStage(t) {
  if (t.clip_url) return "final";
  if (t.scenes_count) return "board";
  return "setup";
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

function renderTrack(t) {
  const tpl = $("#track-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".track-card");
  card.dataset.id = t.id;
  $(".pos", card).textContent = `#${t.position}`;
  // Обложка трека: клик по квадрату = заменить (скрытый file input в label).
  if (t.cover_url) {
    const cImg = $(".t-cover-img", card);
    cImg.src = t.cover_url;
    cImg.classList.remove("hidden");
  }
  $(".t-cover-input", card).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("cover", file);
    await api(`/api/tracks/${t.id}/cover`, { method: "POST", body: fd });
    await loadProject();
  });
  $(".t-title", card).value = t.title;

  // ── табы-этапы с точками-статусами
  const states = stageStates(t);
  const active = trackStages.get(t.id) || defaultStage(t);
  const tabsBox = $(".stage-tabs", card);
  STAGES.forEach(([key, name], i) => {
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
      trackStages.set(t.id, key);
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
    const cur = trackStages.get(t.id) || defaultStage(t);
    const idx = STAGES.findIndex(([k]) => k === cur);
    const next = STAGES[idx + (dx < 0 ? 1 : -1)];
    if (next) {
      trackStages.set(t.id, next[0]);
      setStage(card, next[0]);
    }
  }, { passive: true });

  // ── этап 1: настройка
  $(".t-style", card).value = t.style;
  buildStylePicker($(".t-style-picker", card), t.style, (v) => { $(".t-style", card).value = v; });
  $(".t-comment", card).value = t.comment;
  $(".t-grain", card).checked = Boolean(t.film_grain);
  $(".t-nostory", card).checked = Boolean(t.no_story);
  $(".t-lyrics", card).value = t.lyrics;
  const audioEl = $(".t-audio", card);
  if (t.audio_filename) audioEl.src = `/api/tracks/${t.id}/audio`;
  else audioEl.style.display = "none";
  const durEl = $(".t-duration", card);
  durEl.textContent = t.audio_duration_sec ? fmtTime(t.audio_duration_sec) : "";
  if (t.audio_profile) durEl.title = "🎧 " + t.audio_profile; // простыня — в подсказку

  // Плеер трека сам подсвечивает кадр, который сейчас звучит — и наоборот,
  // клик по кадру перематывает трек на его начало и проигрывает.
  audioEl.addEventListener("timeupdate", () => highlightActiveScene(card, audioEl.currentTime));

  $(".up", card).addEventListener("click", () => moveTrack(t.id, -1));
  $(".down", card).addEventListener("click", () => moveTrack(t.id, 1));
  $(".del", card).addEventListener("click", () => deleteTrack(t.id));
  $(".save-track", card).addEventListener("click", () => saveTrack(t.id, card));

  // ── этап 2: сюжет (read-only цитата + заметка режиссёра + генерация, если пуст)
  if (t.director_note) {
    $(".t-note-view", card).classList.remove("hidden");
    $(".t-note-text", card).textContent = t.director_note;
  }
  const storyBtn = $(".gen-story-t", card);
  const storyBusy = ["queued", "running"].includes(project.story_status);
  storyBtn.classList.toggle("hidden", Boolean((project.story || "").trim()) && !storyBusy);
  storyBtn.disabled = storyBusy;
  storyBtn.addEventListener("click", async () => {
    await saveProject();
    try {
      await api(`/api/project/generate-story?project_id=${activeProjectId}`, { method: "POST" });
    } catch (e) {
      alert(e.message);
    }
    await loadProject();
  });
  const tStoryStatus = statusLabel(project.story_status);
  const tStoryEl = $(".t-story-status", card);
  tStoryEl.textContent = tStoryStatus.text;
  tStoryEl.className = "status " + tStoryStatus.cls;

  // ── этап 3: раскадровка
  $(".add-scene", card).addEventListener("click", () => addManualScene(t.id));
  const allBtn = $(".gen-all-frames", card);
  const framesBusy = (t.scenes || []).some((s) => ["queued", "running"].includes(s.image_status));
  const framesTodo = (t.scenes || []).filter((s) => !(s.image_url && s.image_last_url) && s.image_prompt && !s.image_prompt.startsWith("(готовый кадр")).length;
  allBtn.disabled = framesBusy || !framesTodo;
  allBtn.textContent = framesBusy ? "генерирую кадры…" : `Кадры всех сцен (${framesTodo})`;
  allBtn.title = "очередь идёт по одной сцене — вкладку можно закрыть, прогресс не теряется";
  $(".all-frames-note", card).textContent = framesBusy ? "очередь идёт по одной сцене" : "";
  allBtn.addEventListener("click", async () => {
    try {
      await api(`/api/tracks/${t.id}/generate-all-frames`, { method: "POST" });
    } catch (e) {
      alert(e.message);
    }
    await loadProject();
  });
  const allVidBtn = $(".gen-all-videos", card);
  if (allVidBtn) {
    const vidBusy = (t.scenes || []).some((s) => ["queued", "running"].includes(s.video_status));
    const vidTodo = (t.scenes || []).filter((s) => s.image_url && !s.video_url).length;
    allVidBtn.disabled = vidBusy || !vidTodo;
    allVidBtn.textContent = vidBusy ? "генерирую видео…" : `🎬 Видео всех сцен (${vidTodo})`;
    allVidBtn.addEventListener("click", async () => {
      if (!confirm(`Поставить в очередь видео для ${vidTodo} сцен? Это спишет кредиты видеогенератора.`)) return;
      try {
        await api(`/api/tracks/${t.id}/generate-all-videos`, { method: "POST" });
      } catch (e) { alert(e.message); }
      await loadProject();
    });
  }
  const genBtn = $(".gen-scenes", card);
  const busy = t.scenes_status === "queued" || t.scenes_status === "running";
  genBtn.disabled = busy || !project.story;
  genBtn.title = project.story ? "" : "сначала сгенерируй общий сюжет (этап «Сюжет»)";
  genBtn.addEventListener("click", () => genScenes(t.id));

  // ⚡ Супергенерация: весь конвейер одним нажатием (кнопка живёт в шапке).
  const superBtn = $(".s-supergen", card);
  const superBusy = ["queued", "running"].includes(t.supergen_status);
  superBtn.disabled = superBusy || !t.audio_duration_sec;
  superBtn.textContent = superBusy ? "⚡ генерирую всё…" : "⚡ Супергенерация";
  superBtn.addEventListener("click", () => openSupergenModal(t));
  const superNote = $(".supergen-note", card);
  superNote.textContent = t.supergen_note || "";
  superNote.className = "status supergen-note " +
    (t.supergen_status === "error" ? "error" : t.supergen_status === "done" ? "done" : "");
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
  const st = statusLabel(t.scenes_status, `готово, кадров: ${t.scenes_count}`);
  const stEl = $(".scenes-status", card);
  stEl.textContent = st.text || (t.scenes_count ? `кадров: ${t.scenes_count}` : "");
  stEl.className = "status " + st.cls;

  // Лист раскадровки: весь клип одной картинкой — до покадровой отрисовки.
  const sbStatus = statusLabel(t.storyboard_status, "готово");
  const sbStatusEl = $(".sb-status", card);
  sbStatusEl.textContent = sbStatus.text;
  sbStatusEl.className = "status " + sbStatus.cls;
  if (t.storyboard_url) {
    const img = $(".sb-preview", card);
    img.src = t.storyboard_url;
    img.classList.remove("hidden");
  }
  const sbBtn = $(".gen-storyboard", card);
  const sbBusy = ["queued", "running"].includes(t.storyboard_status);
  sbBtn.disabled = sbBusy || !t.scenes_count;
  sbBtn.textContent = sbBusy ? "рисую лист…" : t.storyboard_url ? "Перерисовать лист" : "Сгенерировать лист";
  sbBtn.addEventListener("click", () => genStoryboard(t.id));
  const sliceBtn = $(".slice-storyboard", card);
  sliceBtn.disabled = !t.storyboard_url;
  sliceBtn.addEventListener("click", () => openCellsModal(t));

  // Сцены двумя лентами: «Раскадровка» — кадры, «Анимация» — видео.
  const boardBox = $(".scenes-board", card);
  const animBox = $(".scenes-anim", card);
  (t.scenes || []).forEach((s) => {
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
    hint.textContent = "Видео ещё нет — сгенерируй их из «Раскадровки»: кнопка 🎬 на карточке кадра.";
    animBox.appendChild(hint);
  }
  $$(".strip-wrap", card).forEach(bindStrip);

  // ── этап 5: готовое — финальный клип + все видео сцен в одном месте
  const clipStatus = statusLabel(t.clip_status, "клип готов");
  const clipStatusEl = $(".clip-status", card);
  clipStatusEl.textContent = clipStatus.text;
  clipStatusEl.className = "status " + clipStatus.cls;
  $(".clip-title", card).textContent =
    `Готовый клип — утверждено сцен: ${t.approved_count}/${t.scenes_count}`;
  if (t.clip_url) {
    const v = $(".clip-preview", card);
    v.src = t.clip_url;
    v.classList.remove("hidden");
    const dl = $(".clip-download", card);
    dl.href = t.clip_url;
    dl.classList.remove("hidden");
  }
  const asmBtn = $(".assemble", card);
  const asmBusy = ["queued", "running"].includes(t.clip_status);
  asmBtn.disabled = asmBusy || !t.approved_count;
  asmBtn.title = t.approved_count ? "" : "утверди хотя бы одну сцену (этап «Анимация»)";
  asmBtn.textContent = asmBusy ? "собираю клип…" : "Собрать клип";
  asmBtn.addEventListener("click", () => assembleClip(t.id));

  const grid = $(".final-grid", card);
  const withVideo = (t.scenes || []).filter((s) => s.video_url);
  if (!withVideo.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "видео сцен ещё нет — они появятся здесь после этапа «Анимация»";
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
    cap.textContent = `Сцена ${s.position} · ${fmtTime(s.start_sec)}` + (s.approved ? " ✅" : "");
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
  card.classList.add("mode-" + mode);
  card.dataset.id = s.id;
  card.dataset.start = s.start_sec;
  card.dataset.duration = s.duration_sec;
  $(".s-pos", card).textContent = `Кадр ${s.position}`;
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
          alert(e.message);
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
  const imgStatus = statusLabel(s.image_status, "готово");
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
  framesBtn.textContent = imgBusy ? "рисую кадры…" : s.image_url ? "Перегенерировать кадры" : "Сгенерировать кадры";
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
    img.title = `промежуточный кадр ${i + 1}`;
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
      img.title = "референс кадра — клик: открыть оригинал";
      img.addEventListener("click", () => window.open(r.url, "_blank"));
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost danger s-ref-del";
      del.textContent = "✕";
      del.title = "убрать референс";
      del.addEventListener("click", async () => {
        try {
          await api(`/api/scenes/refs/${r.id}`, { method: "DELETE" });
        } catch (e) {
          alert(e.message);
        }
        await loadProject();
      });
      wrap.append(img, del);
      refsBox.appendChild(wrap);
    });
    const upload = document.createElement("label");
    upload.className = "s-ref-upload";
    upload.title = "картинка-образец: композиция, свет, вайб кадра";
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
          alert(e.message);
          break;
        }
      }
      await loadProject();
    });
    const refBtn = document.createElement("span");
    refBtn.className = "s-ref-btn";
    refBtn.textContent = "+ реф";
    upload.append(refInput, refBtn);
    refsBox.appendChild(upload);
  }

  const midBtn = $(".s-gen-mid", card);
  const midN = s.midframes_expected != null
    ? s.midframes_expected
    : Math.max(0, Math.min(4, Math.round(s.duration_sec / 2) - 1));
  const midBusy = midframesBusy(s);
  midBtn.textContent = midBusy
    ? `промежуточные ${(s.midframes || []).length}/${(midframesExpect.get(s.id) || { n: midN }).n}…`
    : `+ промежуточные кадры (${midN})`;
  midBtn.disabled = !midN || !s.image_url || midBusy || imgBusy;
  midBtn.title = !midN ? "сцена короткая — промежуточные не нужны"
    : !s.image_url ? "сначала сгенерируй кадры сцены"
    : "по одному кадру между первым и последним, 1 очко за кадр";
  midBtn.addEventListener("click", async () => {
    try {
      const r = await api(`/api/scenes/${s.id}/generate-midframes`, { method: "POST" });
      midframesExpect.set(s.id, { n: r.count, ts: Date.now() });
    } catch (e) {
      alert(e.message);
    }
    await loadProject();
  });

  // Видео сцены + отрезок трека под неё.
  const vidStatus = statusLabel(s.video_status, "готово");
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
  const provSel = $(".s-provider", card);
  if (provSel) {
  provSel.innerHTML = "";
  (providers.video || ["grok"]).forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p === "seedance" ? "Seedance (2 кадра)" : "Grok (1 кадр)";
    provSel.appendChild(opt);
  });
  provSel.value = s.video_provider;
  }

  const vidBtn = $(".s-gen-video", card);
  if (vidBtn) {
  const vidBusy = ["queued", "running"].includes(s.video_status);
  vidBtn.disabled = vidBusy || !s.image_url;
  vidBtn.title = s.image_url ? "" : "сначала сгенерируй кадры сцены (этап «Раскадровка»)";
  vidBtn.textContent = vidBusy ? "генерирую…"
    : !s.image_url ? "сначала кадр"
    : s.video_url ? "🎬 Перегенерировать" : "🎬 Видео сцены";
  vidBtn.title = !s.image_url
    ? "Сначала сгенерируй кадры этой сцены — видео делается из первого и последнего кадра"
    : "Оживить сцену: первый + последний кадр → видео";
  vidBtn.addEventListener("click", () => genSceneVideo(s.id, provSel.value));
  }

  if (s.audio_url) {
    const row = $(".scene-audio-row", card);
    row.classList.remove("hidden");
    $(".s-audio", row).src = s.audio_url;
  }

  const approveBox = $(".s-approve", card);
  approveBox.checked = s.approved;
  approveBox.disabled = !s.video_url;
  if (!s.video_url) approveBox.title = "сначала сгенерируй видео сцены";
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
    alert(e.message);
  }
  await loadProject();
});

async function moveTrack(id, dir) {
  const ids = project.tracks.map((t) => t.id);
  const idx = ids.indexOf(id);
  const swapWith = idx + dir;
  if (swapWith < 0 || swapWith >= ids.length) return;
  [ids[idx], ids[swapWith]] = [ids[swapWith], ids[idx]];
  await api("/api/tracks/reorder", { method: "POST", body: { order: ids } });
  await loadProject();
}

async function deleteTrack(id) {
  if (!confirm("Удалить трек вместе с раскадровкой?")) return;
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
    alert(e.message);
  }
  await loadProject();
}

async function assembleClip(id) {
  try {
    await api(`/api/tracks/${id}/assemble`, { method: "POST" });
  } catch (e) {
    alert(e.message);
  }
  await loadProject();
}

function openSupergenModal(t) {
  openModal("⚡ Супергенерация", (body) => {
    // Чек-лист готовности: без стиля и персонажей генератор выдумывает своё.
    const chars = (project.characters || []).filter((c) => (c.name || "").trim());
    const checks = [
      [Boolean((t.style || "").trim()), "Стиль клипа выбран", "Стиль клипа НЕ выбран — выбери пресет на карточке трека"],
      [chars.length > 0, `Персонажи: ${chars.map((c) => c.name).join(", ")}`, "В проекте НЕТ персонажей — добавь нового или клонируй из базы"],
      [Boolean((t.comment || "").trim() || (t.lyrics || "").trim()), "Идея есть (текст или комментарий)", "Нет ни текста, ни комментария — впиши идею клипа в комментарий"],
    ];
    const list = document.createElement("div");
    list.style.margin = "0 0 12px";
    let ready = true;
    checks.forEach(([ok, okText, badText]) => {
      const row = document.createElement("p");
      row.style.margin = "4px 0";
      row.textContent = (ok ? "✅ " : "⛔ ") + (ok ? okText : badText);
      if (!ok) { row.style.color = "var(--danger)"; ready = false; }
      list.appendChild(row);
    });
    body.appendChild(list);
    const info = document.createElement("p");
    info.className = "muted";
    info.style.margin = "0 0 12px";
    info.textContent = "Сквозной сюжет — по желанию: впиши свой в блоке «Герой и сюжет» или оставь " +
      "пустым, напишу сам. Дальше всё автоматом: сюжет → раскадровка → кадры → видео каждой " +
      "сцены → сборка клипа с треком. Прогресс будет виден на карточке трека. Если сцены уже " +
      "были сгенерены с другим стилем — сначала нажми «Сгенерировать раскадровку» заново.";
    body.appendChild(info);
    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = "Погнали";
    go.disabled = !ready;
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await api(`/api/tracks/${t.id}/supergen`, { method: "POST" });
        closeModal();
        await loadProject();
      } catch (e) {
        go.disabled = false;
        alert(e.message);
      }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = "отмена";
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
    alert(e.message); // в т.ч. «лимит генераций исчерпан»
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
  if (!confirm("Удалить кадр?")) return;
  await api(`/api/scenes/${id}`, { method: "DELETE" });
  await loadProject();
}

async function genSceneFrames(id, which = "both") {
  try {
    await api(`/api/scenes/${id}/generate-frames?which=${which}`, { method: "POST" });
  } catch (e) {
    alert(e.message);
  }
  await loadProject();
}

async function genSceneVideo(id, provider) {
  try {
    await api(`/api/scenes/${id}/generate-video`, { method: "POST", body: { provider } });
  } catch (e) {
    alert(e.message);
  }
  await loadProject();
}

async function approveScene(id, approved) {
  try {
    await api(`/api/scenes/${id}/approve`, { method: "POST", body: { approved } });
  } catch (e) {
    alert(e.message);
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

function renderCharacter(c) {
  const tpl = $("#char-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".char-card");
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
    del.title = "удалить фото";
    del.addEventListener("click", async () => {
      await api(`/api/characters/photos/${ph.id}`, { method: "DELETE" });
      await loadProject();
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
    await loadProject();
  });
  $(".c-del", card).addEventListener("click", async () => {
    if (!confirm(`Удалить персонажа «${c.name}» вместе с фото?`)) return;
    await api(`/api/characters/${c.id}`, { method: "DELETE" });
    await loadProject();
  });
  const genModelBtn = $(".c-gen-model", card);
  if (genModelBtn) genModelBtn.addEventListener("click", () => openModelModal(c));
  const input = $(".c-photo-input", card);
  input.addEventListener("change", async () => {
    for (const file of input.files) {
      const fd = new FormData();
      fd.append("photo", file);
      await api(`/api/characters/${c.id}/photos`, { method: "POST", body: fd });
    }
    await loadProject();
  });

  // Атрибуты — фирменные вещи персонажа: чипы с миниатюрами ракурсов.
  const attrsBox = $(".char-attrs", card);
  (c.attributes || []).forEach((a) => attrsBox.appendChild(renderAttribute(a)));
  $(".attr-add", card).addEventListener("click", () => openAttributeModal(c.id));

  return card;
}

// Чип атрибута: имя (клик = редактирование), миниатюры фото с ✕, «+ фото», ✕ атрибута.
function renderAttribute(a) {
  const chip = document.createElement("div");
  chip.className = "attr-chip";
  chip.dataset.id = a.id;

  const name = document.createElement("button");
  name.type = "button";
  name.className = "attr-name";
  name.textContent = a.name;
  name.title = (a.description ? a.description + " — " : "") + "клик: редактировать";
  name.addEventListener("click", () => openAttributeModal(null, a));
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
    del.title = "удалить фото";
    del.addEventListener("click", async () => {
      await api(`/api/attributes/photos/${ph.id}`, { method: "DELETE" });
      await loadProject();
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
    await loadProject();
  });
  const uploadBtn = document.createElement("span");
  uploadBtn.className = "attr-upload-btn";
  uploadBtn.textContent = "+ фото";
  upload.append(input, uploadBtn);
  chip.appendChild(upload);

  const delAttr = document.createElement("button");
  delAttr.type = "button";
  delAttr.className = "ghost danger attr-del";
  delAttr.textContent = "✕";
  delAttr.title = "удалить атрибут";
  delAttr.addEventListener("click", () => confirmDeleteAttribute(a));
  chip.appendChild(delAttr);

  return chip;
}

// Одна модалка на создание (charId) и редактирование (attr) атрибута.
function openAttributeModal(charId, attr = null) {
  openModal(attr ? "Атрибут персонажа" : "Новый атрибут", (body) => {
    body.innerHTML = `
      <label>Название (напр. красная кепка)</label>
      <input class="at-name" placeholder="как зовётся вещь" />
      <label>Описание (необязательно — пойдёт в промпты)</label>
      <textarea class="at-desc" rows="2"></textarea>
      <div class="row">
        <button type="button" class="primary at-save">${attr ? "Сохранить" : "Создать"}</button>
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
        errEl.textContent = "введи название";
        errEl.classList.remove("hidden");
        return;
      }
      saveBtn.disabled = true;
      try {
        const payload = { name, description: descInput.value.trim() };
        if (attr) await api(`/api/attributes/${attr.id}`, { method: "PATCH", body: payload });
        else await api(`/api/characters/${charId}/attributes`, { method: "POST", body: payload });
        closeModal();
        await loadProject();
      } catch (e) {
        errEl.textContent = e.message;
        errEl.classList.remove("hidden");
        saveBtn.disabled = false;
      }
    };
    saveBtn.addEventListener("click", save);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    nameInput.focus();
  });
}

function confirmDeleteAttribute(a) {
  openModal("Удалить атрибут?", (body) => {
    body.innerHTML = `
      <p class="muted attr-del-text" style="margin:10px 0 0"></p>
      <div class="row">
        <button type="button" class="primary ad-yes">Удалить</button>
        <button type="button" class="ad-no">Отмена</button>
        <span class="ad-error error hidden"></span>
      </div>`;
    $(".attr-del-text", body).textContent = `Атрибут «${a.name}» и все его фото будут удалены.`;
    $(".ad-no", body).addEventListener("click", closeModal);
    const yesBtn = $(".ad-yes", body);
    yesBtn.addEventListener("click", async () => {
      yesBtn.disabled = true;
      try {
        await api(`/api/attributes/${a.id}`, { method: "DELETE" });
        closeModal();
        await loadProject();
      } catch (e) {
        const errEl = $(".ad-error", body);
        errEl.textContent = e.message;
        errEl.classList.remove("hidden");
        yesBtn.disabled = false;
      }
    });
  });
}

$("#add-character-btn").addEventListener("click", () => {
  openModal("Добавить персонажа", (body) => {
    body.innerHTML = `
      <div class="modal-tabs">
        <button type="button" class="modal-tab on" data-tab="new">Новый</button>
        <button type="button" class="modal-tab" data-tab="library">Из базы</button>
      </div>
      <div class="tab-pane" data-pane="new">
        <label>Имя персонажа</label>
        <input class="ch-name" placeholder="напр. Артём" />
        <div class="row">
          <button type="button" class="primary ch-create">Создать</button>
          <span class="ch-error error hidden"></span>
        </div>
      </div>
      <div class="tab-pane hidden" data-pane="library">
        <p class="muted" style="margin:10px 0 0">Персонаж из любого проекта: имя, описание и
          фото-модельки скопируются в текущий.</p>
        <div class="lib-grid"><span class="muted">загружаю…</span></div>
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
        errEl.textContent = "введи имя";
        errEl.classList.remove("hidden");
        return;
      }
      createBtn.disabled = true;
      try {
        await api(`/api/characters?project_id=${activeProjectId}`, { method: "POST", body: { name } });
        closeModal();
        await loadProject();
      } catch (e) {
        errEl.textContent = e.message;
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
          grid.innerHTML = '<span class="muted">в базе пока никого нет</span>';
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
          from.textContent = here ? "уже здесь" : c.project_name;
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
                libErr.textContent = e.message;
                libErr.classList.remove("hidden");
                el.disabled = false;
              }
            });
          }
          grid.appendChild(el);
        }
      } catch (e) {
        grid.innerHTML = "";
        libErr.textContent = e.message;
        libErr.classList.remove("hidden");
      }
    }
  });
});

async function addManualScene(trackId) {
  await api(`/api/tracks/${trackId}/scenes`, { method: "POST", body: {} });
  await loadProject();
}

{
  const form = document.querySelector("#add-track-form");
  buildStylePicker(form.querySelector(".style-picker"), "", (v) => { form.style.value = v; });
}

(async () => {
  me = await api("/api/me");
  // Без сессии гость видит лендинг, а не форму пароля.
  if (me.authed) showApp(); else showWelcome();
})();

// Разбор листа раскадровки: ячейки сеткой, владелец сам решает, какие взять
// и в какие сцены их положить. Не выбранные сцены остаются как есть.
async function openCellsModal(t) {
  let data;
  try {
    data = await api(`/api/tracks/${t.id}/storyboard-cells`, { method: "POST" });
  } catch (e) { alert(e.message); return; }
  const scenes = t.scenes || [];
  openModal("Разложить лист по кадрам", (body) => {
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.style.margin = "0 0 10px";
    hint.textContent = "Отметь ячейки, которые берём, и выбери сцену для каждой. " +
      "Остальные сцены не тронутся — их можно перегенерировать отдельно.";
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
        o.textContent = `в кадр ${sc.position}`;
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
    go.textContent = "Применить выбранные";
    go.addEventListener("click", async () => {
      const pairs = rows.filter((r) => r.cb.checked)
        .map((r) => ({ filename: r.filename, scene_id: Number(r.sel.value) }));
      if (!pairs.length) { alert("не выбрано ни одной ячейки"); return; }
      go.disabled = true;
      try {
        const res = await api(`/api/tracks/${t.id}/apply-cells`, { method: "POST", body: { pairs } });
        closeModal();
        alert(`Разложено кадров: ${res.applied}`);
        await loadProject();
      } catch (e) { go.disabled = false; alert(e.message); }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = "отмена";
    cancel.addEventListener("click", closeModal);
    row.appendChild(go);
    row.appendChild(cancel);
    body.appendChild(row);
  });
}

// Генерация модельки персонажа: разворот в 4 ракурсах по описанию + фото-рефам.
function openModelModal(c) {
  openModal(`Моделька: ${c.name || "персонаж"}`, (body) => {
    const info = document.createElement("p");
    info.className = "muted";
    info.style.margin = "0 0 10px";
    info.textContent = (c.photos || []).length
      ? `Референсом уйдут первые ${Math.min(3, c.photos.length)} фото персонажа — лицо и одежда сохранятся.`
      : "Фото не загружены — моделька будет собрана только по описанию.";
    body.appendChild(info);

    const lab = document.createElement("label");
    lab.textContent = "Описание для модельки";
    body.appendChild(lab);
    const ta = document.createElement("textarea");
    ta.rows = 5;
    ta.value = c.description || "";
    ta.placeholder = "внешность, одежда, атрибуты — чем подробнее, тем точнее";
    body.appendChild(ta);

    const lab2 = document.createElement("label");
    lab2.textContent = "Вид модельки";
    body.appendChild(lab2);
    const sel = document.createElement("select");
    [["3d", "3D-модель (CG-рендер)"], ["real", "Фотореализм (студия)"],
     ["anime", "Аниме (лист сеттеи)"]].forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t; sel.appendChild(o);
    });
    body.appendChild(sel);

    const row = document.createElement("div");
    row.className = "row";
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = "Сгенерировать";
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "генерирую… (до 2 минут)";
      try {
        await api(`/api/characters/${c.id}/generate-model`, {
          method: "POST", body: { description: ta.value, kind: sel.value },
        });
        closeModal();
        await loadProject();
      } catch (e) {
        go.disabled = false;
        go.textContent = "Сгенерировать";
        alert(e.message);
      }
    });
    const cancel = document.createElement("button");
    cancel.className = "ghost";
    cancel.textContent = "отмена";
    cancel.addEventListener("click", closeModal);
    row.appendChild(go); row.appendChild(cancel);
    body.appendChild(row);
  });
}
