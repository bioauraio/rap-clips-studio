# Патч: публикация клипа в Instagram

Модуль `backend/social.py` уже лежит в репозитории и ни от чего не зависит —
он самодостаточный клиент host-агента. Чтобы кнопка появилась в студии, нужно
вставить фрагменты ниже в четыре файла, которые сейчас держат другие агенты:
`backend/db.py`, `backend/main.py`, `frontend/index.html`, `frontend/app.js`.

**Всё — только вставки.** Ни одна существующая строка не переписывается и не
удаляется; единственное исключение — одна строка в `schedulePoll()` (патч 4.2),
где к условию добавляется ещё одно слагаемое.

Номера строк — на состояние репозитория 2026-08-20, вечер. Они уже поехали
один раз, пока писался этот патч: другие агенты правят те же файлы прямо
сейчас. **Ориентируйся на якорь-текст, а не на номер** — все девять якорей
проверены на уникальность в текущих файлах.

Порядок применения: db → main → index.html → app.js → .env. После db.py и
main.py сервис уже рабочий (публиковать можно курлом), фронт — витрина.

---

## 1. `backend/db.py` — два поля в модель Track

**Якорь:** в `class Track`, сразу после блока «Итоговый клип трека»
(`clip_filename` / `clip_status` / `clip_error`, строки ~278–281).

```python
    # Публикация клипа в Instagram через host-агент (backend/social.py).
    # '' | queued | running | done | error:<текст>. Текст ошибки живёт прямо в
    # статусе: отдельное поле под него не заводим — фронт показывает всё,
    # что после «error:», как есть.
    published_ig = Column(String, nullable=False, default="")
    published_ig_at = Column(DateTime, nullable=True)
    # Ссылка на пост. Агент достаёт permalink не всегда (иногда отдаёт заглушку
    # вида instagram:browser:<время>) — тогда здесь пусто, но пост опубликован.
    published_ig_url = Column(String, nullable=False, default="")
```

**Миграция руками не нужна.** `init_db()` (db.py:359) при старте сам проходит по
моделям и добирает недостающие колонки `ALTER TABLE … ADD COLUMN` — база
владельца переживает деплой. `String`/`DateTime` и дефолт `""` этот механизм
обрабатывает штатно; `published_ig_at` уедет как `DATETIME` без DEFAULT
(`nullable=True`), это ровно то, что нужно.

Импорты в db.py уже содержат `Column, DateTime, String` — трогать шапку не надо.

---

## 2. `backend/main.py`

### 2.1. Импорт модуля

**Якорь:** блок импортов, строка `import mediagen` (строка ~28).

```python
import social
```

Ставить сразу после `import mediagen` — тот же уровень, плоские модули.

### 2.2. Публичная раздача клипа по подписанной ссылке

**Якорь:** сразу после роута `@app.get("/api/outbox/{filename}")` и его функции
`get_outbox` (заканчивается на `return FileResponse(path)`, строка ~2505 —
это ВТОРОЕ вхождение `return FileResponse(path)` в файле, не перепутай с первым).

```python
@app.get("/api/social/clip/{token}")
def get_social_clip(token: str, request: Request):
    """Клип для host-агента публикации — БЕЗ куки приложения.

    Агент качает mp4 сам, обычным urllib и без нашей сессии: из /api/media он
    получил бы 401 и уронил публикацию. Имя файла зашито внутрь подписи на
    SECRET_KEY, поэтому подобрать чужой файл нельзя, а срок жизни ссылки
    ограничен (SOCIAL_LINK_TTL_S, по умолчанию 6 часов)."""
    fname = social.clip_from_token(token)
    if not fname:
        raise HTTPException(404, "ссылка недействительна или устарела")
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return _media_response(path, request)
```

Почему рядом с `/api/outbox`: это второе (и последнее) место, где файл уходит
наружу без авторизации — пусть оба живут в одном месте и проверяются вместе.

### 2.3. Фоновая публикация + роут

**Якорь:** конец секции сборки клипа — сразу после функции
`assemble_track_clip` (`return {"ok": True, "scenes": len(approved)}`, строка
~2277) и **перед** комментарием `# ──── супергенерация ────`.

```python
# ───────────────────── публикация клипа в Instagram ─────────────────────

def _run_publish_ig(track_id: int, caption: str) -> None:
    """Публикация идёт минутами: host-агент кликает в живом браузере.

    Ответа во время работы нет вообще, поэтому статус в треке — единственное,
    по чему фронт понимает, что происходит. Ретраев здесь нет намеренно:
    повторный вызов агента = второй пост в ленте."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.published_ig = "running"
        db.commit()
        res = social.publish_clip_sync(
            track.clip_filename, caption,
            platform="instagram",
            title=track.title or "qlolvideo",
        )
        track = db.get(Track, track_id)
        if track:
            track.published_ig = "done"
            track.published_ig_at = now()
            track.published_ig_url = res.get("external_url") or ""
            db.commit()
        log.info("трек %s опубликован в instagram: %s", track_id, res.get("raw_external"))
    except social.PublishError as e:
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.published_ig = f"error:{e}"[:500]
            db.commit()
        log.warning("публикация трека %s не удалась [%s]: %s", track_id, e.code, e)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.published_ig = f"error:{e}"[:500]
            db.commit()
        log.warning("публикация трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/publish-instagram")
async def publish_track_instagram(track_id: int, request: Request,
                                  user: User = Depends(current_user),
                                  db: Session = Depends(db_session)):
    """Ставит готовый клип в очередь на публикацию.

    Все проверки — ДО постановки задачи: у агента нет отмены, а лишний пост из
    чужой ленты руками уже не уберёшь."""
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.clip_filename:
        raise HTTPException(400, "клип ещё не собран")
    if not os.path.exists(os.path.join(UPLOAD_DIR, track.clip_filename)):
        raise HTTPException(400, "файл клипа не найден — собери клип заново")
    if track.published_ig in ("queued", "running"):
        raise HTTPException(409, "этот клип уже публикуется — дождись результата")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — кнопка присылает пустое тело
        body = {}
    if not isinstance(body, dict):
        body = {}

    # Повтор уже опубликованного — только по явному подтверждению с фронта:
    # иначе второй клик кладёт в ленту дубль.
    if track.published_ig == "done" and not body.get("again"):
        raise HTTPException(409, "этот клип уже публиковали — повтор создаст второй пост")

    caption = (body.get("caption") or "").strip() or \
        social.build_caption(track.title, track.style)

    # Быстрая проверка живости: лучше честный отказ сейчас, чем «queued»,
    # который через 15 минут превратится в ошибку.
    health = await social.publisher_health()
    if not health["ready"]:
        raise HTTPException(503, health["detail"] or "служба публикации недоступна")

    track.published_ig = "queued"
    track.published_ig_url = ""
    db.commit()
    Thread(target=_run_publish_ig, args=(track_id, caption), daemon=True).start()
    return {"ok": True, "status": "queued", "caption": caption}


@app.get("/api/social/health")
async def social_health(user: User = Depends(current_user)):
    """Состояние службы публикации — владельцу, чтобы понимать, почему кнопка
    ругается: служба не поднята, нет VPN или слетела сессия."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    return await social.publisher_health()
```

### 2.4. Отдать статус фронту

**Якорь:** словарь в `track_dict` (строка ~1100), строка
`"clip_status": t.clip_status, "clip_error": t.clip_error,`.

Добавить следующей строкой:

```python
        "published_ig": t.published_ig,
        "published_ig_at": t.published_ig_at.isoformat() if t.published_ig_at else "",
        "published_ig_url": t.published_ig_url,
```

---

## 3. `frontend/index.html` — кнопка на этапе «Монтаж»

**Якорь:** `<section class="stage-pane" data-stage="final">` → внутри
`<div class="clip-block">`, после `<div class="row">` с кнопкой `.assemble` и
ссылкой `.clip-download` (её закрывающий `</div>`, строка ~248) и **перед**
`</div>`, закрывающим `clip-block`.

```html
          <div class="row publish-row">
            <button class="publish-ig" title="выложить готовый клип в Instagram">📷 Опубликовать в Instagram</button>
            <a class="publish-ig-link ghost hidden" target="_blank" rel="noopener">открыть пост</a>
          </div>
          <div class="publish-ig-status status"></div>
```

Классы взяты существующие (`row`, `ghost`, `status`, `hidden`) — новых стилей и
цветов не добавляется, правки `style.css` не требуются.

---

## 4. `frontend/app.js`

### 4.1. Отрисовка состояния

**Якорь:** этап 5 в `renderTrack`, сразу после строки
`asmBtn.addEventListener("click", () => assembleClip(t.id));` (строка ~1349).

```js
  // Публикация в Instagram: живёт рядом с готовым клипом — публиковать нечего,
  // пока клип не собран.
  const pubBtn = $(".publish-ig", card);
  const pubStatusEl = $(".publish-ig-status", card);
  const pubLink = $(".publish-ig-link", card);
  const pub = t.published_ig || "";
  const pubBusy = ["queued", "running"].includes(pub);
  pubBtn.disabled = pubBusy || !t.clip_url;
  pubBtn.title = t.clip_url ? "выложить готовый клип в Instagram" : "сначала собери клип";
  pubBtn.textContent = pubBusy
    ? "публикую… (до 15 мин)"
    : pub === "done" ? "📷 Опубликовать ещё раз" : "📷 Опубликовать в Instagram";
  if (pub.startsWith("error:")) {
    // Честный текст ошибки от бэкенда — что именно делать, там уже написано.
    pubStatusEl.textContent = pub.slice(6);
    pubStatusEl.className = "publish-ig-status status error";
  } else if (pub === "done") {
    const when = t.published_ig_at ? new Date(t.published_ig_at).toLocaleString("ru-RU") : "";
    pubStatusEl.textContent = when ? `опубликовано ${when}` : "опубликовано";
    pubStatusEl.className = "publish-ig-status status done";
  } else if (pubBusy) {
    pubStatusEl.textContent = "браузер на сервере выкладывает клип, это долго — не закрывай вкладку до ответа";
    pubStatusEl.className = "publish-ig-status status";
  } else {
    pubStatusEl.textContent = "";
    pubStatusEl.className = "publish-ig-status status";
  }
  if (t.published_ig_url) {
    pubLink.href = t.published_ig_url;
    pubLink.classList.remove("hidden");
  }
  pubBtn.addEventListener("click", () => publishInstagram(t.id, pub === "done"));
```

### 4.2. Продолжать опрос, пока идёт публикация

**Якорь:** функция `schedulePoll()` (строка ~1013), строка внутри `.some(...)`:

```js
        ["queued", "running"].includes(t.clip_status) ||
```

Заменить её на две строки (единственная правка существующей строки во всём
патче — к условию добавляется слагаемое):

```js
        ["queued", "running"].includes(t.clip_status) ||
        ["queued", "running"].includes(t.published_ig) ||
```

Без этого статус публикации застынет на «в очереди» до ручного обновления
страницы.

### 4.3. Сам вызов

**Якорь:** рядом с `assembleClip` (строка ~1709) — вставить следующей функцией
после неё.

```js
async function publishInstagram(id, again) {
  // Повтор уже опубликованного клипа = второй пост в ленте, поэтому спрашиваем.
  if (again && !confirm("Этот клип уже публиковали. Выложить ещё раз? В ленте появится второй пост.")) return;
  try {
    await api(`/api/tracks/${id}/publish-instagram`, { method: "POST", body: { again: Boolean(again) } });
  } catch (e) {
    alert(e.message);
  }
  await loadProject();
}
```

---

## 5. `infra/.env` — переменные

Дописать в `infra/.env` (и, для памяти, в `infra/.env.example`):

```dotenv
# Профиль браузера, в который публикуем клипы. ОТДЕЛЬНЫЙ от бренд-аккаунта
# BIOAURA: дефолтный профиль агента общий, туда лить нельзя.
SOCIAL_IG_ACCOUNT_KEY=qlol
# Публичный адрес сервиса — из него собирается ссылка на mp4 для агента.
PUBLIC_BASE_URL=https://qlolapp.art
# Сколько ждать публикацию: браузер кликает вживую. Меньше 900 не ставить —
# столько же ждёт сам агент.
SOCIAL_PUBLISH_TIMEOUT_S=900
```

Необязательные, со здравыми дефолтами внутри `social.py` — добавлять только при
необходимости:

```dotenv
# Агент живёт на том же хосте, что и контейнер: пусть забирает файл напрямую,
# не гоняя десятки мегабайт наружу через nginx.
SOCIAL_VIDEO_BASE_URL=http://127.0.0.1:8930
# Адрес host-агента (дефолт уже такой).
SOCIAL_PUBLISHER_URL=http://172.18.0.1:8771
# Сколько живёт подписанная ссылка на клип, секунд (дефолт 6 часов).
SOCIAL_LINK_TTL_S=21600
```

`SECRET_KEY` уже есть в `.env` — им же подписывается ссылка на клип. Отдельного
секрета заводить не нужно.

Пересборка образа для новых переменных не нужна (`env_file: .env` в
docker-compose), но контейнер надо перезапустить — и всё равно придётся, ради
нового кода.

---

## 6. Проверка после вставки

```bash
# 1. Синтаксис (локально, до деплоя)
python3 -m py_compile backend/main.py backend/db.py backend/social.py

# 2. Миграция и старт: колонки добираются автоматически при init_db()
docker compose -f infra/docker-compose.yml up -d --build rapclips
docker logs --tail 30 rapclips-api          # ждём Uvicorn running, без трейсбеков

# 3. Колонки на месте
docker exec rapclips-api python -c "import sqlite3;print([r[1] for r in \
  sqlite3.connect('/data/rapclips.db').execute('PRAGMA table_info(tracks)')])" | tr ',' '\n' | grep published

# 4. Служба публикации (только админом, с кукой владельца)
curl -s -b cookies.txt https://qlolapp.art/api/social/health

# 5. Подписанная ссылка отдаёт файл БЕЗ куки (главное условие работы агента)
#    — взять адрес из лога публикации и дёрнуть его чистым curl:
curl -sI 'https://qlolapp.art/api/social/clip/<token>.mp4' | head -3   # ждём 200 и video/mp4
```

Ошибка `no such column: tracks.published_ig` означает, что контейнер поднялся
на старом коде db.py — пересобери образ, не только перезапусти.

---

## 7. Чего в патче намеренно нет

* **Автопубликации по готовности клипа.** Пост в ленту — необратимое действие,
  его запускает человек кнопкой.
* **Ретраев публикации.** `POST /publish` не идемпотентен: повтор = второй пост.
  Ретраится только `/health`.
* **Расписания и очереди на нашей стороне.** Очередь уже есть у агента, вторая
  поверх неё только запутает статусы.
* **YouTube/TikTok.** `social.py` их умеет (параметр `platform`), профили на
  сервере есть, но каждая площадка требует своего логина и своих проверок —
  включать отдельной задачей, когда Instagram отработает.
