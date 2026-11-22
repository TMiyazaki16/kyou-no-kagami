# きょうのかがみ バックエンド（FastAPI + SQLite / DB_BACKEND=mysql で Cloud SQL for MySQL）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DB_PATH=/app/data/app.db \
    APP_UPLOAD_DIR=/app/data/uploads

WORKDIR /app

COPY requirements.txt .
# tzdata: slim イメージにはタイムゾーンデータが無いことがあり、JST の日付決定に必要なため明示的に入れる
RUN pip install --no-cache-dir -r requirements.txt tzdata

COPY app/ ./app/
COPY scripts/ ./scripts/

# SQLite とアップロードの置き場所（docker-compose ではボリュームをここにマウントする）
RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
