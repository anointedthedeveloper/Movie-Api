FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Install Chromium deps as root before switching user
RUN pip install playwright && playwright install-deps chromium

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

COPY --chown=user . .

CMD ["gunicorn", "api:app", "--bind", "0.0.0.0:7860", "--timeout", "300", "--workers", "1", "--threads", "4"]
