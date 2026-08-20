"""Сайдкар мастеринга по эталону: тонкая обёртка вокруг Matchering 2.0.

ЗАПУСКАЕТСЯ ОТДЕЛЬНЫМ КОНТЕЙНЕРОМ (infra/matchering.Dockerfile), а не внутри
rapclips-api. Причина одна и важная: matchering лежит под GPL-3.0. Для SaaS
это безопасно и без разделения (GPL, а не AGPL — обязательства возникают при
распространении, а мы ничего не распространяем), но отдельный процесс снимает
вопрос целиком: наш код с GPL-библиотекой не линкуется, они общаются по HTTP.

Второй довод, уже практический: matchering тянет numpy/scipy/soxr/statsmodels
и хочет 4 ГБ памяти на трек. В основном образе это лишние сотни мегабайт и
риск словить OOM на веб-процессе.

Что это НЕ такое: не нейросеть. Внутри классический DSP — измеряется АЧХ,
RMS, пиковая амплитуда и ширина стерео эталона, и наш трек подтягивается к
этим значениям, дальше брикволл-лимитер. В интерфейсе так и написано.

Два режима, оба без изменений в коде:
  * сервис:  uvicorn matchering_service:app --host 0.0.0.0 --port 8360
  * CLI:     python matchering_service.py --target t.wav --reference r.wav --out o.wav
    (нужен, когда сайдкар поднимать негде — тогда наш бэкенд зовёт этот файл
     чужим интерпретатором, см. MATCHERING_PYTHON в backend/mastering.py)

Вход — ТОЛЬКО WAV: конвертацию делает вызывающая сторона своим ffmpeg,
здесь ffmpeg может отсутствовать.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile

log = logging.getLogger("matchering-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [matchering] %(message)s")

# Ограничение размера загрузки: три минуты 24-битного стерео WAV — это ~50 МБ,
# десять минут — ~170. Больше просто не музыкальный трек.
MAX_UPLOAD_MB = int(os.environ.get("MATCHERING_MAX_UPLOAD_MB", "400"))


def _version() -> str:
    try:
        import matchering  # noqa: PLC0415

        return getattr(matchering, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return ""


def run_matchering(target_path: str, reference_path: str, out_path: str) -> str:
    """Собственно мастеринг. Исключения наружу не глотаем — вызывающая
    сторона должна увидеть причину, а не «что-то пошло не так»."""
    import matchering as mg  # noqa: PLC0415

    # Логи библиотеки уводим в наш logger: по умолчанию она печатает в stdout
    # и в CLI-режиме перемешивается с нашим выводом.
    mg.log(info_handler=log.info, warning_handler=log.warning)
    mg.process(
        target=target_path,
        reference=reference_path,
        # pcm24 — то, что уходит дистрибьютору. Больше форматов не просим:
        # каждый лишний результат это ещё один проход по всему треку.
        results=[mg.pcm24(out_path)],
    )
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        raise RuntimeError("matchering finished but produced no audio")
    return out_path


# ──────────────────────────── HTTP-режим ────────────────────────────

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.background import BackgroundTask

    app = FastAPI(title="matchering sidecar")

    @app.get("/health")
    def health() -> JSONResponse:
        ver = _version()
        return JSONResponse({"ok": bool(ver), "matchering": ver})

    @app.post("/master")
    async def master(target: UploadFile = File(...), reference: UploadFile = File(...)):
        """target — наш трек, reference — эталон «звучать как это». Оба WAV.

        Работа занимает десятки секунд и держит поток занятым — очередь тут
        не нужна: вызывающий бэкенд и так ходит сюда из одной фоновой задачи
        на трек."""
        work = tempfile.mkdtemp(prefix="mg_")
        try:
            paths = {}
            for key, upload in (("target", target), ("reference", reference)):
                path = os.path.join(work, f"{key}.wav")
                size = 0
                with open(path, "wb") as f:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_UPLOAD_MB * 1024 * 1024:
                            raise HTTPException(413, f"{key} is larger than {MAX_UPLOAD_MB} MB")
                        f.write(chunk)
                if size < 1000:
                    raise HTTPException(400, f"{key} is empty")
                paths[key] = path

            out_path = os.path.join(work, "master.wav")
            try:
                run_matchering(paths["target"], paths["reference"], out_path)
            except Exception as e:  # noqa: BLE001
                log.warning("мастеринг упал: %s", e)
                raise HTTPException(500, str(e)[:300])
            # Каталог убираем ПОСЛЕ отдачи файла, иначе стираем то, что
            # прямо сейчас читает Starlette.
            return FileResponse(
                out_path, media_type="audio/wav", filename="master.wav",
                background=BackgroundTask(shutil.rmtree, work, ignore_errors=True),
            )
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise

except ImportError:  # pragma: no cover
    # FastAPI в CLI-режиме не нужен — файл должен оставаться запускаемым
    # интерпретатором, где стоит только matchering.
    app = None


# ──────────────────────────── CLI-режим ────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Matchering CLI wrapper")
    ap.add_argument("--target", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        run_matchering(args.target, args.reference, args.out)
    except Exception as e:  # noqa: BLE001
        print(f"matchering error: {e}", file=sys.stderr)
        return 1
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
