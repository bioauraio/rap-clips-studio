"""Каналы доставки сообщений людям: приложение, Telegram, почта.

ЧЕСТНОСТЬ ВМЕСТО МОЛЧАНИЯ. Транспорта у двух каналов из трёх на проде может
не быть вовсе, и это НЕ повод притворяться, что рассылка ушла. Каждый канал
отвечает на один вопрос — `state()` — тремя полями: включён ли, чего не
хватает и что положить в infra/.env, чтобы включился. Интерфейс показывает
это словами («канал не подключён: нет BOT_TOKEN»), а не пустым списком.

ЧТО ЕСТЬ НА САМОМ ДЕЛЕ, если ничего не настраивать:
  • inapp — работает всегда и стоит ноль. Плашка в приложении по тем же
    сегментам. Для «у тебя осталось 40 токенов» это точнее письма и не требует
    ни ключей, ни DMARC. С него и надо начинать.
  • tg — нужен BOT_TOKEN (тот же, что у бота). ВАЖНО: users.tg_id
    заполняется при входе через Login Widget и мини-апп, а это НЕ «человек
    нажал Start у бота». Тому, кто с ботом не начинал диалог, Bot API вернёт
    403 «bot can't initiate conversation» — такие адреса помечаются
    отписанными, а не ретраятся вечно.
  • email — транспорта в проекте НЕ БЫЛО НИКОГДА: ни smtplib, ни SDK, ни
    одной отправки. Здесь он появляется двумя способами на выбор —
    HTTP-API (Resend) или обычный SMTP. Плюс домену нужны SPF, DKIM и
    DMARC: без них массовая почта уходит в спам, и это уже проверено на
    другом нашем домене.

Модуль самодостаточен: ничего из main.py не импортирует и не знает про базу.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx

log = logging.getLogger("rapclips")

# ── Telegram ──
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
# Тот же мост наружу, что у бота: прямой выход в Bot API из России не живёт.
BOT_PROXY_URL = os.environ.get("BOT_PROXY_URL", "").strip().split(",")[0].strip()

# ── почта ──
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
SMTP_SSL = os.environ.get("SMTP_SSL", "").strip() in ("1", "true", "yes")
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", os.environ.get("BRAND_NAME", "lolq.ai")).strip()
# Физический адрес отправителя в подвале письма. Без него Gmail и Outlook
# режут массовую почту по своим правилам для отправителей — это требование,
# а не вежливость.
MAIL_POSTAL_ADDRESS = os.environ.get("MAIL_POSTAL_ADDRESS", "").strip()

CHANNELS = ("inapp", "tg", "email")


def _email_transport() -> str:
    if RESEND_API_KEY:
        return "resend"
    if SMTP_HOST and MAIL_FROM:
        return "smtp"
    return ""


def state() -> dict:
    """Что реально включено. Отдаётся в интерфейс как есть — интерфейс НЕ
    решает сам, работает канал или нет."""
    email_t = _email_transport()
    # note_code — КОД пояснения, note — та же мысль словами. Интерфейс берёт
    # перевод по коду (bc.note.* в i18n.js) и оставляет текст отсюда запасным
    # вариантом: новый канал появится раньше своего перевода. Отдавать наружу
    # одну только русскую фразу нельзя — английский кабинет показывал её как
    # есть, под английскими заголовками.
    #
    # missing — ЧИСТЫЙ список имён переменных окружения, без слов-связок:
    # «или» между двумя равноправными путями дописывает интерфейс на своём
    # языке (раньше связка «или SMTP_HOST + …» ехала внутри самого списка).
    return {
        "inapp": {
            "enabled": True, "transport": "inapp", "missing": [],
            "note_code": "inapp",
            "note": "плашка в приложении — работает всегда и стоит ноль",
        },
        "tg": {
            "enabled": bool(BOT_TOKEN), "transport": "bot_api",
            "missing": [] if BOT_TOKEN else ["BOT_TOKEN"],
            "note_code": "tg",
            "note": "пишем только тем, кто уже начинал диалог с ботом: "
                    "остальным Telegram отвечает 403",
        },
        "email": {
            "enabled": bool(email_t), "transport": email_t or "",
            "missing": [] if email_t else ["RESEND_API_KEY"],
            # Второй, равноправный путь: обычный SMTP вместо HTTP-API Resend.
            "missing_alt": [] if email_t else
                           ["SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_FROM"],
            "note_code": "email_ready" if email_t else "email_none",
            "note": ("домену нужны SPF, DKIM и DMARC, иначе письма уйдут в спам"
                     if email_t else
                     "транспорта нет: сервис не отправлял писем ни разу"),
            "from": MAIL_FROM,
            "postal_address": MAIL_POSTAL_ADDRESS,
        },
    }


def enabled(channel: str) -> bool:
    return bool(state().get(channel, {}).get("enabled"))


# ─────────────────────────── Telegram ───────────────────────────

async def _tg_call(method: str, **params) -> dict:
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(proxy=BOT_PROXY_URL or None,
                                 timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        r = await client.post(url, json=params)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"telegram {r.status_code}: {r.text[:200]}")
    if not data.get("ok"):
        raise TgSendError(int(data.get("error_code") or r.status_code),
                          str(data.get("description") or "")[:200],
                          int((data.get("parameters") or {}).get("retry_after") or 0))
    return data.get("result") or {}


class TgSendError(RuntimeError):
    """Ошибка Bot API с разбором: 403 — человек не начинал диалог или
    заблокировал бота (ретраить бессмысленно, надо отписать), 429 — просто
    слишком быстро (ждём retry_after)."""

    def __init__(self, code: int, description: str, retry_after: int = 0):
        super().__init__(f"{code}: {description}")
        self.code, self.description, self.retry_after = code, description, retry_after

    @property
    def permanent(self) -> bool:
        return self.code == 403 or "bot can't initiate" in self.description.lower()


def send_tg(chat_id: str, text: str) -> None:
    """Одно сообщение в Telegram. Бросает TgSendError — разбирает вызывающий."""
    if not BOT_TOKEN:
        raise RuntimeError("канал не подключён: нет BOT_TOKEN")
    asyncio.run(_tg_call("sendMessage", chat_id=str(chat_id), text=text[:4000],
                         parse_mode="HTML", disable_web_page_preview=True))


# ───────────────────────────── почта ─────────────────────────────

def _html_body(subject: str, body: str, unsub_url: str) -> str:
    """Письмо простое и без картинок: чем меньше разметки, тем меньше поводов
    у фильтров. Ссылка отписки — обязательная часть, а не украшение."""
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;line-height:1.55">{line}</p>'
        for line in body.replace("\r", "").split("\n\n") if line.strip())
    foot = []
    if MAIL_POSTAL_ADDRESS:
        foot.append(MAIL_POSTAL_ADDRESS)
    if unsub_url:
        foot.append(f'<a href="{unsub_url}" style="color:#8a7a6a">отписаться · unsubscribe</a>')
    footer = ("<hr style='border:none;border-top:1px solid #eee;margin:22px 0 12px'>"
              f"<p style='font-size:12px;color:#8a7a6a;margin:0'>{' · '.join(foot)}</p>"
              ) if foot else ""
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;color:#2d211a;max-width:560px;margin:0 auto;padding:24px">'
        f'<h2 style="font-size:20px;margin:0 0 16px">{subject}</h2>'
        f'{paragraphs}{footer}</div>'
    )


def send_email(to: str, subject: str, body: str, unsub_url: str = "") -> None:
    transport = _email_transport()
    if not transport:
        raise RuntimeError("канал не подключён: нет ни RESEND_API_KEY, ни SMTP_HOST")
    html = _html_body(subject, body, unsub_url)
    if transport == "resend":
        _send_resend(to, subject, html, body, unsub_url)
    else:
        _send_smtp(to, subject, html, body, unsub_url)


def _send_resend(to: str, subject: str, html: str, text: str, unsub_url: str) -> None:
    payload = {
        "from": f"{MAIL_FROM_NAME} <{MAIL_FROM}>" if MAIL_FROM else MAIL_FROM_NAME,
        "to": [to], "subject": subject, "html": html, "text": text,
    }
    if unsub_url:
        # List-Unsubscribe — то, по чему почтовики отличают нормального
        # отправителя от спамера; кнопка «отписаться» в Gmail берётся отсюда.
        payload["headers"] = {"List-Unsubscribe": f"<{unsub_url}>",
                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    r = httpx.post("https://api.resend.com/emails", json=payload, timeout=30.0,
                   headers={"Authorization": f"Bearer {RESEND_API_KEY}"})
    if r.status_code >= 300:
        raise RuntimeError(f"resend {r.status_code}: {r.text[:200]}")


def _send_smtp(to: str, subject: str, html: str, text: str, unsub_url: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
    msg["To"] = to
    if unsub_url:
        msg["List-Unsubscribe"] = f"<{unsub_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if SMTP_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as srv:
            if SMTP_USER:
                srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
        srv.starttls(context=ctx)
        if SMTP_USER:
            srv.login(SMTP_USER, SMTP_PASS)
        srv.send_message(msg)
