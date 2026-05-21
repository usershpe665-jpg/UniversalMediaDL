FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg for media processing + curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      ffmpeg \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application files
COPY app/                 ./app/
COPY ui/                  ./ui/
COPY docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh && \
    mkdir -p /downloads /tmp/lunarmetube

# ── Environment defaults ────────────────────────────────────────
ENV DOWNLOAD_DIR=/downloads \
    AUDIO_DOWNLOAD_DIR=/downloads \
    TEMP_DIR=/tmp/lunarmetube \
    STATE_DIR=/tmp/lunarmetube \
    PORT=8081 \
    HOST=0.0.0.0

VOLUME ["/downloads"]
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
