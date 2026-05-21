# LunarMediaDL

> Universal media downloader with the **LunarMediaDL UI** and the battle-tested **MeTube / yt-dlp backend**.
> 1000+ platforms · Real-time progress · No proxy · Cookie auth via env var

---

## Features

- 🌐 **1000+ platforms** — YouTube, TikTok, Instagram, Twitter/X, Facebook, Vimeo, Reddit, Twitch, SoundCloud, and many more
- ⚡ **Real-time progress** — WebSocket-powered live progress, speed, and ETA
- 🎬 **Video** — up to 8K, choice of codec (H.264 / H.265 / AV1 / VP9) and container (any / MP4 / iOS)
- 🎵 **Audio** — extract as MP3, M4A, FLAC, WAV, or Opus
- 📋 **Queue & history** — multiple concurrent downloads with persistent history
- 🍪 **Cookie auth** — set via `COOKIES` env var in plain Netscape format — no base64 needed
- 🔒 **No proxy** — direct downloads, no intermediaries

---

## Quick Start (Docker)

```bash
# Build
docker build -t lunarmediadl .

# Run
docker run -d \
  -p 8081:8081 \
  -v "$(pwd)/downloads:/downloads" \
  -e DOWNLOAD_DIR=/downloads \
  lunarmediadl
```

Open `http://localhost:8081` in your browser.

---

## Deploy on Railway

1. Push this repo to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add a **Volume** mounted at `/downloads`
4. Set environment variables in the **Variables** panel (see `.env.example`)
5. Railway will auto-detect the `Dockerfile` and build

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DOWNLOAD_DIR` | `/downloads` | Where finished downloads are saved |
| `AUDIO_DOWNLOAD_DIR` | `%%DOWNLOAD_DIR` | Separate directory for audio (optional) |
| `TEMP_DIR` | `%%DOWNLOAD_DIR` | Temp working directory |
| `STATE_DIR` | `.` | Queue state persistence directory |
| `PORT` | `8081` | HTTP port to listen on |
| `HOST` | `0.0.0.0` | Interface to bind |
| `COOKIES` | *(empty)* | Netscape-format cookie file content (plain text, no base64) |
| `YTDL_OPTIONS` | `{}` | Extra yt-dlp options as JSON object |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Max simultaneous downloads |
| `OUTPUT_TEMPLATE` | `%(title)s.%(ext)s` | yt-dlp output filename template |
| `CORS_ALLOWED_ORIGINS` | *(empty)* | Allowed CORS origins (comma-separated) |
| `LOGLEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Setting Cookies (Railway / .env)

Paste your Netscape-format cookie file content directly into the `COOKIES` variable.
No base64 encoding required. Example:

```
COOKIES=# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	SID	value_here
.youtube.com	TRUE	/	TRUE	0	HSID	value_here
```

Multi-line env vars are supported by Railway natively.

---

## API Endpoints (MeTube)

| Method | Path | Description |
|---|---|---|
| `POST` | `/add` | Add a download to the queue |
| `POST` | `/delete` | Cancel or clear a download |
| `GET` | `/history` | Get queue and done list |
| `POST` | `/upload-cookies` | Upload a cookies.txt file via UI |
| `POST` | `/delete-cookies` | Remove uploaded cookies |
| `GET` | `/cookie-status` | Check if cookies are loaded |
| `GET` | `/version` | yt-dlp and app version |

WebSocket events use Socket.IO at `/socket.io`.

---

## Credits

- **UI Design** — Syawaliuz Octavian (LunarMediaDL)
- **Backend** — [MeTube](https://github.com/alexta69/metube) by alexta69
- **Download Engine** — [yt-dlp](https://github.com/yt-dlp/yt-dlp)
