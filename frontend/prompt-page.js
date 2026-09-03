/* ═══════════════════════════════════════════════════════════════════════════
   lolq.ai — СТРАНИЦА ПРОМТА: /p/{layer}/{key}

   У каждой записи каталога (тренд, шаблон мокапа, движение, камера, свет)
   — своя страница: заголовок, галерея примеров, «что получится», кнопка
   «Использовать». Данные — GET /api/p/{layer}/{key}; примеры копятся в
   общем механизме превью (layer_previews, {"main","all"}), админ добирает
   их кнопкой «+ пример» через бесплатный шлюз.

   Отдельный файл по той же причине, что school.js: раздел не должен
   требовать хирургии в app.js — ему хватает четырёх глобалей.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.QlolPromptPage) return;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (v) => (typeof window.escHtml === "function" ? window.escHtml(v)
    : String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const ru = () => (typeof LANG === "string" ? LANG : "ru") === "ru";
  const t = (a, b) => (ru() ? a : b);
  const api = (p, o) => window.api(p, o);

  function page() { return $("#prompt-page"); }

  function close(updateUrl) {
    const p = page();
    if (!p) return;
    p.remove();
    const app = $("#app");
    if (app && !$("#trends-page") && !$("#marketing-page")) app.classList.remove("trends-view");
    if (updateUrl !== false && location.pathname.startsWith("/p/")) {
      history.pushState({}, "", "/studio");
    }
  }

  async function open(layer, key) {
    const app = $("#app");
    if (!app) return;
    if (window.QlolSections && window.QlolSections.closePages) window.QlolSections.closePages();
    if (window.QlolSchool && window.QlolSchool.close) window.QlolSchool.close(false);
    app.classList.remove("hidden");
    app.classList.add("trends-view");
    let p = page();
    if (!p) {
      p = document.createElement("main");
      p.id = "prompt-page";
      p.className = "trends-page pp-page";
      app.appendChild(p);
    }
    const path = `/p/${layer}/${key}`;
    if (location.pathname !== path) history.pushState({}, "", path);
    p.innerHTML = `<div class="skel" style="max-width:900px;margin:40px auto;min-height:200px"></div>`;
    let d;
    try { d = await api(`/api/p/${encodeURIComponent(layer)}/${encodeURIComponent(key)}`); }
    catch (e) {
      // Нет такого слоя/ключа — не табличка с ошибкой, а каталог промтов:
      // ссылка могла устареть, человеку нужен список, а не тупик.
      if (!e || !e.status || e.status === 404) { toCatalog(); return; }
      p.innerHTML = `<p class="muted" style="text-align:center;padding:60px">${esc(e.message)}</p>`;
      return;
    }
    render(p, d);
  }

  function toCatalog() {
    close(false);
    history.replaceState({}, "", "/prompts");
    if (window.QlolSections && window.QlolSections.openLibrary) window.QlolSections.openLibrary();
  }

  /* Пример галереи: {url, poster} по контракту; kind — совместимость со
     старым ответом. Видео узнаём по постеру или расширению. */
  function isVideo(x) {
    if (x.kind) return x.kind === "video";
    return Boolean(x.poster) || /\.(mp4|webm|mov)(\?|$)/i.test(String(x.url || ""));
  }

  function render(p, d) {
    const label = d.label || d.title || d.key || "";
    document.title = `${label} — lolq.ai`;
    const examples = (d.examples || []).filter((x) => x && x.url);
    if (d.preview_url && !examples.some((x) => x.url === d.preview_url)) {
      examples.unshift({ url: d.preview_url, kind: "image" });
    }
    const prompt = (ru() && d.prompt_ru) || d.prompt || "";
    const useCap = {
      trends: t("Использовать в трендах", "Use in trends"),
      mockup: t("Снять с моим продуктом", "Shoot with my product"),
      studio: t("Использовать в студии", "Use in the studio"),
    }[d.use] || t("Использовать", "Use");
    p.innerHTML = `
      <section class="pp-wrap">
        <nav class="pp-crumbs">
          <button type="button" class="ghosty pp-back">← ${t("назад", "back")}</button>
        </nav>
        <header class="pp-head">
          <h1>${esc(label)}</h1>
          <button type="button" class="pp-use">${esc(useCap)}</button>
          ${d.desc ? `<p class="pp-desc">${esc(d.desc)}</p>` : ""}
          ${d.hint ? `<p class="pp-hint">${esc(d.hint)}</p>` : ""}
        </header>
        <div class="pp-gallery">
          ${examples.map((x) => isVideo(x)
            ? `<video src="${esc(x.url)}" ${x.poster ? `poster="${esc(x.poster)}"` : ""} muted loop playsinline preload="metadata"
                 onmouseenter="this.play()" onmouseleave="this.pause()"></video>`
            : `<img src="${esc(x.url)}" alt="" loading="lazy"/>`).join("")}
          ${d.is_admin && (d.layer === "trend" || d.layer === "mockup")
            ? `<button type="button" class="pp-more-ex">＋ ${t("ещё пример ⚡0", "one more example ⚡0")}</button>` : ""}
        </div>
        ${prompt ? `<section class="pp-prompt">
          ${d.is_admin && d.prompt_ru && d.prompt ? `<pre class="pp-pre">${esc(d.prompt_ru)}</pre>
            <details><summary class="pp-muted">EN</summary><pre class="pp-pre">${esc(d.prompt)}</pre></details>`
            : `<pre class="pp-pre">${esc(prompt)}</pre>`}
        </section>` : ""}
      </section>`;
    $(".pp-back", p).addEventListener("click", () => close());
    $(".pp-use", p).addEventListener("click", () => {
      close(false);
      if (d.use === "trends" && window.QlolSections) {
        window.QlolSections.go("trends");
      } else if (d.use === "mockup") {
        if (window.QlolSections) window.QlolSections.go("studio");
        setTimeout(() => window.QlolModeMenu && window.QlolModeMenu.pick("mockup"), 350);
      } else if (window.QlolSections && window.QlolSections.openLibrary) {
        window.QlolSections.go("studio");
        setTimeout(() => window.QlolSections.openLibrary(d.key), 350);
      }
    });
    const more = $(".pp-more-ex", p);
    if (more) {
      more.addEventListener("click", async () => {
        more.disabled = true;
        more.textContent = t("рисую пример…", "drawing…");
        try {
          await api(`/api/admin/p/${d.layer}/${d.key}/example-generate`, { method: "POST" });
          open(d.layer, d.key);
        } catch (e) {
          more.disabled = false;
          more.textContent = "⚠ " + e.message;
        }
      });
    }
  }

  function fromPath() {
    const m = location.pathname.match(/^\/p\/([\w-]+)\/([\w.:-]+)$/);
    if (!m) return false;
    // Даём app.js подняться: страница живёт внутри #app.
    setTimeout(() => open(m[1], decodeURIComponent(m[2])), 400);
    return true;
  }
  window.addEventListener("popstate", () => { if (!fromPath() && page()) close(false); });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fromPath);
  } else fromPath();

  window.QlolPromptPage = { open, close };
})();
