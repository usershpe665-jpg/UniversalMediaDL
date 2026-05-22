# LunarMediaDL — MeTube Engine Merge

**LunarMediaDL frontend** + **MeTube backend engine** = one seamless media downloader.

## Stack

| Layer     | Technology                          |
|-----------|-------------------------------------|
| Backend   | Python 3.11, aiohttp, MeTube engine |
| Engine    | yt-dlp (1000+ platforms)            |
| Realtime  | Socket.IO (WebSocket + polling)     |
| Frontend  | LunarMediaDL HTML/CSS/JS            |
| Deploy    | Railway (Docker / Nixpacks)         |

---

## API Endpoints

| Method | Endpoint               | Description                    |
|--------|------------------------|--------------------------------|
| GET    | `/`                    | Landing page                   |
| GET    | `/downloader.html`     | YouTube downloader             |
| GET    | `/tiktok.html`         | TikTok downloader              |
| GET    | `/instagram.html`      | Instagram downloader           |
| GET    | `/universal.html`      | Universal downloader           |
| POST   | `/metadata`            | Extract URL metadata           |
| POST   | `/download`            | Start a download               |
| GET    | `/queue`               | Current queue state            |
| POST   | `/cancel`              | Cancel a download              |
| GET    | `/history`             | Download history               |
| GET    | `/download/file/:id`   | Serve completed file           |
| GET    | `/health`              | Health check                   |
| GET    | `/version`             | Version info                   |

### Socket.IO Events

| Event            | Direction       | Description              |
|------------------|-----------------|--------------------------|
| `lunar_progress` | server → client | Real-time progress update|
| `lunar_completed`| server → client | Download finished        |
| `lunar_canceled` | server → client | Download canceled        |
| `lunar_added`    | server → client | Job added to queue       |
| `lunar_state`    | server → client | Full state on connect    |

---

## Environment Variables

| Variable               | Default          | Description                         |
|------------------------|------------------|-------------------------------------|
| `PORT`                 | `8080`           | Listening port                      |
| `DOWNLOAD_DIR`         | `/app/downloads` | Download directory                  |
| `STATE_DIR`            | `/app/state`     | State/queue directory               |
| `MAX_CONCURRENT_DOWNLOADS` | `3`          | Parallel downloads                  |
| `FILE_CLEANUP_HOURS`   | `3`              | Auto-delete files after N hours     |
| `YTDL_COOKIES`         | *(empty)*        | Cookies as plain text (Netscape fmt)|
| `YTDL_OPTIONS`         | `{}`             | Extra yt-dlp options (JSON)         |
| `LOGLEVEL`             | `INFO`           | Logging level                       |

### Cookie Setup for Railway

Set `YTDL_COOKIES` as a Railway environment variable with the raw Netscape cookies.txt content.
No encoding/encryption needed — the app reads it directly and writes to disk at startup.

---

## Deploy to Railway

```bash
# 1. Push to GitHub
git init && git add . && git commit -m "Initial commit"
git remote add origin https://github.com/yourname/lunarmediadl.git
git push -u origin main

# 2. Create Railway project
# → railway.app → New Project → Deploy from GitHub repo

# 3. Set environment variables in Railway dashboard:
#    PORT=8080
#    DOWNLOAD_DIR=/app/downloads
#    STATE_DIR=/app/state
#    YTDL_COOKIES=<your cookies content>  # optional
```

---

## Local Development

```bash
# Install Python 3.11+, ffmpeg
pip install .

# Set dirs
export DOWNLOAD_DIR=./downloads
export STATE_DIR=./state
mkdir -p downloads state

# Run
python app/main.py
# Open http://localhost:8080
```

---

## File Cleanup

Files are automatically deleted **3 hours** after creation to keep server storage stable.
- Cleanup runs every **30 minutes** in the background
- Active downloads are **never** touched during cleanup
- Change the window with `FILE_CLEANUP_HOURS` environment variable

---

## Features

- ✅ Real-time Socket.IO progress (actual bytes/speed from yt-dlp hooks)
- ✅ Accurate ETA and speed (no fake progress)
- ✅ Multi-download queue with concurrency control
- ✅ YouTube, TikTok, Instagram + 1000+ platforms
- ✅ Video/audio format selection
- ✅ Download history (localStorage)
- ✅ Cancel downloads
- ✅ Cookies from Railway environment variables
- ✅ Auto-cleanup of old files (3h, configurable)
- ✅ Railway-ready deployment

---

*Created by Syawaliuz Octavian*
