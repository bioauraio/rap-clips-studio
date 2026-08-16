#!/usr/bin/env python3
"""Одноразовый вход в Seedance (Dreamina) для seedance_gateway.

Запускается на Маке. Открывает окно Chromium ЧЕРЕЗ ТОТ ЖЕ заграничный выход,
которым потом ходит сервер (SSH-туннель до релея) — иначе сайт увидит смену
страны между входом и работой и выкинет сессию. Владелец логинится сам,
скрипт ловит появление куки и заливает storage_state на сервер.

  python3 seedance_login_local.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SERVER_SSH = os.environ.get("RAPCLIPS_SSH", "cfo")          # ~/.ssh/config
SSH_JUMP = os.environ.get("RAPCLIPS_SSH_JUMP", "bioura")     # прыжок (прод недоступен напрямую)
LOCAL_PORT = int(os.environ.get("SEEDANCE_LOGIN_PORT", "11084"))
LOGIN_URL = os.environ.get("SEEDANCE_LOGIN_URL", "https://dreamina.capcut.com/")
REMOTE_COOKIES = "/opt/claude-agents/openai/seedance_cookies.json"
# Куку считаем полученной по этим именам (Dreamina/CapCut passport).
MARKERS = ("sessionid", "sid_tt", "passport_csrf_token", "sso_uid_tt", "uid_tt")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


def main() -> None:
    print(f"[login] поднимаю SSH-туннель к заграничному выходу сервера (порт {LOCAL_PORT})…")
    # Держим туннель в цикле: обрыв посреди логина рвёт прокси у браузера.
    tunnel_cmd = (
        "while true; do "
        f"if nc -z 127.0.0.1 {LOCAL_PORT} 2>/dev/null; then sleep 10; else "
        f"ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 "
        f"-J {SSH_JUMP} -L {LOCAL_PORT}:127.0.0.1:1081 {SERVER_SSH}; sleep 5; fi; done"
    )
    tunnel = subprocess.Popen(["/bin/sh", "-c", tunnel_cmd], preexec_fn=os.setsid)
    time.sleep(4)
    if tunnel.poll() is not None:
        raise SystemExit("SSH-туннель не поднялся")

    state_file = tempfile.NamedTemporaryFile(prefix="seedance-", suffix=".json", delete=False)
    state_file.close()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                proxy={"server": f"socks5://127.0.0.1:{LOCAL_PORT}"},
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            ctx = browser.new_context(viewport={"width": 1440, "height": 950}, user_agent=UA)
            page = ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
            print()
            print("=" * 64)
            print("  Войди в Dreamina (Seedance) в открывшемся окне и оплати подписку.")
            print("  Окно закрывать не нужно — сессию заберу сам, как увижу вход.")
            print("=" * 64)

            state = None
            deadline = time.time() + 45 * 60
            while time.time() < deadline:
                time.sleep(3)
                try:
                    cookies = ctx.cookies()
                except Exception:
                    raise SystemExit("окно закрыто до завершения входа — запусти ещё раз")
                if any(c["name"] in MARKERS and c.get("value") for c in cookies):
                    time.sleep(6)  # добираем остальные auth-куки
                    state = ctx.storage_state()
                    break
            if state is None:
                raise SystemExit("вход не завершён за 45 минут")
            Path(state_file.name).write_text(json.dumps(state), encoding="utf-8")
            ctx.close()
            browser.close()
    finally:
        try:
            os.killpg(os.getpgid(tunnel.pid), signal.SIGTERM)
        except Exception:
            tunnel.terminate()

    cookies = json.loads(Path(state_file.name).read_text())["cookies"]
    print(f"[login] снял {len(cookies)} cookies, заливаю на сервер…")
    subprocess.run(
        ["scp", "-o", f"ProxyJump={SSH_JUMP}", state_file.name,
         f"{SERVER_SSH}:/tmp/seedance_cookies.json"], check=True)
    rc = subprocess.run(
        ["ssh", "-J", SSH_JUMP, SERVER_SSH,
         f"sudo mv /tmp/seedance_cookies.json {REMOTE_COOKIES} "
         f"&& sudo chmod 600 {REMOTE_COOKIES} "
         f"&& sudo systemctl restart seedance-gateway "
         f"&& sleep 3 && systemctl is-active seedance-gateway"],
    )
    if rc.returncode == 0:
        Path(state_file.name).unlink(missing_ok=True)
        print("[login] ГОТОВО: Seedance залогинен, шлюз перезапущен")
    else:
        raise SystemExit(f"не смог установить куку на сервере; файл остался: {state_file.name}")


if __name__ == "__main__":
    main()
