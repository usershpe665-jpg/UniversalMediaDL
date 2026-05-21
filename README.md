# LunarMediaDL — Merged Edition

**UI:** UniversalMediaDL (space theme, glassmorphism, star animations)
**Engine:** metube-style yt-dlp Python API (proper download queue, multiprocessing, real progress hooks)

## Halaman yang tersedia

| URL | Halaman |
|-----|---------|
| `/` | Landing page (platform selector) |
| `/downloader.html` | YouTube Downloader |
| `/universal.html` | Universal Downloader (1000+ platform) |
| `/tiktok.html` | TikTok Downloader |
| `/instagram.html` | Instagram Downloader |

## Environment Variables (Railway / .env)

| Variable | Keterangan |
|----------|------------|
| `PORT` | Port server (default: 5000) |
| `YOUTUBE_COOKIES` | **Cookies plain text** format Netscape (tidak perlu base64) |
| `DEBUG` | `true` untuk mode debug (default: false) |
| `WORKDIR` | Override path direktori HTML jika perlu |

### Cara set YOUTUBE_COOKIES

1. Export cookies dari browser (gunakan ekstensi "Get cookies.txt LOCALLY")
2. Copy seluruh isi file `.txt` tersebut
3. Paste langsung ke Railway variable `YOUTUBE_COOKIES` — **tidak perlu encode base64**

## Perubahan dari versi asli

### Dari UniversalMediaDL
- ✅ Seluruh UI/CSS/HTML dipertahankan
- ✅ Semua halaman (YouTube, TikTok, Instagram, Universal) dipertahankan
- ✅ History system dipertahankan
- ✅ Star animation, nebula, glassmorphism dipertahankan
- ❌ **Proxy dihapus sepenuhnya** (frontend & backend)
- ❌ **YOUTUBE_COOKIES_B64** diganti → `YOUTUBE_COOKIES` (plain text langsung)
- ❌ Backend Flask subprocess diganti dengan yt-dlp Python API

### Dari metube
- ✅ yt-dlp Python API (bukan subprocess) — lebih reliable, progress hooks native
- ✅ Multiprocessing download worker (seperti metube)
- ✅ Proper progress queue dengan status real-time
- ✅ Postprocessor hooks (thumbnail embed, metadata, subtitle embed)
- ✅ Format selection logic dari metube

## Deploy ke Railway

```bash
# 1. Push ke GitHub
git add .
git commit -m "LunarMediaDL merged"
git push

# 2. Connect ke Railway
# 3. Set environment variable:
YOUTUBE_COOKIES = <isi cookies.txt plain text>
```

## Run lokal

```bash
pip install -r requirements.txt
pip install -U yt-dlp
python server.py
```

Buka http://localhost:5000
