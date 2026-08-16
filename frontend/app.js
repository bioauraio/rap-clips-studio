// Стили клипов: выбор ТОЛЬКО из пресетов — каждый промпт полностью
// срежиссирован (эстетика, свет, палитра, фактура), чтобы кадры внутри
// трека не разъезжались. value уходит в промпты as-is.
const STYLE_PRESETS = [
  {
    label: "Хаяо Миядзаки (ламповое аниме)",
    value: "Hand-painted Studio Ghibli style anime inspired by Hayao Miyazaki films, vertical 9:16. Soft watercolor backgrounds with visible brush texture, lush painterly clouds and greenery, warm golden-hour sunlight or cozy lamp glow through windows. Gentle pleasant palette: warm cream, soft sky blue, grass green, sunset amber — nothing acidic, everything nostalgic and comforting. Characters drawn in classic 2D anime cel style with simple expressive faces, natural relaxed poses, wind gently moving hair and clothes. Quiet magical realism mood: dust motes in sunbeams, steam from food, fireflies, rustling leaves. Every frame feels like a warm memory — calm, humane, a little wistful. No harsh shadows, no neon, no 3D render look, no text."
  },
  {
    label: "3D мультяшный (Pixar-style)",
    value: "High-end 3D animated feature film style like Pixar and DreamWorks, vertical 9:16, ultra HD render. Rounded appealing character design with large expressive eyes, soft subsurface scattering skin, detailed hair and fabric simulation. Rich cinematic lighting: warm key light, colorful bounce light, gentle rim light separating character from background. Vibrant but tasteful saturated palette, shallow depth of field with creamy bokeh, subtle film grain. Polished storytelling composition, emotional facial expressions. No text, no watermark."
  },
  {
    label: "Кинематографичное аниме (Синкай)",
    value: "Modern cinematic anime film style inspired by Makoto Shinkai, vertical 9:16. Breathtaking hyper-detailed backgrounds: glowing skies with layered clouds, lens flares, glittering city lights, rain droplets catching light. Emotional color grading with luminous gradients — deep blues into warm oranges and pinks. Crisp 2D character animation with delicate lighting on hair and eyes. Dramatic sense of scale: vast skies over small human figures. Melancholic-hopeful atmosphere. No text, no watermark."
  },
  {
    label: "Реализм (кино)",
    value: "Photorealistic cinematic film still, vertical 9:16, shot on ARRI Alexa with anamorphic lenses. Natural skin texture and imperfections, real physical lighting: practical sources, soft window light or hard sun with true shadows. Film color grading with gentle teal-orange balance, subtle 35mm grain, shallow depth of field. Documentary-authentic staging: real locations, lived-in details, honest emotion on faces. No CGI look, no oversharpening, no text."
  },
  {
    label: "2D плоская анимация",
    value: "Bold flat 2D vector animation style, vertical 9:16. Clean geometric shapes, thick confident outlines, limited harmonious palette of 4-6 colors per scene, flat color fills with simple two-tone shading. Playful exaggerated proportions and snappy poses, minimal but expressive faces. Mid-century modern and contemporary motion-design influence: textured paper grain overlay, simple patterned backgrounds. Cheerful, graphic, poster-like compositions. No gradients overload, no 3D, no text."
  },
  {
    label: "Нуарный комикс",
    value: "Gritty noir graphic novel style like Sin City and Batman animated classics, vertical 9:16. High-contrast chiaroscuro: deep ink-black shadows swallowing half of every frame, stark white or single warm accent color (red neon, amber streetlight) cutting through darkness. Heavy dramatic hatching and ink texture, rain-slick streets reflecting light, cigarette smoke curling through venetian-blind shadows. Hard-boiled atmosphere: trench coats, brooding silhouettes, low camera angles. Monochrome with one accent color per scene. No text, no captions."
  },
  {
    label: "Длинные бошки (аналоговый сюр 90-х)",
    value: "1990s analog film street photography, scanned 35mm frame with heavy grain and slightly faded Kodak colors, candid documentary framing. Surreal characters with elongated non-human heads on long necks (ostrich-like, greyhound, pale alien with almond eyes, porcelain mannequin mask) on completely ordinary human bodies in baggy 90s streetwear: oversized denim jackets, loose white shirts, wide pants, chunky chains, plastic grocery bags, coffee cups. Deadpan poses, mundane everyday activities, nobody reacts to the surrealism. Locations: laundromats, convenience stores, crosswalks, chain-link fences, boxy 80s sedans, night streets with neon signage and wet asphalt reflections. Muted denim-blue palette with warm cream skin tones and red/neon accents, harsh daylight or direct flash by day, deep black sky and neon glow by night. Vertical 9:16, no text."
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

function showLogin() {
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
}
function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  loadProject();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = $("#login-password").value;
  try {
    await api("/api/login", { method: "POST", body: { password } });
    showApp();
  } catch (err) {
    $("#login-error").textContent = "неверный пароль";
    $("#login-error").classList.remove("hidden");
  }
});

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});

let project = null;
let providers = { video: ["grok"], seedance: false };
let pollTimer = null;

async function loadProject() {
  project = await api("/api/project");
  if (!providers.loaded) {
    providers = { ...(await api("/api/providers")), loaded: true };
  }
  render();
  schedulePoll();
}

function sceneBusy(s) {
  return ["queued", "running"].includes(s.image_status) || ["queued", "running"].includes(s.video_status);
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

function renderTrack(t) {
  const tpl = $("#track-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".track-card");
  card.dataset.id = t.id;
  $(".pos", card).textContent = `#${t.position}`;
  $(".t-title", card).value = t.title;
  fillStyleSelect($(".t-style", card), t.style);
  $(".t-comment", card).value = t.comment;
  $(".t-lyrics", card).value = t.lyrics;
  const audioEl = $(".t-audio", card);
  if (t.audio_filename) audioEl.src = `/api/tracks/${t.id}/audio`;
  else audioEl.style.display = "none";
  $(".t-duration", card).textContent = t.audio_duration_sec ? fmtTime(t.audio_duration_sec) : "";

  // Плеер трека сам подсвечивает кадр, который сейчас звучит — и наоборот,
  // клик по кадру перематывает трек на его начало и проигрывает.
  audioEl.addEventListener("timeupdate", () => highlightActiveScene(card, audioEl.currentTime));

  $(".up", card).addEventListener("click", () => moveTrack(t.id, -1));
  $(".down", card).addEventListener("click", () => moveTrack(t.id, 1));
  $(".del", card).addEventListener("click", () => deleteTrack(t.id));
  $(".save-track", card).addEventListener("click", () => saveTrack(t.id, card));

  $(".add-scene", card).addEventListener("click", () => addManualScene(t.id));
  const genBtn = $(".gen-scenes", card);
  const busy = t.scenes_status === "queued" || t.scenes_status === "running";
  genBtn.disabled = busy || !project.story;
  genBtn.title = project.story ? "" : "сначала сгенерируй общий сюжет";
  genBtn.addEventListener("click", () => genScenes(t.id));
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
  sbBtn.textContent = sbBusy ? "рисую лист…" : t.storyboard_url ? "Перерисовать лист" : "Сгенерировать лист раскадровки";
  sbBtn.addEventListener("click", () => genStoryboard(t.id));

  const scenesBox = $(".scenes", card);
  (t.scenes || []).forEach((s) => scenesBox.appendChild(renderScene(s, audioEl)));

  // Сборка готового клипа из утверждённых сцен.
  const clipStatus = statusLabel(t.clip_status, "клип готов");
  const clipStatusEl = $(".clip-status", card);
  clipStatusEl.textContent = clipStatus.text;
  clipStatusEl.className = "status " + clipStatus.cls;
  $(".clip-title", card).textContent =
    `Готовый клип трека — утверждено сцен: ${t.approved_count}/${t.scenes_count}`;
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
  asmBtn.title = t.approved_count ? "" : "утверди хотя бы одну сцену";
  asmBtn.textContent = asmBusy ? "собираю клип…" : "Собрать клип из утверждённых сцен";
  asmBtn.addEventListener("click", () => assembleClip(t.id));

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

function renderScene(s, audioEl) {
  const tpl = $("#scene-tpl").content.cloneNode(true);
  const card = tpl.querySelector(".scene-card");
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
  $(".s-lyric", card).value = s.lyric_line;
  $(".s-note", card).value = s.shot_note;
  $(".s-image", card).value = s.image_prompt;
  $(".s-motion", card).value = s.motion_prompt;
  $(".s-del", card).addEventListener("click", () => deleteScene(s.id));
  $(".s-save", card).addEventListener("click", () => saveScene(s.id, card));
  // Клик по кадру — трек перематывается на его начало и играет: удобно
  // сверять текст/промпт кадра с тем, что реально звучит в этот момент.
  $(".s-play", card).addEventListener("click", () => {
    if (!audioEl.src) return;
    audioEl.currentTime = s.start_sec;
    audioEl.play();
  });

  $(".s-motion-last", card).value = s.image_prompt_last || "";

  // Кадры сцены: первый и последний (Seedance интерполирует между ними).
  const imgStatus = statusLabel(s.image_status, "готово");
  const imgStatusEl = $(".s-image-status", card);
  imgStatusEl.textContent = imgStatus.text;
  imgStatusEl.className = "status " + imgStatus.cls;
  if (s.image_url) {
    const p = $(".s-image-preview", card);
    p.src = s.image_url; p.classList.remove("hidden");
  }
  if (s.image_last_url) {
    const p = $(".s-image-last-preview", card);
    p.src = s.image_last_url; p.classList.remove("hidden");
  }
  const framesBtn = $(".s-gen-frames", card);
  const imgBusy = ["queued", "running"].includes(s.image_status);
  framesBtn.disabled = imgBusy;
  framesBtn.textContent = imgBusy ? "рисую кадры…" : s.image_url ? "Перегенерировать кадры" : "Сгенерировать кадры (4К)";
  framesBtn.addEventListener("click", () => genSceneFrames(s.id));

  // Видео сцены + отрезок трека под неё.
  const vidStatus = statusLabel(s.video_status, "готово");
  const vidStatusEl = $(".s-video-status", card);
  vidStatusEl.textContent = vidStatus.text;
  vidStatusEl.className = "status " + vidStatus.cls;
  if (s.video_url) {
    const v = $(".s-video-preview", card);
    v.src = s.video_url; v.classList.remove("hidden");
  }
  const provSel = $(".s-provider", card);
  provSel.innerHTML = "";
  (providers.video || ["grok"]).forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p === "seedance" ? "Seedance (2 кадра)" : "Grok (1 кадр)";
    provSel.appendChild(opt);
  });
  provSel.value = s.video_provider;

  const vidBtn = $(".s-gen-video", card);
  const vidBusy = ["queued", "running"].includes(s.video_status);
  vidBtn.disabled = vidBusy || !s.image_url;
  vidBtn.textContent = vidBusy ? "генерирую видео…" : s.video_url ? "Перегенерировать видео" : "Сгенерировать видео сцены";
  vidBtn.addEventListener("click", () => genSceneVideo(s.id, provSel.value));

  if (s.audio_url) {
    const row = $(".scene-audio-row", card);
    row.classList.remove("hidden");
    $(".s-audio", row).src = s.audio_url;
  }

  const approveBox = $(".s-approve", card);
  approveBox.checked = s.approved;
  approveBox.disabled = !s.video_url;
  approveBox.title = s.video_url ? "" : "сначала сгенерируй видео сцены";
  approveBox.addEventListener("change", () => approveScene(s.id, approveBox.checked));

  return card;
}

async function saveProject() {
  await api("/api/project", {
    method: "PATCH",
    body: { name: $("#project-name").value, character_bible: $("#character-bible").value },
  });
}
$("#save-project-btn").addEventListener("click", saveProject);

$("#gen-story-btn").addEventListener("click", async () => {
  await saveProject();
  await api("/api/project/generate-story", { method: "POST" });
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
      lyrics: $(".t-lyrics", card).value,
    },
  });
  await loadProject();
}

async function genStoryboard(id) {
  await api(`/api/tracks/${id}/generate-storyboard`, { method: "POST" });
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

async function genScenes(id) {
  await api(`/api/tracks/${id}/generate-scenes`, { method: "POST" });
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

async function genSceneFrames(id) {
  await api(`/api/scenes/${id}/generate-frames`, { method: "POST" });
  await loadProject();
}

async function genSceneVideo(id, provider) {
  await api(`/api/scenes/${id}/generate-video`, { method: "POST", body: { provider } });
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
  await api("/api/tracks", { method: "POST", body: fd });
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
  const input = $(".c-photo-input", card);
  input.addEventListener("change", async () => {
    for (const file of input.files) {
      const fd = new FormData();
      fd.append("photo", file);
      await api(`/api/characters/${c.id}/photos`, { method: "POST", body: fd });
    }
    await loadProject();
  });
  return card;
}

$("#add-character-btn").addEventListener("click", async () => {
  const name = prompt("Имя персонажа:");
  if (!name) return;
  await api("/api/characters", { method: "POST", body: { name } });
  await loadProject();
});

async function addManualScene(trackId) {
  await api(`/api/tracks/${trackId}/scenes`, { method: "POST", body: {} });
  await loadProject();
}

fillStyleSelect(document.querySelector('#add-track-form select[name=style]'), "");

(async () => {
  const me = await api("/api/me");
  if (me.authed) showApp(); else showLogin();
})();
