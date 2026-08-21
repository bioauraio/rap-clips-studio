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
# АДМИНКА ЛЕЖИТ ОТДЕЛЬНО ОТ СТАТИКИ И ЭТО НЕ ПРИДИРКА. Всё, что попадает в
# /app/static, раздаётся StaticFiles с корня и читается кем угодно; проверка
# is_admin на роуте /admin стала бы декорацией, потому что admin.js всё
# равно отдавался бы напрямую. Отсюда файлы отдаёт только роут main.admin_page.
COPY admin/ /app/admin/
# Уроки школы — ФАЙЛЫ, а не строки в базе: правка урока = коммит и деплой,
# версионирование и откат бесплатны. /api/learn читает их отсюда.
COPY docs/learn/ /app/learn/

ENV DB_PATH=/data/rapclips.db
ENV UPLOAD_DIR=/data/uploads
ENV FRONTEND_DIR=/app/static
ENV ADMIN_DIR=/app/admin
ENV LEARN_DIR=/app/learn
# Файлы стилей (постеры, примеры кадров, референсы) — в томе, не в образе:
# их кладёт владелец через админку, и пересборку они обязаны пережить.
ENV STYLE_ASSETS_DIR=/data/styles

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
