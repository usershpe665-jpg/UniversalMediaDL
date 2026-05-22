FROM python:3.11-slim

# System dependencies including ffmpeg for audio/video processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies from pyproject.toml
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy app source
COPY app/ ./app/
COPY static/ ./static/

# Create persistent dirs
RUN mkdir -p /app/downloads /app/state

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
