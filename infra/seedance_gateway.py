#!/usr/bin/env python3
"""Seedance gateway (host-side) — видео по ПОДПИСКЕ через живой веб-UI.

Тот же приём, что у grok_gateway.py: API по ключу мы не покупаем, а держим
тёплый headless-Chromium с кукой оплаченного аккаунта и драйвим настоящий
интерфейс Dreamina (CapCut, модель Seedance). Ключевое ради чего всё
затевалось — режим ПЕРВЫЙ+ПОСЛЕДНИЙ кадр: сцена интерполируется между двумя
нашими картинками, поэтому монтаж получается связным.

Playwright sync-объекты живут в одном потоке-владельце, HTTP-хендлеры кладут
задания в очередь — иначе Playwright падает на кросс-поточных вызовах.

API:
  GET  /health   -> {"ok": true, "logged_in": bool}
  POST /animate  {"prompt": "...", "first_image_path": "/opt/rapclips/data/uploads/a.png",
                  "last_image_path": "...", "duration_sec": 5}
                 -> {"ok": true, "video_path": "/opt/rapclips/data/uploads/seedance_x.mp4"}
  GET  /diag     -> снимок структуры страницы (для настройки селекторов после
                    первого входа: UI Dreamina меняется, гадать вслепую нельзя)

Конфиг (EnvironmentFile /opt/claude-agents/openai/seedance_gateway.env):
  SEEDANCE_COOKIES_FILE=/opt/claude-agents/openai/seedance_cookies.json
  AGENT_PROXY=socks5://172.18.0.1:1081     # Dreamina недоступна из РФ напрямую
  SEEDANCE_URL=https://dreamina.capcut.com/ai-tool/video/generate
  SEEDANCE_OUT_DIR=/opt/rapclips/data/uploads
  SEEDANCE_BIND_HOST=172.18.0.1
  SEEDANCE_BIND_PORT=8768
"""
import json
import os
import queue
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

BIND_HOST = os.environ.get("SEEDANCE_BIND_HOST", "172.18.0.1")
BIND_PORT = int(os.environ.get("SEEDANCE_BIND_PORT", "8768"))
PROXY = os.environ.get("AGENT_PROXY", "socks5://172.18.0.1:1081")
COOKIES_FILE = os.environ.get(
    "SEEDANCE_COOKIES_FILE", "/opt/claude-agents/openai/seedance_cookies.json")
SEEDANCE_URL = os.environ.get(
    "SEEDANCE_URL", "https://dreamina.capcut.com/ai-tool/video/generate")
OUT_DIR = os.environ.get("SEEDANCE_OUT_DIR", "/opt/rapclips/data/uploads")
JOB_TIMEOUT_S = int(os.environ.get("SEEDANCE_JOB_TIMEOUT_S", "900"))
RENDER_TIMEOUT_S = int(os.environ.get("SEEDANCE_RENDER_TIMEOUT_S", "600"))
UA = os.environ.get(
    "SEEDANCE_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
)
ALLOW_PREFIXES = ("172.16.", "172.17.", "172.18.", "172.19.", "127.")

JOBS: "queue.Queue[Job]" = queue.Queue()


def log(*parts):
    print("[seedance]", *parts, flush=True)


class Job:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.kw = kw
        self.ev = threading.Event()
        self.result = None
        self.error = None


def _playwright_proxy(url: str) -> dict:
    return {"server": url}


def _new_context(p, browser_holder):
    br = browser_holder[0]
    if br is None or not br.is_connected():
        browser_holder[0] = p.chromium.launch(
            headless=True, proxy=_playwright_proxy(PROXY),
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        log("browser launched")
        br = browser_holder[0]
    ctx = br.new_context(viewport={"width": 1440, "height": 950}, user_agent=UA)
    if os.path.exists(COOKIES_FILE):
        state = json.load(open(COOKIES_FILE))
        cookies = state.get("cookies", state) if isinstance(state, dict) else state
        ctx.add_cookies(cookies)
    return ctx


def _looks_logged_in(pg) -> bool:
    """Страница генерации доступна только залогиненному: если нас увели на
    вход/лендинг — сессии нет."""
    url = pg.url.lower()
    if any(x in url for x in ("/login", "/signin", "passport", "sign-up")):
        return False
    for sel in ("text=Sign in", "text=Log in", "text=Войти"):
        try:
            if pg.locator(sel).first.is_visible(timeout=1500):
                return False
        except Exception:  # noqa: BLE001
            pass
    return True


def run_diag(browser_holder, p, job) -> dict:
    """Снимок структуры страницы: по нему пишутся точные селекторы UI."""
    ctx = _new_context(p, browser_holder)
    pg = ctx.new_page()
    try:
        pg.goto(SEEDANCE_URL, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(6000)
        shot = os.path.join(OUT_DIR, f"seedance_diag_{uuid.uuid4().hex[:8]}.png")
        pg.screenshot(path=shot, full_page=False)
        info = pg.evaluate("""() => {
            const vis = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            const texts = [...document.querySelectorAll('button, [role=button], a')]
                .filter(vis).map(e => (e.innerText || e.getAttribute('aria-label') || '').trim())
                .filter(Boolean).slice(0, 80);
            const inputs = [...document.querySelectorAll('input, textarea, [contenteditable=true]')]
                .filter(vis).map(e => ({
                    tag: e.tagName, type: e.type || '', ph: e.placeholder || '',
                    label: e.getAttribute('aria-label') || '',
                })).slice(0, 40);
            const files = document.querySelectorAll('input[type=file]').length;
            return {title: document.title, url: location.href, texts, inputs, fileInputs: files};
        }""")
        info["screenshot"] = shot
        info["logged_in"] = _looks_logged_in(pg)
        return info
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


def run_animate(browser_holder, p, job) -> dict:
    """Генерация видео из первого и последнего кадра.

    Селекторы UI Dreamina вынесены в SELECTORS: интерфейс меняется, и после
    первого входа их правим по данным /diag, не трогая остальную логику."""
    prompt = job.kw["prompt"]
    first_path = job.kw["first_image_path"]
    last_path = job.kw.get("last_image_path") or ""
    duration = int(job.kw.get("duration_sec") or 5)

    ctx = _new_context(p, browser_holder)
    pg = ctx.new_page()
    videos: list[str] = []

    def on_response(resp):
        ct = (resp.headers or {}).get("content-type", "")
        if "video/" in ct or resp.url.split("?")[0].endswith(".mp4"):
            videos.append(resp.url)

    pg.on("response", on_response)
    try:
        pg.goto(SEEDANCE_URL, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(5000)
        if not _looks_logged_in(pg):
            raise RuntimeError(
                "Seedance: нет сессии — владельцу нужно войти "
                "(infra/seedance_login_local.py) и залить куку на сервер")

        # 1. Кадры. Первый input[type=file] — начальный кадр, второй — конечный.
        file_inputs = pg.locator("input[type=file]")
        n = file_inputs.count()
        if n == 0:
            raise RuntimeError("Seedance: не нашёл поля загрузки кадров (UI изменился, см. /diag)")
        file_inputs.nth(0).set_input_files(first_path)
        pg.wait_for_timeout(4000)
        if last_path and os.path.exists(last_path) and n > 1:
            file_inputs.nth(1).set_input_files(last_path)
            pg.wait_for_timeout(4000)

        # 2. Промпт.
        composer = pg.locator("textarea, [contenteditable=true]").first
        composer.click(timeout=15000)
        composer.fill(prompt) if composer.evaluate("e => e.tagName") == "TEXTAREA" else \
            pg.keyboard.type(prompt)
        pg.wait_for_timeout(1500)

        # 3. Генерация.
        for label in ("Generate", "生成", "Создать", "Сгенерировать"):
            try:
                btn = pg.get_by_role("button", name=label).first
                if btn.is_visible(timeout=2500):
                    btn.click()
                    break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise RuntimeError("Seedance: не нашёл кнопку генерации (см. /diag)")

        # 4. Ждём mp4 в сетевом трафике страницы.
        deadline = time.time() + RENDER_TIMEOUT_S
        while time.time() < deadline and not videos:
            pg.wait_for_timeout(3000)
        if not videos:
            raise RuntimeError(f"Seedance: видео не отрендерилось за {RENDER_TIMEOUT_S}s")

        url = videos[-1]
        data = pg.request.get(url, timeout=180000).body()
        if len(data) < 20000:
            raise RuntimeError(f"Seedance: пришёл подозрительно маленький файл ({len(data)}b)")
        os.makedirs(OUT_DIR, exist_ok=True)
        fname = f"seedance_{uuid.uuid4().hex[:12]}.mp4"
        out = os.path.join(OUT_DIR, fname)
        with open(out, "wb") as f:
            f.write(data)
        log("saved", fname, len(data), "bytes")
        return {"video_path": out, "filename": fname, "bytes": len(data)}
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


def worker():
    while True:
        try:
            with sync_playwright() as p:
                browser_holder = [None]
                while True:
                    job = JOBS.get()
                    try:
                        if job.kind == "diag":
                            job.result = run_diag(browser_holder, p, job)
                        else:
                            job.result = run_animate(browser_holder, p, job)
                    except Exception as e:  # noqa: BLE001
                        job.error = f"{e}"
                        log("job failed:", str(e)[:300])
                        log(traceback.format_exc()[-500:])
                    finally:
                        job.ev.set()
        except Exception as e:  # noqa: BLE001
            log("playwright session died, restarting:", str(e)[:200])
            time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: A003
        pass

    def _denied(self) -> bool:
        ip = self.client_address[0]
        if not any(ip.startswith(p) for p in ALLOW_PREFIXES):
            self._send(403, {"ok": False, "error": "forbidden"})
            return True
        return False

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _submit(self, kind, **kw):
        job = Job(kind, **kw)
        JOBS.put(job)
        if not job.ev.wait(timeout=JOB_TIMEOUT_S):
            self._send(504, {"ok": False, "error": "seedance job timeout"})
            return
        if job.error:
            self._send(502, {"ok": False, "error": job.error})
            return
        self._send(200, {"ok": True, **(job.result or {})})

    def do_GET(self):  # noqa: N802
        if self._denied():
            return
        if self.path == "/health":
            self._send(200, {"ok": True, "cookies": os.path.exists(COOKIES_FILE)})
        elif self.path == "/diag":
            self._submit("diag")
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if self._denied():
            return
        if self.path != "/animate":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"bad json: {e}"})
            return
        if not (body.get("prompt") or "").strip():
            self._send(400, {"ok": False, "error": "prompt required"})
            return
        if not os.path.exists(body.get("first_image_path") or ""):
            self._send(400, {"ok": False, "error": "first_image_path не найден"})
            return
        self._submit(
            "animate",
            prompt=body["prompt"],
            first_image_path=body["first_image_path"],
            last_image_path=body.get("last_image_path") or "",
            duration_sec=body.get("duration_sec") or 5,
        )


def main():
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    log(f"listening on {BIND_HOST}:{BIND_PORT} proxy={PROXY} url={SEEDANCE_URL}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
