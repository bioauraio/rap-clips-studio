# Патч: раздел «Аудио» — озвучка, музыка, мастеринг, разбор дорожки

Три новых модуля уже лежат в репозитории и ни от чего не зависят:

| Файл | Что делает |
|---|---|
| `backend/audio.py` | ElevenLabs: речь (TTS), музыка (Eleven Music), звуковые эффекты + ffmpeg-утилиты |
| `backend/mastering.py` | мастеринг: matchering (по эталону) / ffmpeg (громкость) / RoEx (платный) |
| `backend/audio_analysis.py` | BPM, сетка долей, границы секций, энергия, точки реза сцен |
| `backend/matchering_service.py` | сайдкар мастеринга (отдельный контейнер, GPL-изоляция) |
| `infra/matchering.Dockerfile` | образ для этого сайдкара |

Ниже — фрагменты для четырёх файлов, которые сейчас держат другие агенты:
`backend/requirements.txt` (уже дописан), `backend/db.py`, `backend/main.py`,
и подсказки для фронта.

**Всё — вставки.** Существующие строки не переписываются. Единственное
исключение отмечено явно: п. 3.7 (необязательный) меняет две строки в
генерации сцен, и без него всё работает — просто нарезка остаётся «на глаз».

Ориентируйся на **якорь-текст**, а не на номера строк: файлы правятся
параллельно и номера уже поехали. Все якоря проверены на уникальность
в текущих файлах (состояние 21.08.2026, утро).

Порядок применения: requirements → db → main → .env → compose → фронт.
После db.py и main.py раздел уже рабочий курлом; фронт — витрина.

Важно: **без ключей ничего не ломается.** Нет `ELEVENLABS_API_KEY` — генерация
честно отвечает «not connected». Нет `MATCHERING_URL` — мастеринг остаётся на
ffmpeg. Нет `ROEX_API_KEY` — платная кнопка не показывается. Нет `numpy` —
разбор дорожки выключается, остальной сервис живёт.

---

## 1. `backend/requirements.txt` — уже дописано

```
numpy==2.1.3
```

Единственная новая зависимость раздела, ~18 МБ. `librosa` намеренно НЕ берём:
она тянет numba, llvmlite, scikit-learn, joblib, soxr и pooch — это +400…500 МБ
к образу, который сейчас весит около 400. Всё, что нам от неё нужно, посчитано
на голом numpy (см. шапку `audio_analysis.py`). Если librosa когда-нибудь
окажется в образе по другой причине — модуль сам переключится на неё для
темпа и долей, менять код не придётся.

---

## 2. `backend/db.py` — семь полей в модель Track

**Якорь:** в `class Track`, сразу после строки
`supergen_note = Column(Text, nullable=False, default="")`
и **перед** `created_at = Column(DateTime, default=now)`.

```python
    # ─────────────────────────── раздел «Аудио» ───────────────────────────
    # Мастер трека: отдельный файл, исходник НЕ перезаписываем. Причина та же,
    # что у версий сборки клипа, — исходная дорожка могла уже уехать в клип и
    # в раскадровку, и «улучшить» её задним числом значит рассинхронить всё.
    master_file = Column(String, nullable=False, default="")
    master_status = Column(String, nullable=False, default="")  # '' | queued | running | done | error
    # На успехе — человеческий отчёт (что сделали, громкость до/после),
    # на ошибке — текст ошибки. Отдельного поля под ошибку не заводим:
    # состояние различает master_status.
    master_note = Column(Text, nullable=False, default="")
    master_engine = Column(String, nullable=False, default="")  # matchering | ffmpeg | roex
    # Эталонный трек «звучать как это» — файл в UPLOAD_DIR. Храним, чтобы
    # повторный мастеринг не заставлял грузить его заново.
    master_ref_filename = Column(String, nullable=False, default="")

    # Разбор дорожки: темп для показа человеку и полный результат JSON'ом
    # (доли, сильные доли, границы секций, энергия). Кэш, а не источник
    # правды: пересчитывается из файла за доли секунды, живёт до смены аудио.
    bpm = Column(Integer, nullable=False, default=0)
    beats_json = Column(Text, nullable=False, default="")
```

**Миграция руками не нужна:** `init_db()` при старте проходит по моделям и
добирает недостающие колонки `ALTER TABLE … ADD COLUMN`. `String`/`Text`/
`Integer` с дефолтами `""`/`0` этот механизм обрабатывает штатно.

**Импорты в db.py трогать не надо** — `Column, Integer, String, Text` уже там.
Поле `bpm` намеренно `Integer`, а не `Float`: целое число читает человек, а
точное значение (87.5) и так лежит внутри `beats_json`. Так патч не требует
добавлять `Float` в шапку импортов чужого файла.

---

## 3. `backend/main.py`

### 3.1. Импорт модулей

**Якорь:** блок импортов, строка `import stripe_pay`.

```python
import audio
import audio_analysis
import mastering
```

Ставить сразу после `import stripe_pay` — тот же уровень, плоские модули.
`mastering` сам импортирует `audio`, порядок значения не имеет.

### 3.2. Цены раздела в очках

**Якорь:** строка `SCENE_SEC = 6              # средняя длина сцены, из claude.py`
(конец блока «сколько стоит работа»).

```python

# ───────────────────────── раздел «Аудио» ─────────────────────────
# Считаем по худшему для нас курсу очка — 1.13 ¢ в пакете p15000. Если цена
# сходится там, она сходится везде.
#
# Речь: ElevenLabs берёт ~1 кредит за символ, на тарифе Creator это
# ~$0.00018/символ. 1000 символов обходятся нам в ~$0.18, значит меньше 16
# очков за них брать нельзя. Берём 20 — покрывает и склейку кусков ffmpeg'ом.
COST_TTS_PER_100_CHARS = 2
COST_TTS_MIN = 2
# Музыка: 900 кредитов за минуту ≈ $0.16/мин. 12 очков за 30 секунд = 24 за
# минуту ≈ $0.27 по худшему курсу — полтора конца сверху, и трёхминутный бит
# стоит столько же, сколько трёхминутный клип на Grok (120 очков).
COST_MUSIC_PER_30S = 12
COST_SFX = 4                 # как сцена на Grok: короткая генерация
# Мастеринг своим движком стоит нам ноль (CPU) — берём символические очки,
# чтобы кнопку не жали в цикле по сто раз.
COST_MASTER_LOCAL = 6
# Облачный мастеринг — живые $1.76–2.20 из кассы. 200 очков ≈ $2.26 по худшему
# курсу: ровно себестоимость, без наценки. Дороже целого клипа — поэтому он
# и не движок по умолчанию (см. шапку backend/mastering.py).
COST_MASTER_CLOUD = 200
# Разбор дорожки считается у нас на CPU за доли секунды — бесплатно.
COST_ANALYSIS = 0


def _tts_cost(text: str) -> int:
    """Тарифицируем символами, как сам ElevenLabs: любой другой способ либо
    обдирает за короткий хук, либо дарит длинный войсовер."""
    chars = len(text or "")
    return max(COST_TTS_MIN, -(-chars // 100) * COST_TTS_PER_100_CHARS)


def _music_cost(seconds: float) -> int:
    """Полминуты — минимальная единица: короче генерация всё равно не бывает."""
    blocks = max(1, -(-int(round(seconds)) // 30))
    return blocks * COST_MUSIC_PER_30S


def _refund(db: Session, user: User, points: int, what: str) -> None:
    """Вернуть очки, если оплаченный шаг не состоялся.

    Списываем ДО обращения к внешнему сервису (иначе параллельные запросы
    уводят баланс в минус), а при отказе сервиса возвращаем. Без этого самый
    обидный сценарий выглядит так: ElevenLabs ответил 500, очки списаны,
    аудио нет."""
    if user.is_admin or points <= 0:
        return
    user.gen_points = int(user.gen_points or 0) + points
    db.commit()
    log.info("user %s: +%s очков возврат за %s", user.id, points, what)
```

### 3.3. Мастер и темп в карточке трека

**Якорь:** в `track_dict`, строка
`"supergen_status": t.supergen_status, "supergen_note": t.supergen_note,`

```python
        "master_url": f"/api/media/{t.master_file}" if t.master_file else "",
        "master_status": t.master_status, "master_note": t.master_note,
        "master_engine": t.master_engine,
        "bpm": t.bpm,
```

### 3.4. Публичная раздача исходника (нужна только облачному мастерингу)

**Якорь:** сразу после роута `@app.get("/api/outbox/{filename}")` и его функции
`get_outbox`. Если патч публикации в Instagram (`social_patch.md`) уже
применён — ставить следом за `get_social_clip`, они про одно и то же.

```python
@app.get("/api/audio/public/{token}")
def get_audio_source(token: str, request: Request):
    """Исходник трека для внешнего сервиса мастеринга — БЕЗ куки приложения.

    RoEx Tonn умеет принимать трек ТОЛЬКО по ссылке: загрузить файлом нельзя.
    Из /api/media он получил бы 401 (файлы приватные), поэтому здесь отдельный
    подписанный адрес: имя файла зашито в подпись на SECRET_KEY, срок жизни
    ограничен (MASTER_LINK_TTL_S, по умолчанию 6 часов).

    Своим движкам (matchering, ffmpeg) этот роут не нужен вообще — они
    работают с файлом на диске и наружу аудио не выпускают. Ещё один довод
    держать их движками по умолчанию."""
    fname = mastering.source_from_token(token)
    if not fname:
        raise HTTPException(404, "ссылка недействительна или устарела")
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return _media_response(path, request)
```

### 3.5. Роуты раздела «Аудио»

**Якорь:** перед строкой `@app.get("/api/health")` (конец файла, до
`_backfill_scene_ledger`). Место выбрано намеренно нейтральное — там сейчас
никто не правит.

```python
# ═══════════════════════════ раздел «Аудио» ═══════════════════════════
# Генерация звука (ElevenLabs), мастеринг и разбор дорожки. Логика — в
# backend/audio.py, backend/mastering.py, backend/audio_analysis.py;
# здесь только роуты, права и очки.

def _audio_error(e) -> "ApiError":
    """Ошибки модулей → ApiError с машинным кодом.

    Фронту важно различать «ключ не подключён» (показать владельцу инструкцию)
    и «текст слишком длинный» (показать пользователю). Разбирать текст ошибки
    на клиенте — верный способ сломаться на первой же смене формулировки."""
    status = {"disabled": 503, "network": 503, "timeout": 504,
              "input": 400, "auth": 502, "credits": 402}.get(getattr(e, "code", ""), 502)
    return ApiError(status, f"audio_{getattr(e, 'code', 'error')}", str(e))


@app.get("/api/audio/status")
def audio_status(user: User = Depends(current_user)):
    """Что в разделе живо и почём. Ключи наружу не отдаём — только флаги."""
    return {
        "generation": audio.status(),
        "mastering": mastering.status(),
        "analysis": {"enabled": audio_analysis.available()},
        "cost": {
            "tts_per_100_chars": COST_TTS_PER_100_CHARS,
            "music_per_30_sec": COST_MUSIC_PER_30S,
            "sfx": COST_SFX,
            "master": COST_MASTER_LOCAL,
            "master_cloud": COST_MASTER_CLOUD,
        },
    }


@app.get("/api/audio/voices")
async def audio_voices(language: str = "", refresh: bool = False,
                       user: User = Depends(current_user)):
    """Каталог голосов подписки. language — 'en', 'ru', пусто = все."""
    try:
        return {"voices": await audio.list_voices(language=language, refresh=refresh)}
    except audio.AudioError as e:
        raise _audio_error(e)


@app.post("/api/audio/tts")
async def audio_tts(
    text: str = Form(""), voice_id: str = Form(""),
    stability: float = Form(0.45), similarity: float = Form(0.85),
    style: float = Form(0.35), speed: float = Form(1.0),
    language: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Текст → mp3. Списываем ДО обращения к ElevenLabs и возвращаем при отказе."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "нечего озвучивать")
    cost = _tts_cost(text)
    _charge(db, user, cost, "озвучка")
    try:
        res = await audio.synthesize(
            text, voice_id, language=language,
            settings=audio.voice_settings(stability, similarity, style, speed),
        )
    except audio.AudioError as e:
        _refund(db, user, cost, "озвучка")
        raise _audio_error(e)
    _reg_file(db, res["filename"], user.id)
    db.commit()
    return {"url": f"/api/media/{res['filename']}", "charged": cost, **res}


@app.post("/api/audio/music")
async def audio_music(
    prompt: str = Form(""), seconds: float = Form(30.0),
    instrumental: bool = Form(False),
    track_id: int = Form(0), attach: bool = Form(False),
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Промпт → музыкальный фрагмент.

    attach=true подставляет результат дорожкой указанного трека — это главный
    сценарий раздела: сгенерировал бит, он сразу стал треком, дальше обычный
    конвейер студии. Без флага файл просто отдаётся ссылкой и ничего не
    перезаписывает."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "опиши, какая нужна музыка")
    track = _own_track(db, user, track_id) if (attach and track_id) else None
    cost = _music_cost(seconds)
    _charge(db, user, cost, "музыка")
    try:
        res = await audio.compose_music(prompt, seconds, instrumental=instrumental)
    except audio.AudioError as e:
        _refund(db, user, cost, "музыка")
        raise _audio_error(e)
    _reg_file(db, res["filename"], user.id)
    if track is not None:
        _attach_track_audio(db, track, res["filename"])
    db.commit()
    return {"url": f"/api/media/{res['filename']}", "charged": cost,
            "track_id": track.id if track else 0, **res}


@app.post("/api/audio/sfx")
async def audio_sfx(
    prompt: str = Form(""), seconds: float = Form(0.0),
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Промпт → звуковой эффект. seconds=0 — длину выбирает модель."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "опиши, какой нужен звук")
    _charge(db, user, COST_SFX, "звуковой эффект")
    try:
        res = await audio.sound_effect(prompt, seconds)
    except audio.AudioError as e:
        _refund(db, user, COST_SFX, "звуковой эффект")
        raise _audio_error(e)
    _reg_file(db, res["filename"], user.id)
    db.commit()
    return {"url": f"/api/media/{res['filename']}", "charged": COST_SFX, **res}


def _attach_track_audio(db: Session, track: Track, filename: str) -> None:
    """Подставить файл дорожкой трека и пересчитать всё, что от неё зависит.

    Разбор (bpm/beats_json) обнуляем намеренно: старая сетка долей к новой
    дорожке отношения не имеет, а «залипший» BPM от прошлого трека — ровно
    та ошибка, которую потом ищут неделю."""
    path = os.path.join(UPLOAD_DIR, filename)
    track.audio_filename = filename
    track.audio_duration_sec = _ffprobe_duration(path)
    try:
        track.audio_profile = _audio_profile(path, track.audio_duration_sec)
    except Exception as e:  # noqa: BLE001
        log.warning("профиль звука не посчитался: %s", e)
    track.bpm = 0
    track.beats_json = ""
    db.commit()


# ───────────────────────────── мастеринг ─────────────────────────────

def _run_master(track_id: int, engine: str, target: str, style: str,
                ref_name: str, cost: int) -> None:
    """Мастеринг идёт минутами (у облачного — до четверти часа), поэтому
    фоном со статусом, как сборка клипа.

    Очки списываем ЗДЕСЬ, по факту готового файла, а не при постановке в
    очередь: у облачного движка кредиты тратит только финальный проход, и
    если чужой API упал — человек не должен за это платить. Баланс при этом
    проверен заранее в роуте, так что «сделали и не смогли взять» — это
    только гонка двух вкладок, и она стоит нам одного мастеринга, а не денег
    пользователя."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.master_status = "running"
        track.master_note = ""
        db.commit()
        res = mastering.master_sync(
            track.audio_filename, engine=engine, reference_filename=ref_name,
            target=target, style=style)
        track = db.get(Track, track_id)
        if not track:
            return
        old = track.master_file
        track.master_file = res["filename"]
        track.master_engine = res["engine"]
        track.master_note = res["note"]
        track.master_status = "done"
        _reg_file(db, res["filename"], track.project.owner_id)
        db.commit()
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        if owner and not _take_points(db, owner, cost):
            log.warning("мастеринг трека %s готов, но очков на списание не хватило", track_id)
        _remove_media(old)
        log.info("мастеринг трека %s: движок %s, %s → %s LUFS", track_id,
                 res["engine"], res["before"]["lufs"], res["after"]["lufs"])
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.master_status = "error"
            track.master_note = str(e)[:500]
            db.commit()
        log.warning("мастеринг трека %s упал: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/master")
async def master_track(
    track_id: int,
    engine: str = Form("auto"), target: str = Form("streaming"),
    style: str = Form("HIPHOP_GRIME"),
    reference: UploadFile | None = None,
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Мастеринг дорожки трека. Фоном, статус — в track.master_status.

    reference — эталонный трек «хочу звучать как это». Без него движок по
    эталону работать не может и честно понижается до выравнивания громкости
    (об этом будет сказано в отчёте, а не молча)."""
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "у трека нет дорожки")
    if track.master_status in ("queued", "running"):
        raise HTTPException(409, "мастеринг этого трека уже идёт")

    engine = engine if engine in ("auto", "matchering", "ffmpeg", "roex") else "auto"
    if engine == "roex":
        if not mastering.roex_available():
            raise ApiError(503, "mastering_cloud_off",
                           "Cloud mastering is not connected yet.")
        # Платный движок тратит деньги владельца сервиса — на бесплатном
        # тарифе его нет, иначе первый же гость уводит кассу в минус.
        if _plan_of(user) == "free":
            raise ApiError(402, "plan_required",
                           "Cloud mastering is available on paid plans.",
                           plan="pro")
    cost = COST_MASTER_CLOUD if engine == "roex" else COST_MASTER_LOCAL
    if not user.is_admin and int(user.gen_points or 0) < cost:
        raise NotEnoughPoints(cost, int(user.gen_points or 0), _plan_of(user), "мастеринг")

    ref_name = track.master_ref_filename or ""
    if reference is not None:
        ext = os.path.splitext(reference.filename or "")[1] or ".mp3"
        ref_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, ref_name), "wb") as f:
            f.write(await reference.read())
        _reg_file(db, ref_name, user.id)
        track.master_ref_filename = ref_name
    track.master_status = "queued"
    track.master_note = ""
    db.commit()
    Thread(target=_run_master,
           args=(track_id, engine, target, style, ref_name, cost), daemon=True).start()
    return {"ok": True, "engine": engine, "cost": cost}


# ───────────────────────── разбор дорожки ─────────────────────────

@app.get("/api/tracks/{track_id}/analysis")
def track_analysis(track_id: int, refresh: bool = False,
                   user: User = Depends(current_user), db: Session = Depends(db_session)):
    """BPM, сетка долей, границы секций и рекомендованные точки реза сцен.

    Роут синхронный (обычный def) — FastAPI сам уведёт его в пул потоков, а
    разбор трёхминутного трека занимает меньше секунды. Результат кэшируем в
    beats_json: пересчитывать одно и то же на каждое открытие карточки незачем.
    """
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "у трека нет дорожки")
    if not audio_analysis.available():
        raise ApiError(503, "analysis_off",
                       "Track analysis is not available in this build (numpy missing).")
    data = None
    if track.beats_json and not refresh:
        try:
            data = json.loads(track.beats_json)
        except ValueError:
            data = None
    if data is None:
        try:
            data = audio_analysis.analyze(_track_audio_path(track))
        except audio_analysis.AnalysisError as e:
            raise _audio_error(e)
        track.bpm = int(round(data.get("bpm") or 0))
        track.beats_json = json.dumps(data)
        db.commit()
    cuts = audio_analysis.suggest_cuts(data, target_sec=SCENE_SEC)
    return {
        "bpm": data.get("bpm"),
        # Вторая правдоподобная подпись темпа: 87 и 174 автомат не различает,
        # и делать вид, что различает, — врать пользователю.
        "bpm_alt": data.get("bpm_alt"),
        "confidence": data.get("bpm_confidence"),
        "bar_sec": data.get("bar_sec"),
        "beat_sec": data.get("beat_sec"),
        "duration_sec": data.get("duration_sec"),
        "sections": data.get("sections"),
        "energy": data.get("energy"),
        "downbeats": data.get("downbeats"),
        "cuts": cuts,
        "engine": data.get("engine"),
    }
```

### 3.6. Проверка перед деплоем

В памяти проекта есть отдельная заметка про то, как новый роут роняет сервис
**на импорте**. Здесь все три известные грабли обойдены заранее, но проверить
стоит:

* ни один роут не отвечает `204` с телом;
* у всех необязательных параметров есть явный тип и значение
  (`refresh: bool = False`, `seconds: float = Form(0.0)`);
* `multipart` уже используется в `create_track`, `python-multipart` в
  requirements есть — новые `Form(...)` ничего не добавляют к зависимостям;
* `json` уже импортирован в main.py (используется в платёжках) — отдельный
  импорт не нужен;
* `uuid`, `os`, `Thread` — тоже уже на месте.

После рестарта: `curl -s localhost:8930/api/health` и
`curl -s -b cookie localhost:8930/api/audio/status` — второй показывает,
какие движки видит сервис.

### 3.7. (Необязательно) Резать сцены по долям, а не «на глаз»

Это тот самый техдолг: сейчас Claude сам придумывает `duration_sec` кадра, и
склейки почти никогда не попадают в такт. Два способа это починить, оба
опираются на уже написанные функции.

**Вариант А — две строки, нулевой риск.** Профиль звука уходит в промпт
раскадровки как есть (`claude.py`, блок «Профиль звука»). Значит достаточно
положить туда измеренный профиль вместо словесного.

**Якорь:** в `_run_scenes_generation`, строка
`audio_profile=track.audio_profile,` в вызове `claude.generate_scenes`.

```python
            audio_profile=_beat_profile(track) or track.audio_profile,
```

и рядом, перед `_run_scenes_generation`:

```python
def _beat_profile(track: Track) -> str:
    """Профиль дорожки с измеренным темпом — для промпта раскадровки.

    Пусто, если разбор недоступен или трек неритмичный: тогда остаётся
    прежний профиль громкости, и ничего не ломается."""
    if not audio_analysis.available() or not track.audio_filename:
        return ""
    try:
        data = json.loads(track.beats_json) if track.beats_json else \
            audio_analysis.analyze(_track_audio_path(track))
    except Exception as e:  # noqa: BLE001
        log.warning("разбор дорожки трека %s не удался: %s", track.id, e)
        return ""
    if float(data.get("bpm_confidence") or 0) < 0.2:
        return ""
    return audio_analysis.profile_text(data)
```

**Вариант Б — настоящая починка.** Длительности сцен берутся не от Claude, а
из сетки долей: модель придумывает содержание кадров, а тайминг задаёт музыка.

**Якорь:** в `_run_scenes_generation`, строки

```python
        cursor = 0
        for i, sc in enumerate(result.get("scenes", []), start=1):
            dur = max(2, min(12, int(sc.get("duration_sec") or 6)))
```

заменить на

```python
        # Тайминг задаёт музыка, а не модель: Claude отвечает за содержание
        # кадров, а границы сцен ложатся на начала тактов. Если сетки нет
        # (речь, эмбиент, неудачный разбор) — работает прежнее поведение.
        scenes_raw = result.get("scenes", [])
        grid = _beat_cuts(track, len(scenes_raw))
        cursor = 0
        for i, sc in enumerate(scenes_raw, start=1):
            dur = (grid[i - 1] if i - 1 < len(grid)
                   else max(2, min(12, int(sc.get("duration_sec") or 6))))
```

плюс функция рядом с `_beat_profile`:

```python
def _beat_cuts(track: Track, want: int) -> list:
    """Длительности сцен, привязанные к тактам. Пустой список — сетки нет.

    Число резов подгоняем под число кадров от Claude: лишние соседние резы
    склеиваем, недостающие добираем прежним способом. Иначе пришлось бы либо
    выбрасывать кадры, либо оставлять хвост трека без картинки."""
    if want <= 0 or not audio_analysis.available() or not track.audio_filename:
        return []
    try:
        data = json.loads(track.beats_json) if track.beats_json else \
            audio_analysis.analyze(_track_audio_path(track))
    except Exception:  # noqa: BLE001
        return []
    if float(data.get("bpm_confidence") or 0) < 0.2:
        return []
    dur = float(data.get("duration_sec") or 0)
    target = max(2.0, min(10.0, dur / want)) if dur else float(SCENE_SEC)
    cuts = audio_analysis.suggest_cuts(data, target_sec=target)
    out = [c["duration"] for c in cuts]
    while len(out) > want and len(out) > 1:  # склеиваем самые короткие
        j = min(range(len(out) - 1), key=lambda k: out[k] + out[k + 1])
        out[j] = out[j] + out[j + 1]
        del out[j + 1]
    return [max(2, min(12, int(round(x)))) for x in out]
```

Вариант Б стоит включать после того, как посмотришь глазами на пару треков:
он меняет монтаж всех новых клипов.

---

## 4. `infra/.env` — ключи и адреса

```dotenv
# ── Генерация аудио (ElevenLabs) ──
# Ключ: elevenlabs.io → Profile → API keys. Без него раздел честно выключен.
ELEVENLABS_API_KEY=
# Модель речи. Дефолт — многоязычная (понимает и русский, и английский).
# eleven_v3 выразительнее, но лимит текста втрое меньше и дороже.
ELEVENLABS_MODEL=eleven_multilingual_v2
ELEVENLABS_MUSIC_MODEL=music_v1
# 192 kbps требует тариф Creator и выше; на более низком автоматически
# откатимся на 128, ронять генерацию из-за битрейта не будем.
ELEVENLABS_FORMAT=mp3_44100_192

# ── Мастеринг ──
# Свой контейнер matchering (см. п. 5). Пусто — остаётся только выравнивание
# громкости через ffmpeg.
MATCHERING_URL=http://matchering:8360
# Куда целимся по громкости по умолчанию: −14 LUFS / −1 dBTP — как играют
# площадки. Клубную громкость (−9) выбирает пользователь кнопкой.
MASTER_TARGET_LUFS=-14
MASTER_TARGET_TP=-1
# Формат мастера: wav — то, что просит дистрибьютор; mp3 — если важнее вес.
MASTER_OUTPUT=wav

# ── Облачный мастеринг (необязательно) ──
# Ключ: tonn-portal.roexaudio.com → запросить доступ к API. На старте 1000
# бесплатных кредитов ≈ 4–5 мастеров. Пусто — платная кнопка не показывается.
ROEX_API_KEY=
```

`PUBLIC_BASE_URL` и `SECRET_KEY` уже заданы — из них строится подписанная
ссылка на исходник для RoEx.

---

## 5. `infra/docker-compose.yml` — сайдкар мастеринга

**Якорь:** секция `services:`, после блока `rapclips:` (перед `networks:`).

```yaml
  # Мастеринг по эталонному треку. Отдельный контейнер, а не слой в образе
  # API: библиотека под GPL-3.0 (держим её в чужом процессе), тянет
  # numpy/scipy/soxr на сотни мегабайт и просит до 4 ГБ памяти на трек —
  # пусть OOM-killer приходит сюда, а не к веб-процессу.
  matchering:
    build:
      context: ..
      dockerfile: infra/matchering.Dockerfile
    container_name: rapclips-matchering
    restart: unless-stopped
    # Наружу не публикуется вообще: ходит в него только rapclips по имени
    # сервиса внутри общей сети compose.
    expose:
      - "8360"
    mem_limit: 4g
```

Проверка после подъёма:

```bash
cd /opt/rapclips && docker compose up -d --build matchering
docker compose exec rapclips python -c \
  "import mastering; print(mastering.matchering_health())"
# ожидаем {'ok': True, 'mode': 'http', 'version': '2.0.6'}
```

Если сайдкар поднимать негде — можно без него: поставить matchering в
отдельный venv на хосте и указать `MATCHERING_PYTHON=/opt/matchering/bin/python`.
Тот же `backend/matchering_service.py` работает и как CLI.

---

## 6. Фронт — что показать

Файлы фронта не трогаю (их держит другой агент). Что нужно от интерфейса:

**Раздел «Аудио» — четыре блока.**

1. **Voice** — поле текста, выбор голоса (`GET /api/audio/voices`, группировать
   по полу как в матрице), четыре ползунка (stability / similarity / style /
   speed) и цена в очках прямо в кнопке: `ceil(len(text)/100) * 2`, минимум 2.
   Пересчитывать при вводе — человек должен видеть цену до нажатия.
2. **Music** — промпт, длина (30 / 60 / 120 / 180 с), тумблер «без вокала»,
   тумблер «сделать дорожкой этого трека» (`attach`). Цена: 12 очков за
   каждые 30 секунд.
3. **Mastering** — загрузка эталонного трека, выбор цели громкости
   (streaming −14 / club −9), кнопка «Master». Прогресс по `master_status`
   из карточки трека (тот же поллинг, что у сборки клипа). После готовности —
   плеер «до/после» и текст `master_note` целиком: там измеренные цифры.
   Платная кнопка показывается, только если в ответе `/api/audio/status`
   в списке `mastering.engines` элемент с `id === "roex"` имеет `ready: true`
   (список, не словарь — там же лежат `matchering` и `ffmpeg` с их флагами).
4. **Track analysis** — BPM крупно, рядом мелко «or 174» из `bpm_alt`,
   полоска энергии, метки секций и предлагаемые резы.

**Чего в интерфейсе писать НЕЛЬЗЯ** (это не вкусовщина, это то, что не
подтверждается движком):

* «AI mastering» / «нейросетевой мастеринг» — основной движок это классический
  DSP, а не нейросеть;
* «студийное качество», «как у инженера», «исправим плохой микс» — ни один из
  трёх движков этого не делает;
* «сведение» — мы работаем с готовым стерео-миксом, а не с многодорожкой.

Честные формулировки: **«Match a reference track»**, **«Bring it to streaming
loudness»**, **«Cut scenes on the beat»**.

---

## 7. Что проверить после выкладки

```bash
# 1. Сервис поднялся с новыми роутами
curl -s localhost:8930/api/health

# 2. Движки видны (нужна кука сессии)
curl -s -b "qv_session=…" localhost:8930/api/audio/status | jq

# 3. Разбор дорожки на живом треке
curl -s -b "qv_session=…" localhost:8930/api/tracks/1/analysis | jq '.bpm,.confidence,(.cuts|length)'

# 4. Мастеринг без эталона (ffmpeg, ничего не стоит)
curl -s -b "qv_session=…" -F engine=ffmpeg -F target=streaming \
     localhost:8930/api/tracks/1/master

# 5. Через минуту — отчёт
curl -s -b "qv_session=…" localhost:8930/api/tracks/1 | jq '.master_status,.master_note'
```

Ожидаемое в п. 5: `"done"` и текст вида
`Loudness -18.2 → -14.0 LUFS · true peak -0.3 → -1.0 dBTP · dynamic range 8.4 → 7.9 LU.`
