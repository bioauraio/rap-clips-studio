# Собирается из корня репозитория (context: .. в docker-compose.yml),
# чтобы одним образом упаковать бэкенд и статику фронтенда.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY frontend/ /app/static/

ENV DB_PATH=/data/rapclips.db
ENV UPLOAD_DIR=/data/uploads
ENV FRONTEND_DIR=/app/static

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
