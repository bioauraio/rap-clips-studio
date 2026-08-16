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
  $(".t-style", card).value = t.style;
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
  const style = form.style_custom.value.trim() || form.style.value;
  fd.append("style", style);
  fd.append("lyrics", form.lyrics.value);
  fd.append("comment", form.comment.value);
  if (form.audio.files[0]) fd.append("audio", form.audio.files[0]);
  await api("/api/tracks", { method: "POST", body: fd });
  form.reset();
  await loadProject();
});

(async () => {
  const me = await api("/api/me");
  if (me.authed) showApp(); else showLogin();
})();
