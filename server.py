#!/usr/bin/env python3
"""
LunarMediaDL - Backend Server
Production-ready Universal Downloader API powered by yt-dlp
Supports YouTube, TikTok, Instagram, and 1000+ other platforms.
"""

import os
import sys
import json
import uuid
import time
import threading
import logging
import re
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

# ─── Configuration ────────────────────────────────────────────────────────────
def _resolve_base_dir() -> Path:
    if wd := os.environ.get("WORKDIR"):
        return Path(wd)
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "index.html").exists():
        return script_dir
    cwd = Path.cwd()
    if (cwd / "index.html").exists():
        return cwd
    return script_dir

BASE_DIR     = _resolve_base_dir()
DOWNLOAD_DIR = BASE_DIR / "downloads"
LOG_DIR      = BASE_DIR / "logs"
STATE_DIR    = BASE_DIR / "state"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

# ─── Cookies ──────────────────────────────────────────────────────────────────
# Cookies bisa disediakan dengan dua cara (prioritas dari atas ke bawah):
#
#   1. Upload via UI  → POST /api/cookies/upload  (disimpan di state/cookies.txt)
#   2. Env var Railway → YOUTUBE_COOKIES
#      Isi variabelnya dengan konten cookies.txt mentah (format Netscape),
#      persis seperti isi file cookies.txt, tanpa encoding apapun.
#
COOKIES_FILE = STATE_DIR / "cookies.txt"

# ─── Proxy ────────────────────────────────────────────────────────────────────
DEFAULT_PROXY = os.environ.get("PROXY_URL", "").strip()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "server.log"),
    ],
)
logger = logging.getLogger("LunarMediaDL")

# ─── App Init ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")

@app.before_request
def _log_base_dir_once():
    if not getattr(app, "_base_dir_logged", False):
        app._base_dir_logged = True
        logger.info(f"📁 BASE_DIR resolved to: {BASE_DIR}")
        logger.info(f"   index.html exists: {(BASE_DIR / 'index.html').exists()}")
        logger.info(f"   downloader.html exists: {(BASE_DIR / 'downloader.html').exists()}")
        logger.info(f"🌐 Default proxy: {DEFAULT_PROXY or 'none (direct connection)'}")
        _auto_load_cookies()

CORS(app, resources={r"/api/*": {"origins": "*"}})

# ─── In-Memory Job Store ──────────────────────────────────────────────────────
jobs      = {}
jobs_lock = threading.Lock()

# Runtime overrides — thread-safe key-value untuk setting aktif (e.g. cookiefile)
_runtime_overrides: dict = {}
_overrides_lock = threading.Lock()

def set_runtime_override(key: str, value):
    with _overrides_lock:
        _runtime_overrides[key] = value

def remove_runtime_override(key: str):
    with _overrides_lock:
        _runtime_overrides.pop(key, None)

def get_runtime_override(key: str):
    with _overrides_lock:
        return _runtime_overrides.get(key)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_valid_url(url: str) -> bool:
    url_stripped = url.strip()
    return url_stripped.startswith("http://") or url_stripped.startswith("https://")

def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:200]

def cleanup_old_files(max_age_hours: int = 4):
    """Remove download files older than max_age_hours."""
    cutoff = time.time() - max_age_hours * 3600
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                logger.info(f"Cleaned up old file: {f.name}")
            except OSError:
                pass

def cleanup_old_jobs(max_age_hours: int = 4):
    """Hapus job lama dari memori untuk mencegah memory leak."""
    cutoff = time.time() - max_age_hours * 3600
    to_remove = []
    with jobs_lock:
        for jid, job in jobs.items():
            if job["status"] in ("completed", "error") and job.get("created_at", 0) < cutoff:
                to_remove.append(jid)
        for jid in to_remove:
            jobs.pop(jid, None)
    if to_remove:
        logger.info(f"Cleaned up {len(to_remove)} old jobs from memory")

def _load_cookies_from_env():
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        return
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        logger.debug("Cookies file already exists (uploaded via UI), skipping env var load")
        return
    try:
        content = raw.encode("utf-8")
        tmp_path = Path(str(COOKIES_FILE) + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(COOKIES_FILE)
        set_runtime_override("cookiefile", str(COOKIES_FILE))
        logger.info(f"Cookies loaded from env var YOUTUBE_COOKIES ({len(content)} bytes)")
    except Exception as e:
        logger.warning(f"Failed to write cookies from YOUTUBE_COOKIES env var: {e}")

def _auto_load_cookies():
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        set_runtime_override("cookiefile", str(COOKIES_FILE))
        logger.info(f"Cookie file detected at {COOKIES_FILE}")
        return
    _load_cookies_from_env()

def get_cookies_args() -> list:
    """Kembalikan argumen --cookies untuk yt-dlp jika cookies tersedia."""
    cookiefile = get_runtime_override("cookiefile")
    if cookiefile and Path(cookiefile).exists() and Path(cookiefile).stat().st_size > 0:
        return ["--cookies", cookiefile]
    return []

def get_proxy_args(user_proxy: str = None) -> list:
    proxy = (user_proxy or "").strip() or DEFAULT_PROXY
    if proxy:
        logger.info(f"🌐 Using proxy: {proxy}")
        return ["--proxy", proxy]
    return []

def run_ytdlp(args: list) -> subprocess.CompletedProcess:
    cmd = ["yt-dlp"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

def _find_latest_file(hint_name: str = None) -> Path | None:
    """Find the most recently modified file in DOWNLOAD_DIR."""
    if hint_name:
        hint_path = Path(hint_name)
        if hint_path.exists() and hint_path.is_file():
            return hint_path
        candidate = DOWNLOAD_DIR / hint_path.name
        if candidate.exists() and candidate.is_file():
            return candidate

    files = sorted(
        [f for f in DOWNLOAD_DIR.glob("*.*") if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None

def _extract_error_message(output: str) -> str:
    """Ekstrak pesan error yang paling relevan dari output yt-dlp."""
    if not output:
        return "Unknown error"
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    for line in reversed(lines):
        if any(kw in line for kw in ("ERROR:", "Error:", "error:", "WARNING:", "Sign in")):
            line = re.sub(r'^(ERROR|WARNING):\s*', '', line)
            return line
    return lines[-1] if lines else "Unknown error"

def _format_bytes(n) -> str:
    """Format bytes ke string yang mudah dibaca (seperti metube)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PiB"

def _format_speed(bps) -> str:
    """Format kecepatan download (bytes/s) ke string yang akurat."""
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return None
    return f"{_format_bytes(bps)}/s"

def _format_eta(seconds) -> str:
    """Format ETA (seconds) ke string yang akurat."""
    try:
        secs = int(seconds)
    except (TypeError, ValueError):
        return None
    if secs < 0:
        return None
    hours, rem = divmod(secs, 3600)
    mins, secs2 = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{mins:02d}:{secs2:02d}"
    return f"{mins:02d}:{secs2:02d}"

# ─── Static Routes ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def serve_index():
    index_path = BASE_DIR / "index.html"
    if not index_path.exists():
        return (
            f"<h2>index.html not found</h2>"
            f"<p>BASE_DIR = <code>{BASE_DIR}</code></p>"
            f"<p>Files in BASE_DIR: {[f.name for f in BASE_DIR.iterdir() if f.is_file()]}</p>"
            f"<p>Set the <code>WORKDIR</code> environment variable to the directory containing your HTML files.</p>",
            200,
            {"Content-Type": "text/html"},
        )
    return send_from_directory(str(BASE_DIR), "index.html")

@app.route("/downloader", methods=["GET"])
@app.route("/downloader.html", methods=["GET"])
def serve_downloader():
    return send_from_directory(str(BASE_DIR), "downloader.html")

@app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    if filename.startswith("api/"):
        from flask import abort
        abort(404)
    return send_from_directory(str(BASE_DIR), filename)

# ─── Health ───────────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health_check():
    try:
        result = run_ytdlp(["--version"])
        ytdlp_version = result.stdout.strip()
    except FileNotFoundError:
        ytdlp_version = "not found"
    except Exception as e:
        ytdlp_version = f"error: {e}"

    cookiefile  = get_runtime_override("cookiefile")
    cookies_ok  = bool(cookiefile and Path(cookiefile).exists() and Path(cookiefile).stat().st_size > 0)
    has_env_var = bool(os.environ.get("YOUTUBE_COOKIES", "").strip())
    has_file    = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

    if has_file and not has_env_var:
        cookies_src = "uploaded_file"
    elif has_env_var:
        cookies_src = "env_var (YOUTUBE_COOKIES)"
    else:
        cookies_src = "none"

    return jsonify({
        "status":         "online",
        "server":         "LunarMediaDL v2.2.0",
        "ytdlp_version":  ytdlp_version,
        "timestamp":      datetime.utcnow().isoformat(),
        "cookies_loaded": cookies_ok,
        "cookies_source": cookies_src,
        "proxy_active":   bool(DEFAULT_PROXY),
        "proxy":          DEFAULT_PROXY or "none (direct connection)",
    })

# ─── Cookies API ──────────────────────────────────────────────────────────────

@app.route("/api/cookies/upload", methods=["POST"])
def upload_cookies():
    MAX_SIZE = 1_000_000  # 1MB

    content = None
    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("cookies")
        if f is None:
            return jsonify({"status": "error", "msg": "No 'cookies' field in multipart form"}), 400
        content = f.read(MAX_SIZE + 1)
    else:
        content = request.get_data()

    if not content:
        return jsonify({"status": "error", "msg": "No cookies data provided"}), 400
    if len(content) > MAX_SIZE:
        return jsonify({"status": "error", "msg": "Cookie file too large (max 1MB)"}), 400

    try:
        text = content.decode("utf-8", errors="replace")
        if not any(line.strip() and not line.startswith("#") for line in text.splitlines()):
            return jsonify({"status": "error", "msg": "File tampak kosong atau tidak valid"}), 400
    except Exception:
        pass

    tmp_path = Path(str(COOKIES_FILE) + ".tmp")
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(COOKIES_FILE)
        set_runtime_override("cookiefile", str(COOKIES_FILE))
        logger.info(f"Cookies file uploaded ({len(content)} bytes)")
        return jsonify({"status": "ok", "msg": f"Cookies uploaded ({len(content)} bytes)"})
    except Exception as e:
        logger.error(f"Failed to save cookies: {e}")
        return jsonify({"status": "error", "msg": f"Failed to save cookies: {e}"}), 500

@app.route("/api/cookies/delete", methods=["DELETE", "POST"])
def delete_cookies():
    has_uploaded = COOKIES_FILE.exists()
    has_env_var  = bool(os.environ.get("YOUTUBE_COOKIES", "").strip())

    if not has_uploaded:
        if has_env_var:
            return jsonify({
                "status": "error",
                "msg": "Cookies aktif berasal dari env var YOUTUBE_COOKIES di Railway. Hapus atau kosongkan env var tersebut untuk menonaktifkan."
            }), 400
        return jsonify({"status": "error", "msg": "Tidak ada cookies yang di-upload"}), 400

    try:
        COOKIES_FILE.unlink()
        remove_runtime_override("cookiefile")
        if has_env_var:
            _load_cookies_from_env()
            return jsonify({"status": "ok", "msg": "Cookies file dihapus. Cookies dari env var YOUTUBE_COOKIES diaktifkan kembali."})
        logger.info("Cookies file deleted")
        return jsonify({"status": "ok", "msg": "Cookies berhasil dihapus"})
    except Exception as e:
        logger.error(f"Failed to delete cookies: {e}")
        return jsonify({"status": "error", "msg": f"Gagal menghapus cookies: {e}"}), 500

@app.route("/api/cookies/status", methods=["GET"])
def cookies_status():
    cookiefile  = get_runtime_override("cookiefile")
    has_cookies = bool(cookiefile and Path(cookiefile).exists() and Path(cookiefile).stat().st_size > 0)
    has_env_var = bool(os.environ.get("YOUTUBE_COOKIES", "").strip())
    has_file    = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0

    if has_file and not has_env_var:
        source = "uploaded_file"
    elif has_file and has_env_var:
        source = "uploaded_file"
    elif has_env_var:
        source = "env_var (YOUTUBE_COOKIES)"
    else:
        source = "none"

    return jsonify({
        "status":      "ok",
        "has_cookies": has_cookies,
        "source":      source,
    })

# ─── Info ─────────────────────────────────────────────────────────────────────

@app.route("/api/info", methods=["POST"])
def fetch_info():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Invalid or unsupported URL"}), 422

    logger.info(f"Fetching info for: {url}")

    user_proxy = (data.get("proxy") or "").strip()

    args = [
        url, "--dump-json",
        "--no-playlist" if not data.get("playlist") else "--yes-playlist",
        "--no-warnings",
        "--no-check-formats",
        "--ignore-no-formats-error",
        "--socket-timeout", "30", "--retries", "3",
        "--extractor-retries", "3",
    ] + get_cookies_args() + get_proxy_args(user_proxy)

    cfb = (data.get("cookies_from_browser") or "").strip()
    if cfb:
        args += ["--cookies-from-browser", cfb]

    try:
        result = run_ytdlp(args)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Request timed out. Coba lagi."}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp tidak terinstall di server"}), 500

    if result.returncode != 0:
        raw_err = (result.stderr or result.stdout or "").strip()
        err_msg = _extract_error_message(raw_err)
        logger.warning(f"yt-dlp info error: {err_msg}")
        return jsonify({"error": f"Gagal mengambil info media: {err_msg}"}), 400

    lines = [l for l in result.stdout.strip().split("\n") if l.startswith("{")]
    if not lines:
        return jsonify({"error": "Tidak ada data media yang dikembalikan"}), 400

    try:
        meta = json.loads(lines[0])
    except json.JSONDecodeError:
        return jsonify({"error": "Gagal mem-parse data media"}), 500

    # Build format list
    formats = []
    seen    = set()
    for f in (meta.get("formats") or []):
        fid    = f.get("format_id", "")
        ext    = f.get("ext", "")
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        width  = f.get("width")
        tbr    = f.get("tbr")
        fps    = f.get("fps")
        fsize  = f.get("filesize") or f.get("filesize_approx")

        if vcodec == "none" and acodec == "none":
            continue

        label_parts = []
        if height:            label_parts.append(f"{height}p")
        if fps and fps > 30:  label_parts.append(f"{int(fps)}fps")
        if ext:               label_parts.append(ext.upper())

        category = "video" if vcodec != "none" else "audio"
        key      = f"{height}-{ext}-{category}"
        if key in seen: continue
        seen.add(key)

        formats.append({
            "format_id":  fid,
            "ext":        ext,
            "resolution": f"{width}x{height}" if width and height else None,
            "height":     height,
            "fps":        fps,
            "tbr":        tbr,
            "filesize":   fsize,
            "vcodec":     vcodec,
            "acodec":     acodec,
            "category":   category,
            "label":      " · ".join(label_parts) or fid,
        })

    formats.sort(key=lambda x: (0 if x["category"] == "video" else 1, -(x["height"] or 0)))

    subtitles = {}
    for lang, subs in (meta.get("subtitles") or {}).items():
        if subs:
            subtitles[lang] = [{"ext": s.get("ext"), "name": s.get("name", lang)} for s in subs[:3]]

    auto_subs = {}
    for lang, subs in (meta.get("automatic_captions") or {}).items():
        if subs:
            auto_subs[lang] = [{"ext": s.get("ext"), "name": s.get("name", lang)} for s in subs[:3]]

    is_playlist    = bool(data.get("playlist") and len(lines) > 1)
    playlist_count = len(lines) if is_playlist else None

    response = {
        "id":               meta.get("id"),
        "title":            meta.get("title"),
        "uploader":         meta.get("uploader") or meta.get("channel"),
        "duration":         meta.get("duration"),
        "duration_string":  meta.get("duration_string"),
        "view_count":       meta.get("view_count"),
        "like_count":       meta.get("like_count"),
        "thumbnail":        meta.get("thumbnail"),
        "description":      (meta.get("description") or "")[:500],
        "upload_date":      meta.get("upload_date"),
        "formats":          formats,
        "subtitles":        subtitles,
        "automatic_captions": auto_subs,
        "is_playlist":      is_playlist,
        "playlist_count":   playlist_count,
        "playlist_title":   meta.get("playlist_title") if is_playlist else None,
        "webpage_url":      meta.get("webpage_url") or url,
        "original_url":     url,
    }

    logger.info(f"Info fetched: {meta.get('title')!r} | {len(formats)} formats")
    return jsonify(response)

# ─── Download ─────────────────────────────────────────────────────────────────

@app.route("/api/download/start", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Invalid URL"}), 422

    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {
            "status":     "queued",
            "progress":   0,
            "speed":      None,
            "eta":        None,
            "filename":   None,
            "filesize":   None,
            "error":      None,
            "url":        url,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_download_worker,
        args=(job_id, url, data),
        daemon=True,
        name=f"download-{job_id[:8]}",
    )
    thread.start()

    logger.info(f"Download job {job_id[:8]} started for {url}")
    return jsonify({"job_id": job_id})


# ─── Progress template untuk parsing akurat (seperti metube) ─────────────────
# Template ini menghasilkan baris YTDLP_PROGRESS|...| yang mudah di-parse
_PROGRESS_TMPL = (
    "download:YTDLP_PROGRESS"
    "|%(progress.downloaded_bytes)s"
    "|%(progress.total_bytes)s"
    "|%(progress.total_bytes_estimate)s"
    "|%(progress.speed)s"
    "|%(progress.eta)s"
    "|%(progress._percent_str)s"
)


def _parse_progress_line(line: str) -> dict | None:
    """
    Parse baris YTDLP_PROGRESS dari yt-dlp progress-template.
    Mengembalikan dict dengan key: progress, filesize, speed, eta
    atau None jika bukan baris progress.
    """
    if not line.startswith("YTDLP_PROGRESS|"):
        return None

    parts = line.split("|")
    # parts[0] = "YTDLP_PROGRESS"
    # parts[1] = downloaded_bytes
    # parts[2] = total_bytes
    # parts[3] = total_bytes_estimate
    # parts[4] = speed (bytes/s)
    # parts[5] = eta (seconds)
    # parts[6] = percent string (e.g. " 42.3%")

    def safe_float(val):
        try:
            v = float(val)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    downloaded = safe_float(parts[1] if len(parts) > 1 else None)
    total      = safe_float(parts[2] if len(parts) > 2 else None) or \
                 safe_float(parts[3] if len(parts) > 3 else None)
    speed_bps  = safe_float(parts[4] if len(parts) > 4 else None)
    eta_secs   = safe_float(parts[5] if len(parts) > 5 else None)
    pct_str    = parts[6].strip() if len(parts) > 6 else ""

    # Hitung persen dari downloaded/total (akurat), fallback ke pct_str
    pct = None
    if downloaded is not None and total and total > 0:
        pct = min(downloaded / total * 100, 100.0)
    elif pct_str:
        m = re.search(r"([\d.]+)%", pct_str)
        if m:
            pct = float(m.group(1))

    return {
        "progress": pct,
        "filesize":  _format_bytes(total) if total else None,
        "speed":     _format_speed(speed_bps),
        "eta":       _format_eta(eta_secs),
    }


def _build_format_selector(audio_only: bool, audio_format: str,
                            audio_quality: str, format_id: str,
                            quality: str) -> list:
    """
    Bangun argumen -f / -x yang tepat untuk yt-dlp.
    Referensi: metube dl_formats.py get_format()
    """
    # ── Audio only ────────────────────────────────────────────────────────────
    if audio_only:
        # Pilih stream audio terbaik, lalu konversi ke format yang diminta.
        # Mirip metube: bestaudio/best agar tidak gagal "format not available".
        # -x = extract audio; --audio-format = target codec via ffmpeg.
        # audio_quality: 0=best VBR (default), 2=high, 5=medium, 9=low
        # Untuk FLAC/WAV (lossless), --audio-quality tidak berpengaruh.
        _aq = audio_quality if audio_quality in ("0", "2", "5", "9") else "0"
        return [
            "-f", "bestaudio/best",
            "-x",
            "--audio-format", (audio_format or "mp3").lower(),
            "--audio-quality", _aq,
        ]

    # ── Format_id eksplisit dari daftar format ────────────────────────────────
    if format_id:
        # Fallback chain: format+audio → format+apapun → format saja → best
        # Matching metube: bestvideo+bestaudio/best pattern
        return [
            "-f",
            f"{format_id}+bestaudio[ext=m4a]"
            f"/{format_id}+bestaudio"
            f"/{format_id}"
            f"/bestvideo+bestaudio/best",
        ]

    # ── Quality preset (resolusi) ─────────────────────────────────────────────
    if quality and quality not in ("best", "bestvideo+bestaudio", "bestvideo+bestaudio/best", ""):
        # quality berupa height, misalnya "720", "1080"
        # Coba dua kolom: mp4 stream dulu, lalu any
        vres = f"[height<={quality}]"
        return [
            "-f",
            f"bestvideo{vres}[ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo{vres}+bestaudio"
            f"/best{vres}"
            f"/bestvideo+bestaudio/best",
        ]

    # ── Default: kualitas terbaik ─────────────────────────────────────────────
    return ["-f", "bestvideo+bestaudio/best"]


def _download_worker(job_id: str, url: str, opts: dict):
    """Background thread: runs yt-dlp and updates job state."""
    cleanup_old_files()
    cleanup_old_jobs()

    audio_only     = opts.get("audio_only", False)
    audio_format   = (opts.get("audio_format") or "mp3").lower()
    # audio_quality: 0=best VBR, 2=high, 5=medium, 9=low (untuk lossy MP3/AAC/OGG/Opus)
    # Untuk FLAC/WAV (lossless) nilai ini diabaikan yt-dlp (sudah lossless)
    audio_quality  = str(opts.get("audio_quality") or "0").strip()
    if audio_quality not in ("0", "2", "5", "9"):
        audio_quality = "0"
    format_id      = (opts.get("format_id") or "").strip()
    quality        = (opts.get("quality") or "").strip()
    # Container format untuk video: mp4/mkv/webm/avi/mov. Default mp4.
    _VALID_CONTAINERS = {"mp4", "mkv", "webm", "avi", "mov"}
    container      = (opts.get("container") or "mp4").lower().strip()
    if container not in _VALID_CONTAINERS:
        container = "mp4"
    subtitles      = opts.get("subtitles", False)
    subtitle_lang  = opts.get("subtitle_lang", "en")
    auto_subs      = opts.get("auto_subtitles", False)
    playlist       = opts.get("playlist", False)
    playlist_start = opts.get("playlist_start")  # None = dari awal
    playlist_end   = opts.get("playlist_end")    # None = sampai akhir
    embed_thumb    = opts.get("embed_thumbnail", False)
    embed_meta     = opts.get("embed_metadata", True)
    write_subs     = opts.get("write_subtitles", False)
    sub_format     = opts.get("subtitle_format", "srt")
    cookies_from   = (opts.get("cookies_from_browser") or "").strip()
    rate_limit     = opts.get("rate_limit")
    user_proxy     = (opts.get("proxy") or "").strip()
    embed_chapters = opts.get("embed_chapters", False)

    output_template = str(DOWNLOAD_DIR / "%(title)s.%(ext)s")

    args = [
        url,
        "--output", output_template,
        "--no-warnings",
        "--socket-timeout", "60",
        "--retries", "5",
        "--fragment-retries", "5",
        "--extractor-retries", "3",
        "--newline",
        # Gunakan progress-template untuk data numerik yang akurat (seperti metube)
        "--progress-template", _PROGRESS_TMPL,
    ]

    # ── Format selection (diperbaiki) ─────────────────────────────────────────
    args += _build_format_selector(audio_only, audio_format, audio_quality, format_id, quality)

    # ── Playlist ──────────────────────────────────────────────────────────────
    if not playlist:
        args.append("--no-playlist")
    else:
        args += [
            "--yes-playlist",
            "--output",
            str(DOWNLOAD_DIR / "%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s"),
        ]
        # Playlist range: --playlist-start / --playlist-end
        try:
            if playlist_start and int(playlist_start) > 1:
                args += ["--playlist-start", str(int(playlist_start))]
        except (TypeError, ValueError):
            pass
        try:
            if playlist_end and int(playlist_end) > 0:
                args += ["--playlist-end", str(int(playlist_end))]
        except (TypeError, ValueError):
            pass

    # ── Subtitles ─────────────────────────────────────────────────────────────
    if subtitles:
        args += ["--write-subs", "--sub-langs", subtitle_lang, "--sub-format", sub_format]
    if auto_subs:
        args += ["--write-auto-subs", "--sub-langs", subtitle_lang]
    if write_subs:
        args += ["--embed-subs"]

    # ── Metadata / Thumbnail ──────────────────────────────────────────────────
    if embed_thumb:
        args.append("--embed-thumbnail")
    if embed_meta:
        args.append("--embed-metadata")
    if embed_chapters and not audio_only:
        args.append("--embed-chapters")

    # ── Network & Cookies ─────────────────────────────────────────────────────
    if rate_limit:
        args += ["--rate-limit", str(rate_limit)]
    if cookies_from:
        args += ["--cookies-from-browser", cookies_from]

    args += get_proxy_args(user_proxy)
    args += get_cookies_args()

    # ── Post-processing (video) ───────────────────────────────────────────────
    if not audio_only:
        # Gunakan container yang dipilih user (mp4/mkv/webm/avi/mov)
        args += ["--merge-output-format", container, "--add-metadata"]

    with jobs_lock:
        jobs[job_id]["status"] = "downloading"

    logger.info(f"Job {job_id[:8]} yt-dlp args: {' '.join(args)}")

    # ── Run yt-dlp ────────────────────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            ["yt-dlp"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        output_filename = None
        error_lines     = []

        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            # Kumpulkan baris error
            if any(pfx in line for pfx in ("ERROR:", "WARNING:", "Error:")):
                error_lines.append(line)

            # ── Parse progress dari template (akurat, seperti metube) ──────
            progress_data = _parse_progress_line(line)
            if progress_data:
                with jobs_lock:
                    if progress_data["progress"] is not None:
                        jobs[job_id]["progress"] = progress_data["progress"]
                    if progress_data["filesize"]:
                        jobs[job_id]["filesize"] = progress_data["filesize"]
                    if progress_data["speed"]:
                        jobs[job_id]["speed"]    = progress_data["speed"]
                    if progress_data["eta"]:
                        jobs[job_id]["eta"]      = progress_data["eta"]
                continue  # Baris progress sudah di-handle

            # ── Fallback: parse progress dari format lama yt-dlp ──────────
            # (untuk kompatibilitas jika --progress-template tidak support)
            if "[download]" in line and "%" in line:
                m = re.search(
                    r"([\d.]+)%"
                    r"(?:\s+of\s+~?\s*([\d.]+\s*\S+))?"
                    r"(?:\s+at\s+([\d.]+\s*\S+/s))?"
                    r"(?:\s+ETA\s+(\S+))?",
                    line,
                )
                if m:
                    with jobs_lock:
                        jobs[job_id]["progress"] = float(m.group(1))
                        if m.group(2): jobs[job_id]["filesize"] = m.group(2).strip()
                        if m.group(3): jobs[job_id]["speed"]    = m.group(3).strip()
                        if m.group(4): jobs[job_id]["eta"]      = m.group(4).strip()

            # ── Deteksi nama file output ───────────────────────────────────
            if "Destination:" in line:
                output_filename = line.split("Destination:")[-1].strip()

            if "[Merger]" in line and "Merging formats into" in line:
                m = re.search(r'Merging formats into ["\'](.+?)["\']', line)
                if m:
                    output_filename = m.group(1).strip()

            if "[ExtractAudio]" in line and "Destination:" in line:
                output_filename = line.split("Destination:")[-1].strip()

            if "has already been downloaded" in line:
                m = re.search(r"\[download\]\s+(.+?)\s+has already been downloaded", line)
                if m:
                    output_filename = m.group(1).strip()

            logger.debug(f"yt-dlp [{job_id[:8]}]: {line}")

        proc.wait(timeout=600)

        if proc.returncode == 0:
            if not output_filename or not Path(output_filename).exists():
                output_filename = str(_find_latest_file(hint_name=output_filename) or "")

            with jobs_lock:
                jobs[job_id]["status"]   = "completed"
                jobs[job_id]["progress"] = 100
                jobs[job_id]["filename"] = Path(output_filename).name if output_filename else None
                jobs[job_id]["filepath"] = output_filename

            logger.info(f"Job {job_id[:8]} completed: {output_filename}")
        else:
            candidate = _find_latest_file(hint_name=output_filename)
            if candidate and candidate.stat().st_mtime > time.time() - 30:
                logger.warning(f"Job {job_id[:8]} exited {proc.returncode} but file found: {candidate}")
                with jobs_lock:
                    jobs[job_id]["status"]   = "completed"
                    jobs[job_id]["progress"] = 100
                    jobs[job_id]["filename"] = candidate.name
                    jobs[job_id]["filepath"] = str(candidate)
            else:
                err_msg = _extract_error_message("\n".join(error_lines)) if error_lines else "Download failed. Cek URL atau coba lagi."
                with jobs_lock:
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["error"]  = err_msg
                logger.warning(f"Job {job_id[:8]} failed (code {proc.returncode}): {err_msg}")

    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = "Download timed out."
    except FileNotFoundError:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = "yt-dlp is not installed on this server."
    except Exception as e:
        logger.exception(f"Unexpected error in job {job_id[:8]}")
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = str(e)

@app.route("/api/download/status/<job_id>", methods=["GET"])
def job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "job_id":    job_id,
        "status":    job["status"],
        "progress":  job["progress"],
        "speed":     job["speed"],
        "eta":       job["eta"],
        "filename":  job["filename"],
        "filesize":  job["filesize"],
        "error":     job["error"],
    })

@app.route("/api/download/file/<job_id>", methods=["GET"])
def serve_file(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "Download not yet complete"}), 409

    filepath = job.get("filepath")
    if not filepath or not Path(filepath).exists():
        return jsonify({"error": "File not found on server"}), 404

    filename = Path(filepath).name
    return send_file(filepath, as_attachment=True, download_name=sanitize_filename(filename))

@app.route("/api/download/cancel/<job_id>", methods=["DELETE"])
@app.route("/api/history/<job_id>", methods=["DELETE"])
def cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.pop(job_id, None)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    filepath = job.get("filepath")
    if filepath and Path(filepath).exists():
        try:
            Path(filepath).unlink()
        except OSError:
            pass

    return jsonify({"message": "Job cancelled and file removed"})

# ─── Error Handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint not found"}), 404
    index_path = BASE_DIR / "index.html"
    if index_path.exists():
        return send_from_directory(str(BASE_DIR), "index.html")
    return jsonify({"error": "Endpoint not found", "base_dir": str(BASE_DIR)}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(e):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    logger.info(f"🌙 LunarMediaDL Server starting on port {port}")
    logger.info(f"📂 BASE_DIR: {BASE_DIR}")
    logger.info(f"📂 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"📄 index.html found: {(BASE_DIR / 'index.html').exists()}")
    logger.info(f"🌐 Proxy: {DEFAULT_PROXY or 'none (direct connection)'}")

    _auto_load_cookies()

    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)