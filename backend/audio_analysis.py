"""Разбор дорожки: темп, сетка долей, границы секций, энергия по времени.

ЗАЧЕМ ЭТО ВООБЩЕ. Сейчас сцены режутся «на глаз»: Claude получает текст,
длительность и словесный профиль громкости и сам придумывает, что кадр
длится 6 секунд. Из-за этого монтаж почти никогда не попадает в долю —
склейка приходится на середину такта, и клип выглядит любительским, даже
когда каждый кадр хорош. Ниже — измерение вместо угадывания: реальный BPM,
реальные доли, реальные границы куплета и припева.

БЕЗ НЕЙРОСЕТЕЙ. Спектральный флакс + автокорреляция + гребёнка фаз. Это
классический MIR-набор, он детерминирован (один и тот же трек всегда даёт
один и тот же ответ), считается на CPU за пару секунд и ничего не стоит.

ПРО librosa. Она умеет то же самое и чуть точнее, но тянет numba, llvmlite,
scikit-learn, joblib, soxr, pooch — это +400…500 МБ к образу, который сейчас
весит около 400 МБ. Ради одной функции удваивать образ не стоит, поэтому
здесь всё считается на голом numpy (+18 МБ). Если librosa всё-таки окажется
в образе, темп и доли возьмутся у неё: см. _HAVE_LIBROSA — переключение
автоматическое, наш алгоритм остаётся запасным.

ЧЕСТНЫЕ ОГРАНИЧЕНИЯ (их надо знать до того, как показывать цифры клиенту):
  * темп считается ПОСТОЯННЫМ на весь трек. Для рэпа, трэпа, хауса и
    драм-н-бэйса это правда; для живой записи с человеческим драммером и
    для трека со сменой темпа — нет, сетка поедет к концу;
  * половинный и двойной темп для машины почти неразличимы (87 против 174).
    Октаву выбирает приор на человеческий диапазон, а вторая версия честно
    едет рядом в поле bpm_alt — «починить» это нельзя, можно только не врать.
    На сетку резов выбор почти не влияет: начала тактов совпадают;
  * «границы секций» — это места, где заметно меняется спектр и энергия.
    Обычно они совпадают с началом припева или врывом бита, но это не
    разметка формы песни, и называть их «куплет/припев» мы не будем.
"""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("rapclips.analysis")

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

# Частота дискретизации разбора. 22050 достаточно: вся ритмическая информация
# лежит сильно ниже 11 кГц, а данных вдвое меньше, чем на 44100.
SR = 22050
N_FFT = 1024
HOP = 512                      # 43.07 кадра в секунду — шаг сетки ~23 мс
FPS = SR / HOP

MIN_BPM = 55.0
MAX_BPM = 200.0
# Куда «тянуть» темп при выборе между 87 и 174: логнормальный приор вокруг
# 110 BPM. Так драм-н-бэйс не превращается в 87, а трэп — в 140.
BPM_PRIOR_CENTER = 110.0
BPM_PRIOR_WIDTH = 1.0          # в октавах темпа

try:  # numpy — единственная тяжёлая зависимость раздела
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    np = None
    _HAVE_NUMPY = False

try:  # необязательный ускоритель точности
    if os.environ.get("ANALYSIS_USE_LIBROSA", "1") != "0":
        import librosa  # noqa: F401

        _HAVE_LIBROSA = True
    else:
        _HAVE_LIBROSA = False
except ImportError:
    _HAVE_LIBROSA = False


class AnalysisError(RuntimeError):
    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def available() -> bool:
    return _HAVE_NUMPY


# ───────────────────────────── чтение звука ─────────────────────────────

def _decode(path: str, sr: int = SR):
    """Декодируем ffmpeg'ом в сырой моно float32 и читаем из stdout.

    Через ffmpeg, а не через звуковую библиотеку: он уже есть в образе,
    открывает вообще всё (mp3, wav, m4a, ogg, дорожку из mp4) и не требует
    ни libsndfile, ни audioread."""
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"],
        capture_output=True, timeout=900,
    )
    if r.returncode != 0 or len(r.stdout) < sr * 4:
        raise AnalysisError(
            f"could not read the audio: {r.stderr.decode()[-200:] or 'file too short'}",
            "input")
    y = np.frombuffer(r.stdout, dtype=np.float32).copy()
    # Постоянная составляющая ломает и RMS, и флакс — снимаем сразу.
    y -= float(y.mean())
    peak = float(np.max(np.abs(y)) or 1.0)
    if peak > 0:
        y /= peak
    return y


# ─────────────────────── спектр и функция начал ───────────────────────

def _spectrogram(y):
    """Магнитудный спектр окнами по N_FFT с шагом HOP.

    sliding_window_view даёт представление без копирования — на трёх минутах
    это разница между 26 МБ и парой сотен."""
    if len(y) < N_FFT:
        raise AnalysisError("the track is too short to analyse", "input")
    frames = np.lib.stride_tricks.sliding_window_view(y, N_FFT)[::HOP]
    win = np.hanning(N_FFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float32)
    return spec


def _bands(spec, n_bands: int = 48):
    """Сводим 513 бинов в 48 логарифмических полос.

    Ухо слышит логарифмически, и удар бочки размазан по десяткам бинов:
    в полосах он виден как один скачок, а не как шум по всему спектру."""
    edges = np.unique(np.geomspace(1, spec.shape[1] - 1, n_bands + 1).astype(int))
    out = np.empty((spec.shape[0], len(edges) - 1), dtype=np.float32)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
        out[:, i] = spec[:, lo:hi].mean(axis=1)
    # В дБ: иначе громкие места забивают тихие и «врыв» после тишины
    # оказывается единственным событием на весь трек.
    return 20.0 * np.log10(out + 1e-6)


def _onset_envelope(bands):
    """Спектральный флакс: сумма ПОЛОЖИТЕЛЬНЫХ приращений по полосам.

    Отрицательные отбрасываем намеренно — затухание звука это не событие,
    события создаёт только появление энергии."""
    diff = np.diff(bands, axis=0)
    flux = np.maximum(diff, 0.0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # Вычитаем скользящее среднее: убирает медленный дрейф громкости, из-за
    # которого громкая часть трека выглядела бы «сплошным ударом».
    w = int(FPS)  # окно в одну секунду
    if w > 2 and len(flux) > w:
        kernel = np.ones(w, dtype=np.float32) / w
        base = np.convolve(flux, kernel, mode="same")
        flux = np.maximum(flux - base, 0.0)
    m = float(flux.max() or 1.0)
    return (flux / m).astype(np.float32)


# ──────────────────────────────── темп ────────────────────────────────

def _tempo_period(onset) -> float:
    """Грубый период доли (в кадрах) по автокорреляции функции начал.

    Целочисленный лаг — слишком грубо: на 70 BPM соседние лаги отличаются на
    два BPM, и за полторы минуты сетка уезжает почти на полдоли. Поэтому по
    трём точкам вокруг пика достраиваем параболу и берём её вершину."""
    x = onset - onset.mean()
    n = len(x)
    size = 1
    while size < 2 * n:
        size *= 2
    f = np.fft.rfft(x, size)
    ac = np.fft.irfft(f * np.conj(f), size)[:n]
    ac /= (ac[0] + 1e-9)

    lag_min = max(2, int(round(FPS * 60.0 / MAX_BPM)))
    lag_max = min(n - 1, int(round(FPS * 60.0 / MIN_BPM)))
    if lag_max <= lag_min:
        raise AnalysisError("the track is too short to detect tempo", "input")
    lags = np.arange(lag_min, lag_max + 1)
    bpms = 60.0 * FPS / lags
    prior = np.exp(-0.5 * (np.log2(bpms / BPM_PRIOR_CENTER) / BPM_PRIOR_WIDTH) ** 2)
    score = ac[lag_min:lag_max + 1] * prior
    k = int(np.argmax(score))
    lag = float(lags[k])
    if 0 < k < len(score) - 1:
        a, b, c = float(score[k - 1]), float(score[k]), float(score[k + 1])
        denom = a - 2 * b + c
        if denom != 0:
            lag += 0.5 * (a - c) / denom
    return lag


def _comb_score(onset, period_frames: float, offset: float) -> float:
    """Средняя энергия начал, попадающая на доли при такой сетке.

    Среднее, а не сумма: сумма всегда растёт с числом долей и поэтому
    механически голосует за вдвое более быстрый темп."""
    n = len(onset)
    count = int((n - 1 - offset) / period_frames)
    if count < 4:
        return -1.0
    idx = np.round(offset + np.arange(count) * period_frames).astype(int)
    idx = idx[(idx >= 0) & (idx < n)]
    if idx.size == 0:
        return -1.0
    return float(onset[idx].mean())


def _best_offset(onset, period_frames: float):
    """Фаза сетки: где стоит первая доля."""
    best_off, best_score = 0.0, -1.0
    step = max(0.25, period_frames / 64.0)
    off = 0.0
    while off < period_frames:
        s = _comb_score(onset, period_frames, off)
        if s > best_score:
            best_off, best_score = off, s
        off += step
    return best_off, best_score


def _refine_period(onset, period0: float, span: float = 0.03, steps: int = 121):
    """Точный период: перебор коридора ±3 % гребёнкой.

    Гребёнка чувствительна именно к накоплению ошибки — при чуть неверном
    периоде дальние доли перестают попадать в удары, и счёт падает. Это и
    делает её хорошим уточнителем там, где автокорреляция уже расплывается.
    121 шаг на ±3 % — это 0.05 % по периоду, то есть уход сетки меньше
    полукадра (~20 мс) на трёхминутном треке."""
    best = (period0, -1.0, 0.0)
    for p in np.linspace(period0 * (1 - span), period0 * (1 + span), steps):
        off, sc = _best_offset(onset, float(p))
        if sc > best[1]:
            best = (float(p), sc, off)
    return best[0], best[2]


def _octave_alt(bpm: float) -> float:
    """Вторая правдоподобная подпись темпа — вдвое быстрее или вдвое медленнее.

    Половинный и двойной темп неразличимы алгоритмически, и «починить» это
    нельзя: 87 и 174 в драм-н-бэйсе различает только человек, а хай-хэты на
    восьмых есть в каждом втором рэп-бите и уводят любой автомат вдвое.
    Поэтому мы НЕ гадаем, а отдаём вторую версию рядом — пусть выбирает
    человек, если ему важна именно подпись.

    На сетку резов выбор почти не влияет: такт остаётся тактом, начала
    тактов совпадают, меняется только число на экране."""
    if bpm <= 0:
        return 0.0
    if bpm * 2 <= MAX_BPM and bpm < BPM_PRIOR_CENTER:
        return round(bpm * 2, 1)
    if bpm / 2 >= MIN_BPM:
        return round(bpm / 2, 1)
    return 0.0


def _grid_confidence(onset, bpm: float) -> float:
    """Насколько сетка вообще похожа на правду.

    Сравниваем НАЙДЕННУЮ фазу не со средним по треку, а с типичной фазой:
    насколько пик гребёнки выше медианы по всем возможным сдвигам. Сравнение
    со средним обманывает само себя — фазу мы и выбирали по максимуму, так
    что на любом шуме она окажется «выше среднего».

    Нужно не для красоты. На а капелле, на речи и на эмбиенте выраженной доли
    нет вообще, и любой трекер всё равно выдаст какое-то число — по этой
    цифре видно, что верить ему не надо, и интерфейс может честно сказать
    «темп не определился, режу по времени»."""
    if bpm <= 0:
        return 0.0
    period = FPS * 60.0 / bpm
    step = max(0.25, period / 64.0)
    scores = []
    off = 0.0
    while off < period:
        s = _comb_score(onset, period, off)
        if s >= 0:
            scores.append(s)
        off += step
    if len(scores) < 8:
        return 0.0
    arr = np.asarray(scores)
    med = float(np.median(arr)) + 1e-9
    ratio = float(arr.max()) / med
    # ×1 — фаза не выделяется вообще; ×3 и выше — уверенная доля.
    return round(min(1.0, max(0.0, (ratio - 1.0) / 2.0)), 2)


def _beat_times(onset, bpm: float, offset_frames: float, duration: float):
    period = FPS * 60.0 / bpm
    n = int((len(onset) - 1 - offset_frames) / period) + 1
    times = (offset_frames + np.arange(max(0, n)) * period) / FPS
    return times[times <= duration]


def _downbeat_phase(onset, beats, beats_per_bar: int = 4) -> int:
    """Какая из четырёх долей — первая в такте.

    Считаем по энергии начал: на «раз» почти всегда стоит бочка, и суммарно
    эта фаза набирает больше. Размер такта берём 4/4 — для рэпа, трэпа,
    хауса и драм-н-бэйса это верно практически всегда."""
    if len(beats) < beats_per_bar * 2:
        return 0
    idx = np.clip(np.round(np.asarray(beats) * FPS).astype(int), 0, len(onset) - 1)
    best_phase, best = 0, -1.0
    for phase in range(beats_per_bar):
        sel = idx[phase::beats_per_bar]
        if sel.size == 0:
            continue
        s = float(onset[sel].mean())
        if s > best:
            best_phase, best = phase, s
    return best_phase


# ────────────────────────── энергия и секции ──────────────────────────

def _energy_curve(y, step_sec: float = 1.0):
    """RMS по секундам в дБFS. Ровно то же, что делает нынешний профиль
    звука в main.py, только одним проходом вместо десяти вызовов ffmpeg."""
    win = int(SR * step_sec)
    if win <= 0 or len(y) < win:
        return []
    n = len(y) // win
    blocks = y[:n * win].reshape(n, win)
    rms = np.sqrt((blocks.astype(np.float64) ** 2).mean(axis=1)) + 1e-9
    db = 20.0 * np.log10(rms)
    return [round(float(v), 1) for v in db]


def _sections(bands, beats, bar_sec: float, duration: float):
    """Границы секций: где спектр заметно «переключается».

    Считаем усреднённый спектр по четырёхтактным окнам и сравниваем соседние
    окна косинусным расстоянием. Пики расстояния — это врыв бита, уход в
    брейк, вход припева. Названий секций не выдумываем: помечаем уровнем
    энергии, потому что это измеряется, а «куплет/припев» — угадывается."""
    if len(beats) < 16 or bar_sec <= 0:
        return []
    per_bar = max(1, int(round(bar_sec * FPS)))
    n_bars = bands.shape[0] // per_bar
    win = 4  # сравниваем четыре такта «до» с четырьмя «после»
    if n_bars < win * 2 + 2:
        return []
    feats = bands[:n_bars * per_bar].reshape(n_bars, per_bar, bands.shape[1]).mean(axis=1)
    feats = feats - feats.min()

    def unit(v):
        return v / (np.linalg.norm(v) + 1e-9)

    # Окна СКОЛЬЗЯЩИЕ, шаг — один такт. Непересекающиеся блоки по четыре
    # такта дали бы разрешение в 14 секунд, и врыв припева находился бы на
    # полкуплета раньше, чем он есть.
    novelty = np.zeros(n_bars, dtype=np.float32)
    for i in range(win, n_bars - win):
        a = unit(feats[i - win:i].mean(axis=0))
        b = unit(feats[i:i + win].mean(axis=0))
        novelty[i] = 1.0 - float(a.dot(b))
    valid = novelty[win:n_bars - win]
    if valid.size < 3:
        return []
    # Если спектр вообще не меняется (тишина, ровный гул, тон), разброс
    # новизны нулевой — и тогда порог «среднее плюс сигма» пропускает КАЖДЫЙ
    # такт, потому что все они одинаково равны порогу. Секций тут нет.
    if float(valid.std()) < 1e-6:
        return []
    thr = float(valid.mean() + 1.0 * valid.std())
    bounds = [0.0]
    min_gap = max(8.0, bar_sec * 8)  # не чаще, чем раз в 8 тактов
    for i in range(win, n_bars - win):
        if novelty[i] < thr:
            continue
        if novelty[i] < novelty[i - 1] or novelty[i] < novelty[i + 1]:
            continue
        t = i * per_bar / FPS
        if t - bounds[-1] >= min_gap and duration - t >= min_gap:
            bounds.append(round(float(t), 2))
    # Границы прижимаем к ближайшей доле: секция, начинающаяся на 0.14 с
    # позже такта, в монтаже читается как ошибка. Нулевую границу не трогаем —
    # трек начинается там, где начинается, а не на первой найденной доле.
    beats_arr = np.asarray(beats)
    snapped = [0.0]
    for t in bounds[1:]:
        if beats_arr.size:
            t = float(beats_arr[int(np.argmin(np.abs(beats_arr - t)))])
        snapped.append(round(t, 2))
    return sorted(set(snapped))


def _energy_label(db: float, lo: float, hi: float) -> str:
    span = max(hi - lo, 1.0)
    rel = (db - lo) / span
    if rel < 0.25:
        return "quiet"
    if rel < 0.5:
        return "steady"
    if rel < 0.75:
        return "full"
    return "peak"


# ─────────────────────────────── публичное ───────────────────────────────

def analyze(path: str) -> dict:
    """Полный разбор файла. Считается за секунды, ключей и сети не требует.

    Возвращает:
      bpm, bpm_confidence, bar_sec, beat_sec,
      beats[], downbeats[], sections[{start,label}], energy[] (дБ по секундам),
      duration_sec, engine.
    """
    if not _HAVE_NUMPY:
        raise AnalysisError(
            "Track analysis is not available: numpy is not installed in this image "
            "(add `numpy` to backend/requirements.txt and rebuild).", "disabled")
    if not os.path.exists(path):
        raise AnalysisError("file not found", "input")

    y = _decode(path)
    duration = round(len(y) / SR, 2)
    spec = _spectrogram(y)
    bands = _bands(spec)
    onset = _onset_envelope(bands)

    engine = "numpy-flux"
    if _HAVE_LIBROSA:
        try:
            bpm, beats = _librosa_beats(path)
            engine = "librosa"
        except Exception as e:  # noqa: BLE001
            log.warning("librosa не справилась, считаю сам: %s", e)
            bpm, beats = _own_beats(onset, duration)
    else:
        bpm, beats = _own_beats(onset, duration)

    beat_sec = 60.0 / bpm if bpm else 0.0
    bar_sec = beat_sec * 4
    phase = _downbeat_phase(onset, beats)
    downbeats = [round(float(t), 3) for t in beats[phase::4]]

    energy = _energy_curve(y)
    lo = min(energy) if energy else -60.0
    hi = max(energy) if energy else 0.0
    bounds = _sections(bands, beats, bar_sec, duration)
    sections = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else duration
        seg = energy[int(start):max(int(start) + 1, int(end))] or [lo]
        sections.append({
            "start": round(float(start), 2),
            "end": round(float(end), 2),
            "label": _energy_label(sum(seg) / len(seg), lo, hi),
        })

    return {
        "duration_sec": duration,
        "bpm": round(float(bpm), 1),
        # Вторая правдоподобная подпись темпа: см. _octave_alt. Показывать её
        # честнее, чем делать вид, что 87 и 174 различимы автоматом.
        "bpm_alt": _octave_alt(float(bpm)),
        "bpm_confidence": _grid_confidence(onset, float(bpm)),
        "beat_sec": round(beat_sec, 4),
        "bar_sec": round(bar_sec, 4),
        "beats": [round(float(t), 3) for t in beats],
        "downbeats": downbeats,
        "sections": sections,
        "energy": energy,
        "engine": engine,
    }


def _own_beats(onset, duration: float):
    """Грубый период по автокорреляции → точный по гребёнке → доли.

    Октаву темпа (×2 / ÷2) выбирает автокорреляция с приором на человеческий
    диапазон — гребёнке эту работу не отдаём: на любом бите с хай-хэтами она
    уверенно голосует за вдвое более быстрый темп."""
    period = _tempo_period(onset)
    period, offset = _refine_period(onset, period)
    # Уточнение ходит на ±3 % и может вынести темп за объявленные границы —
    # на тишине оно так и делает, потому что все варианты равны нулю. Наружу
    # число вне [55, 200] выпускать нельзя: это сразу читается как поломка.
    bpm = min(MAX_BPM, max(MIN_BPM, 60.0 * FPS / period))
    beats = _beat_times(onset, bpm, offset, duration)
    return bpm, beats


def _librosa_beats(path: str):
    """Если librosa всё-таки в образе — темп и доли берём у неё: она
    отслеживает плавающий темп, а наша сетка считает его постоянным."""
    y, sr = librosa.load(path, sr=SR, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr)
    bpm = float(tempo if not hasattr(tempo, "__len__") else tempo[0])
    if bpm < MIN_BPM or bpm > MAX_BPM or len(beats) < 8:
        raise ValueError("librosa вернула неправдоподобный темп")
    return bpm, beats


# ───────────────────── точки реза сцен, привязанные к долям ─────────────────────

def suggest_cuts(analysis: dict, target_sec: float = 6.0,
                 min_sec: float = 2.0, max_sec: float = 10.0) -> list:
    """Где резать сцены, чтобы склейка попадала в музыку.

    Правила, по убыванию важности:
      1. рез в границе секции — там музыка сама меняется, и склейка там
         читается как режиссёрское решение, а не как обрыв;
      2. иначе — в начале такта (сильная доля);
      3. иначе — в ближайшей доле;
      4. и только если сетки нет вообще — ровно через target_sec.

    Длины держим в [min_sec, max_sec] — тех же границах, что понимает
    раскадровщик (claude.py просит duration_sec 2–10).

    Возвращает [{"start", "duration", "bars", "on_section"}] — ровно то, из
    чего строятся сцены трека.
    """
    duration = float(analysis.get("duration_sec") or 0)
    if duration <= 0:
        return []
    bar_sec = float(analysis.get("bar_sec") or 0)
    sect = [float(s["start"]) for s in (analysis.get("sections") or []) if s.get("start")]
    # Слабая сетка (речь, а капелла, эмбиент) — привязываться не к чему.
    # Лучше честно резать по времени, чем ставить склейки по выдуманным
    # долям и объяснять потом, почему монтаж «дёргается мимо музыки».
    if float(analysis.get("bpm_confidence") or 0) < 0.2:
        downbeats, beats = [], []
    else:
        downbeats = [float(t) for t in (analysis.get("downbeats") or [])]
        beats = [float(t) for t in (analysis.get("beats") or [])]

    def nearest(points: list, want: float, lo: float, hi: float):
        cands = [p for p in points if lo <= p <= hi]
        if not cands:
            return None
        return min(cands, key=lambda p: abs(p - want))

    cuts: list = []
    cur = 0.0
    guard = 0
    while cur < duration - min_sec and guard < 1000:
        guard += 1
        want, lo, hi = cur + target_sec, cur + min_sec, min(cur + max_sec, duration)
        nxt = nearest(sect, want, lo, hi)
        on_section = nxt is not None
        if nxt is None:
            nxt = nearest(downbeats, want, lo, hi)
        if nxt is None:
            nxt = nearest(beats, want, lo, hi)
        if nxt is None:
            nxt = min(want, duration)
        # Хвост короче минимума не оставляем: он всё равно не превратится
        # в сцену, а «огрызок» в конце клипа заметен сильнее всего.
        if duration - nxt < min_sec:
            nxt = duration
        cuts.append({
            "start": round(cur, 2),
            "duration": round(nxt - cur, 2),
            "bars": round((nxt - cur) / bar_sec, 2) if bar_sec else 0,
            "on_section": bool(on_section),
        })
        cur = nxt
    if not cuts:
        # Трек короче минимальной сцены: одна сцена на всё, а не пустой ответ —
        # иначе вызывающий код решит, что разбор не удался.
        cuts.append({"start": 0.0, "duration": round(duration, 2),
                     "bars": round(duration / bar_sec, 2) if bar_sec else 0,
                     "on_section": False})
    return cuts


def profile_text(analysis: dict) -> str:
    """Текстовый профиль трека для промптов Claude.

    Формат намеренно такой же, как у нынешнего `_audio_profile` в main.py:
    его читает генератор сюжета и раскадровки, и он ожидает русский текст
    вида «длительность …; динамика …». Здесь та же строка, но с измеренным
    темпом и реальными границами секций вместо десяти замеров громкости."""
    dur = int(analysis.get("duration_sec") or 0)
    bpm = analysis.get("bpm") or 0
    parts = [f"длительность {dur // 60}:{dur % 60:02d}",
             f"темп {bpm:g} BPM (такт {analysis.get('bar_sec', 0):.2f} с)"]
    sections = analysis.get("sections") or []
    if sections:
        human = {"quiet": "тихо", "steady": "спокойно", "full": "плотно", "peak": "врыв"}
        marks = []
        for s in sections:
            t = int(s["start"])
            marks.append(f"{t // 60}:{t % 60:02d} {human.get(s['label'], s['label'])}")
        parts.append("секции: " + ", ".join(marks))
    parts.append("склейки ставь по началам тактов, длину кадра меряй тактами, "
                 "а не секундами")
    return "; ".join(parts)
