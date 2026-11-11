FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
# One worker only: the 8 PM scheduler runs inside the web process.
# Uses $PORT when the host provides one (Railway, Render), else 8000 (Fly).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 \
    --proxy-headers --forwarded-allow-ips "*"
