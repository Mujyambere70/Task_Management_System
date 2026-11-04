# TaskFlow on Fly.io (or any Docker host). The data lives in PostgreSQL: set the DATABASE_URL secret (Neon).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
# Gunicorn (production server), not the Flask development server.
# 1 worker with threads: every open board keeps one WebSocket, and the reminder job runs in one place.
CMD gunicorn "app:create_app()" --bind 0.0.0.0:${PORT} --workers 1 --worker-class gthread --threads 24 --timeout 120 --graceful-timeout 20 --access-logfile -
