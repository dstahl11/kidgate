FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Data dir for SQLite (mount a volume here so state survives container recreation).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8099

# Single worker: APScheduler must not run in multiple processes (would double-fire timers).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--workers", "1"]
