# Сайдкар мастеринга по эталону (Matchering 2.0).
#
# Отдельный образ, а не слой в backend/Dockerfile:
#   * matchering под GPL-3.0 — держим её в чужом процессе, не в нашем коде;
#   * numpy/scipy/soxr/statsmodels весят сотни мегабайт и веб-процессу не нужны;
#   * на трек библиотека просит до 4 ГБ RAM — пусть падает она, а не API.
#
# Сборка (из корня репозитория):
#   docker build -f infra/matchering.Dockerfile -t rapclips-matchering .
FROM python:3.12-slim

# libsndfile — matchering читает и пишет через soundfile.
# ffmpeg НЕ ставим: на вход сюда приходит уже готовый WAV (конвертирует
# основной бэкенд), а лишние 200 МБ образа ни к чему.
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Пакет на PyPI (2.0.6) не обновлялся с 2022 года, хотя репозиторий живой,
# поэтому ставим из git по ФИКСИРОВАННОМУ тегу: «последний main» однажды
# поедет и сломает мастеринг посреди рабочего дня.
ARG MATCHERING_REF=2.0.6
RUN pip install --no-cache-dir \
        "matchering @ git+https://github.com/sergree/matchering@${MATCHERING_REF}" \
        fastapi==0.115.0 "uvicorn[standard]==0.30.6" python-multipart==0.0.9

COPY backend/matchering_service.py /app/matchering_service.py

EXPOSE 8360
CMD ["uvicorn", "matchering_service:app", "--host", "0.0.0.0", "--port", "8360"]
