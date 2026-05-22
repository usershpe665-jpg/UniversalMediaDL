FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────────────────────
# nodejs  → yt-dlp signature/n-challenge solver (YouTube requires JS runtime)
# ffmpeg  → audio/video conversion (mp3, flac, mp4 muxing, thumbnail embedding)
# curl    → yt-dlp network fallback / health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    nodejs \
    npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Verify Node.js is available for yt-dlp signature solving
RUN node --version && npm --version

WORKDIR /app

# ── Python dependencies (from pyproject.toml, no requirements.txt) ────────────
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ── App source ────────────────────────────────────────────────────────────────
COPY app/    ./app/
COPY static/ ./static/

# ── Persistent storage dirs ───────────────────────────────────────────────────
RUN mkdir -p /app/downloads /app/state

# ── Environment defaults (override via Railway env vars) ──────────────────────
ENV DOWNLOAD_DIR=/app/downloads
ENV AUDIO_DOWNLOAD_DIR=/app/downloads
ENV TEMP_DIR=/app/downloads
ENV STATE_DIR=/app/state
ENV PORT=8080
ENV HOST=0.0.0.0
ENV LOGLEVEL=INFO
ENV MAX_CONCURRENT_DOWNLOADS=3
ENV FILE_CLEANUP_HOURS=3

EXPOSE 8080
CMD ["python", "app/main.py"]
