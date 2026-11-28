# TaskFlow on Fly.io (or any Docker host). The data lives in PostgreSQL: set the DATABASE_URL secret (Neon).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
# Gunicorn (production server), not the Flask development server.
# Version 4: 2 workers with threads. Every open board keeps one WebSocket; a change made in one worker reaches the boards
# of the other worker at once through PostgreSQL LISTEN / NOTIFY (livebus.py), so no Redis is needed.
CMD gunicorn "app:create_app()" --bind 0.0.0.0:${PORT} --workers 2 --worker-class gthread --threads 16 --timeout 120 --graceful-timeout 20 --access-logfile -
