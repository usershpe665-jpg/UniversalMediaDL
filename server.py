#!/usr/bin/env python3
"""
LunarMediaDL — Backend Server (Merged Edition)
UI: UniversalMediaDL (space theme)
Engine: metube-style yt-dlp Python API with proper download queue
Cookies: YOUTUBE_COOKIES env var (plain Netscape text, no base64)
Proxy: REMOVED
"""

import os
import sys
import json
import uuid
import time
import threading
import logging
import re
import queue
import multiprocessing
from pathlib import Path
from datetime import datetime

import yt_dlp
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("LunarMediaDL")

# ─── Paths ────────────────────────────────────────────────────────────────────
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
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = BASE_DIR / "cookies.txt"

# ─── Cookies (plain text from env, no base64) ─────────────────────────────────
def _ensure_cookies():
    """Write YOUTUBE_COOKIES env var (plain Netscape text) to cookies.txt."""
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        return
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        return
    try:
        COOKIES_FILE.write_text(raw, encoding="utf-8")
        logger.info(f"Cookies written from YOUTUBE_COOKIES env var ({len(raw)} chars)")
    except Exception as e:
        logger.warning(f"Failed to write cookies: {e}")

_ensure_cookies()

def _get_cookie_opts() -> dict:
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        return {"cookiefile": str(COOKIES_FILE)}
    return {}

# ─── Flask App ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ─── In-Memory Job Store ──────────────────────────────────────────────────────
jobs: dict = {}
jobs_lock   = threading.Lock()

# ─── yt-dlp Progress Logger ──────────────────────────────────────────────────

class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): logger.warning(f"yt-dlp: {msg}")
    def error(self, msg):   logger.error(f"yt-dlp: {msg}")

# ─── Download Worker (metube-style: yt-dlp Python API + multiprocessing) ─────

def _download_process(job_id: str, url: str, opts: dict, status_q, download_dir: str, cookies_file: str):
    """
    Runs in a separate process (like metube).
    Uses yt-dlp Python API — not subprocess.
    Sends status dicts through status_q.
    """
    audio_only    = opts.get("audio_only", False)
    audio_format  = opts.get("audio_format", "mp3")
    audio_quality = opts.get("audio_quality", "0")
    format_id     = opts.get("format_id", "")
    quality       = opts.get("quality", "")
    playlist      = opts.get("playlist", False)
    embed_thumb   = opts.get("embed_thumbnail", False)
    embed_meta    = opts.get("embed_metadata", True)
    write_subs    = opts.get("write_subtitles", False)
    auto_subs     = opts.get("auto_subtitles", False)
    embed_subs    = opts.get("embed_subs", False)
    sub_lang      = opts.get("subtitle_lang", "en")
    sub_fmt       = opts.get("subtitle_format", "srt")
    rate_limit    = opts.get("rate_limit", "")
    container     = opts.get("container", "mp4")

    dl_dir = Path(download_dir)

    # ── Format selection (metube-style logic) ─────────────────────────────────
    if audio_only:
        fmt = f"bestaudio[ext={audio_format}]/bestaudio/best"
    elif format_id:
        fmt = f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}/bestvideo+bestaudio/best"
    elif quality and quality not in ("best", "bestvideo+bestaudio", ""):
        # quality is like "1080", "720", etc.
        fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/bestvideo+bestaudio/best"
    else:
        fmt = "bestvideo+bestaudio/best"

    # ── Output template ───────────────────────────────────────────────────────
    if playlist:
        outtmpl = str(dl_dir / "%(playlist_title)s" / "%(playlist_index)s - %(title)s.%(ext)s")
    else:
        outtmpl = str(dl_dir / "%(title)s.%(ext)s")

    # ── Postprocessors (metube-style) ─────────────────────────────────────────
    postprocessors = []

    if audio_only:
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": int(audio_quality) if audio_quality.isdigit() else 0,
        })
        if audio_format not in ("wav",):
            postprocessors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            postprocessors.append({"key": "FFmpegMetadata"})
            postprocessors.append({"key": "EmbedThumbnail"})
    else:
        if embed_thumb:
            postprocessors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            postprocessors.append({"key": "EmbedThumbnail"})
        if embed_meta:
            postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
        if embed_subs:
            postprocessors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    # ── Progress hook (sends to queue) ────────────────────────────────────────
    output_filename = []

    def progress_hook(d):
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            down  = d.get("downloaded_bytes", 0)
            pct   = (down / total * 100) if total else 0
            speed = d.get("speed") or 0
            eta   = d.get("eta")

            def _fmt_speed(bps):
                if not bps: return "—"
                if bps > 1_048_576: return f"{bps/1_048_576:.1f} MB/s"
                if bps > 1024:      return f"{bps/1024:.1f} KB/s"
                return f"{int(bps)} B/s"

            status_q.put({
                "type":     "progress",
                "progress": round(pct, 1),
                "speed":    _fmt_speed(speed),
                "eta":      f"{int(eta)}s" if eta else "—",
                "filesize": _fmt_bytes(total),
            })

        elif st == "finished":
            fname = d.get("filename", "")
            if fname:
                output_filename.append(fname)

    def postprocessor_hook(d):
        if d.get("postprocessor") == "MoveFiles" and d.get("status") == "finished":
            info = d.get("info_dict", {})
            fp   = info.get("filepath") or info.get("filename", "")
            if fp:
                output_filename.append(fp)

    def _fmt_bytes(n):
        if not n: return "—"
        if n > 1_073_741_824: return f"{n/1_073_741_824:.1f} GB"
        if n > 1_048_576:     return f"{n/1_048_576:.1f} MB"
        if n > 1024:          return f"{n/1024:.1f} KB"
        return f"{int(n)} B"

    # ── yt-dlp params (metube-style Python API) ───────────────────────────────
    ydl_params = {
        "quiet":          True,
        "no_color":       True,
        "logger":         _SilentLogger(),
        "format":         fmt,
        "outtmpl":        outtmpl,
        "paths":          {"home": str(dl_dir)},
        "socket_timeout": 60,
        "retries":        5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "postprocessors": postprocessors,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "writethumbnail": embed_thumb or audio_only,
        "noplaylist":     not playlist,
    }

    # Merge output format
    if not audio_only:
        ydl_params["merge_output_format"] = container

    # Subtitles
    if write_subs:
        ydl_params["writesubtitles"]   = True
        ydl_params["subtitleslangs"]   = [sub_lang]
        ydl_params["subtitlesformat"]  = sub_fmt
    if auto_subs:
        ydl_params["writeautomaticsub"] = True
        ydl_params["subtitleslangs"]   = [sub_lang]

    # Rate limit
    if rate_limit:
        ydl_params["ratelimit"] = rate_limit

    # Cookies (plain text file)
    if Path(cookies_file).exists() and Path(cookies_file).stat().st_size > 0:
        ydl_params["cookiefile"] = cookies_file

    # ── Run ───────────────────────────────────────────────────────────────────
    try:
        status_q.put({"type": "status", "status": "downloading"})
        with yt_dlp.YoutubeDL(params=ydl_params) as ydl:
            ret = ydl.download([url])

        # Resolve final file
        final_file = ""
        if output_filename:
            # Try last reported, then scan directory
            for candidate in reversed(output_filename):
                p = Path(candidate)
                if p.exists():
                    final_file = str(p)
                    break

        if not final_file:
            # Fall back: newest file in download dir
            files = sorted(
                [f for f in dl_dir.rglob("*.*") if f.is_file()],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if files:
                final_file = str(files[0])

        if ret == 0 and final_file:
            status_q.put({"type": "completed", "filepath": final_file, "filename": Path(final_file).name})
        else:
            status_q.put({"type": "error", "msg": "Download failed or file not found."})

    except yt_dlp.utils.YoutubeDLError as exc:
        status_q.put({"type": "error", "msg": str(exc)})
    except Exception as exc:
        status_q.put({"type": "error", "msg": f"Unexpected error: {exc}"})


def _download_worker(job_id: str, url: str, opts: dict):
    """Thread: starts subprocess, reads queue, updates job state."""
    manager = multiprocessing.Manager()
    status_q = manager.Queue()

    proc = multiprocessing.Process(
        target=_download_process,
        args=(job_id, url, opts, status_q, str(DOWNLOAD_DIR), str(COOKIES_FILE)),
        daemon=True,
    )
    proc.start()

    while True:
        try:
            msg = status_q.get(timeout=1)
        except Exception:
            if not proc.is_alive():
                break
            continue

        if msg is None:
            break

        t = msg.get("type")

        if t == "status":
            with jobs_lock:
                jobs[job_id]["status"] = msg.get("status", "downloading")

        elif t == "progress":
            with jobs_lock:
                jobs[job_id].update({
                    "progress": msg.get("progress", 0),
                    "speed":    msg.get("speed"),
                    "eta":      msg.get("eta"),
                    "filesize": msg.get("filesize"),
                })

        elif t == "completed":
            with jobs_lock:
                jobs[job_id].update({
                    "status":   "completed",
                    "progress": 100,
                    "filename": msg.get("filename"),
                    "filepath": msg.get("filepath"),
                })
            logger.info(f"Job {job_id[:8]} completed: {msg.get('filepath')}")
            break

        elif t == "error":
            with jobs_lock:
                jobs[job_id].update({
                    "status": "error",
                    "error":  msg.get("msg", "Unknown error"),
                })
            logger.warning(f"Job {job_id[:8]} error: {msg.get('msg')}")
            break

    proc.join(timeout=5)
    proc.terminate()
    manager.shutdown()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_valid_url(url: str) -> bool:
    u = url.strip()
    return u.startswith("http://") or u.startswith("https://")

def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:200]

def cleanup_old_files(max_age_hours: int = 4):
    cutoff = time.time() - max_age_hours * 3600
    for f in DOWNLOAD_DIR.rglob("*.*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except OSError:
                pass

# ─── Static Routes ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def serve_index():
    return send_from_directory(str(BASE_DIR), "index.html")

@app.route("/downloader", methods=["GET"])
@app.route("/downloader.html", methods=["GET"])
def serve_downloader():
    return send_from_directory(str(BASE_DIR), "downloader.html")

@app.route("/universal", methods=["GET"])
@app.route("/universal.html", methods=["GET"])
def serve_universal():
    return send_from_directory(str(BASE_DIR), "universal.html")

@app.route("/tiktok", methods=["GET"])
@app.route("/tiktok.html", methods=["GET"])
def serve_tiktok():
    return send_from_directory(str(BASE_DIR), "tiktok.html")

@app.route("/instagram", methods=["GET"])
@app.route("/instagram.html", methods=["GET"])
def serve_instagram():
    return send_from_directory(str(BASE_DIR), "instagram.html")

@app.route("/<path:filename>", methods=["GET"])
def serve_static(filename):
    if filename.startswith("api/"):
        from flask import abort
        abort(404)
    return send_from_directory(str(BASE_DIR), filename)

# ─── API: Health ──────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health_check():
    try:
        import yt_dlp
        ytdlp_version = yt_dlp.version.__version__
    except Exception as e:
        ytdlp_version = f"error: {e}"

    cookies_ok = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
    return jsonify({
        "status":         "online",
        "server":         "LunarMediaDL v3.0.0 (Merged)",
        "ytdlp_version":  ytdlp_version,
        "timestamp":      datetime.utcnow().isoformat(),
        "cookies_loaded": cookies_ok,
        "cookies_source": "YOUTUBE_COOKIES env var" if os.environ.get("YOUTUBE_COOKIES") else ("file" if cookies_ok else "none"),
    })

# ─── API: Info ────────────────────────────────────────────────────────────────

@app.route("/api/info", methods=["POST"])
def fetch_info():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Invalid URL. Must start with http:// or https://"}), 422

    logger.info(f"Fetching info: {url}")

    ydl_opts = {
        "quiet":            True,
        "no_color":         True,
        "logger":           _SilentLogger(),
        "noplaylist":       not data.get("playlist", False),
        "socket_timeout":   30,
        "retries":          3,
        "extractor_retries": 3,
    }
    ydl_opts.update(_get_cookie_opts())

    try:
        with yt_dlp.YoutubeDL(params=ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=False)
    except yt_dlp.utils.YoutubeDLError as exc:
        logger.warning(f"yt-dlp info error: {exc}")
        return jsonify({"error": f"Failed to fetch media info: {str(exc)}"}), 400
    except Exception as exc:
        logger.exception("Unexpected info error")
        return jsonify({"error": str(exc)}), 500

    if meta is None:
        return jsonify({"error": "No media data returned"}), 400

    # Build format list (metube-style dedup + labeling)
    formats = []
    seen = set()
    for f in (meta.get("formats") or []):
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        if vcodec == "none" and acodec == "none":
            continue

        fid    = f.get("format_id", "")
        ext    = f.get("ext", "")
        height = f.get("height")
        width  = f.get("width")
        fps    = f.get("fps")
        tbr    = f.get("tbr")
        fsize  = f.get("filesize") or f.get("filesize_approx")
        category = "video" if vcodec != "none" else "audio"

        key = f"{height}-{ext}-{category}"
        if key in seen:
            continue
        seen.add(key)

        label_parts = []
        if height:           label_parts.append(f"{height}p")
        if fps and fps > 30: label_parts.append(f"{int(fps)}fps")
        if ext:              label_parts.append(ext.upper())

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

    # Subtitles
    subtitles = {}
    for lang, subs in (meta.get("subtitles") or {}).items():
        if subs:
            subtitles[lang] = [{"ext": s.get("ext"), "name": s.get("name", lang)} for s in subs[:3]]

    auto_subs = {}
    for lang, subs in (meta.get("automatic_captions") or {}).items():
        if subs:
            auto_subs[lang] = [{"ext": s.get("ext"), "name": s.get("name", lang)} for s in subs[:3]]

    response = {
        "id":                 meta.get("id"),
        "title":              meta.get("title"),
        "uploader":           meta.get("uploader") or meta.get("channel"),
        "duration":           meta.get("duration"),
        "duration_string":    meta.get("duration_string"),
        "view_count":         meta.get("view_count"),
        "like_count":         meta.get("like_count"),
        "thumbnail":          meta.get("thumbnail"),
        "description":        (meta.get("description") or "")[:500],
        "upload_date":        meta.get("upload_date"),
        "formats":            formats,
        "subtitles":          subtitles,
        "automatic_captions": auto_subs,
        "webpage_url":        meta.get("webpage_url") or url,
        "original_url":       url,
    }

    logger.info(f"Info OK: {meta.get('title')!r} | {len(formats)} formats")
    return jsonify(response)

# ─── API: Start Download ──────────────────────────────────────────────────────

@app.route("/api/download/start", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not is_valid_url(url):
        return jsonify({"error": "Invalid URL"}), 422

    cleanup_old_files()

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status":     "queued",
            "progress":   0,
            "speed":      None,
            "eta":        None,
            "filename":   None,
            "filesize":   None,
            "filepath":   None,
            "error":      None,
            "url":        url,
            "created_at": time.time(),
        }

    t = threading.Thread(
        target=_download_worker,
        args=(job_id, url, data),
        daemon=True,
        name=f"dl-{job_id[:8]}",
    )
    t.start()

    logger.info(f"Job {job_id[:8]} queued: {url}")
    return jsonify({"job_id": job_id})

# ─── API: Job Status ──────────────────────────────────────────────────────────

@app.route("/api/download/status/<job_id>", methods=["GET"])
def job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "job_id":   job_id,
        "status":   job["status"],
        "progress": job["progress"],
        "speed":    job["speed"],
        "eta":      job["eta"],
        "filename": job["filename"],
        "filesize": job["filesize"],
        "error":    job["error"],
    })

# ─── API: Serve File ──────────────────────────────────────────────────────────

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

    return send_file(
        filepath,
        as_attachment=True,
        download_name=sanitize_filename(Path(filepath).name),
    )

# ─── API: Cancel / Delete ────────────────────────────────────────────────────

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
    return jsonify({"error": "Not found"}), 404

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

    logger.info(f"🌙 LunarMediaDL (Merged) starting on port {port}")
    logger.info(f"📂 BASE_DIR:     {BASE_DIR}")
    logger.info(f"📂 Download dir: {DOWNLOAD_DIR}")
    logger.info(f"🍪 Cookies:      {COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0}")

    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
