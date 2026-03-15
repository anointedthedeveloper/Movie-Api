FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

COPY . .

CMD ["gunicorn", "api:app", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "1", "--threads", "4"]
