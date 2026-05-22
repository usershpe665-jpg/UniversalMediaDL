#!/usr/bin/env python3
"""
LunarMediaDL — Merged Backend
MeTube engine + LunarMediaDL frontend API
- yt-dlp via Python library (NOT shell)
- Subscriptions wired up fully
- NTFS sanitization from ytdl.py
- Advanced metadata (embed thumb, embed meta, chapters, subtitle, codec, clip)
- Realtime Socket.IO progress
- Auto-cleanup 3h
"""

import os, sys, asyncio, json, logging, time, uuid, re
from pathlib import Path
from typing import Any

# ── Resolve base directory ─────────────────────────────────────────────────────
_SCRIPT_DIR  = Path(__file__).resolve().parent   # …/app/
_PROJECT_DIR = _SCRIPT_DIR.parent                # project root
_STATIC_DIR  = str(_PROJECT_DIR / "static")

from aiohttp import web
from aiohttp.log import access_logger
import socketio
import yt_dlp
from yt_dlp.version import __version__ as yt_dlp_version

from ytdl import DownloadQueueNotifier, DownloadQueue, Download
from subscriptions import SubscriptionManager, SubscriptionNotifier, SubscriptionInfo
from dl_formats import AUDIO_FORMATS

# ── Logging ────────────────────────────────────────────────────────────────────
def _parse_level(v):
    return getattr(logging, str(v).upper(), None)

if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=_parse_level(os.environ.get("LOGLEVEL","INFO")) or logging.INFO)

log = logging.getLogger("main")

# ── Config ─────────────────────────────────────────────────────────────────────
class Config:
    _DEFAULTS = {
        "DOWNLOAD_DIR":               "/app/downloads",
        "AUDIO_DOWNLOAD_DIR":         "%%DOWNLOAD_DIR",
        "TEMP_DIR":                   "%%DOWNLOAD_DIR",
        "STATE_DIR":                  "/app/state",
        "OUTPUT_TEMPLATE":            "%(title)s.%(ext)s",
        "OUTPUT_TEMPLATE_CHAPTER":    "%(title)s - %(section_number)02d - %(section_title)s.%(ext)s",
        "OUTPUT_TEMPLATE_PLAYLIST":   "%(playlist_title)s/%(title)s.%(ext)s",
        "OUTPUT_TEMPLATE_CHANNEL":    "%(channel)s/%(title)s.%(ext)s",
        "CUSTOM_DIRS":                "false",
        "CREATE_CUSTOM_DIRS":         "false",
        "CUSTOM_DIRS_EXCLUDE_REGEX":  r"(^|/)[.@].*$",
        "DELETE_FILE_ON_TRASHCAN":    "false",
        "DOWNLOAD_DIRS_INDEXABLE":    "false",
        "URL_PREFIX":                 "/",
        "PUBLIC_HOST_URL":            "download/",
        "PUBLIC_HOST_AUDIO_URL":      "download/",
        "DEFAULT_OPTION_PLAYLIST_ITEM_LIMIT": "0",
        "SUBSCRIPTION_DEFAULT_CHECK_INTERVAL": "60",
        "SUBSCRIPTION_SCAN_PLAYLIST_END":      "50",
        "SUBSCRIPTION_MAX_SEEN_IDS":           "50000",
        "CLEAR_COMPLETED_AFTER":      "0",
        "YTDL_OPTIONS":               "{}",
        "YTDL_OPTIONS_FILE":          "",
        "YTDL_OPTIONS_PRESETS":       "{}",
        "YTDL_OPTIONS_PRESETS_FILE":  "",
        "ALLOW_YTDL_OPTIONS_OVERRIDES": "false",
        "CORS_ALLOWED_ORIGINS":       "",
        "HOST":                       "0.0.0.0",
        "PORT":                       os.environ.get("PORT", "8080"),
        "MAX_CONCURRENT_DOWNLOADS":   "3",
        "LOGLEVEL":                   "INFO",
        "ENABLE_ACCESSLOG":           "false",
        "FILE_CLEANUP_HOURS":         "3",
    }
    _BOOLEAN = (
        "CUSTOM_DIRS","CREATE_CUSTOM_DIRS","DELETE_FILE_ON_TRASHCAN",
        "DOWNLOAD_DIRS_INDEXABLE","ALLOW_YTDL_OPTIONS_OVERRIDES","ENABLE_ACCESSLOG",
    )

    def __init__(self):
        for k, v in self._DEFAULTS.items():
            setattr(self, k, os.environ.get(k, v))
        for k, v in list(self.__dict__.items()):
            if isinstance(v, str) and v.startswith("%%"):
                setattr(self, k, getattr(self, v[2:]))
        for k in self._BOOLEAN:
            v = getattr(self, k)
            if v not in ("true","false","True","False","on","off","1","0"):
                log.error(f'Config "{k}" has non-boolean value "{v}"'); sys.exit(1)
            setattr(self, k, v in ("true","True","on","1"))
        if not self.URL_PREFIX.endswith("/"):
            self.URL_PREFIX += "/"

        for d in (self.DOWNLOAD_DIR, self.AUDIO_DOWNLOAD_DIR, self.TEMP_DIR, self.STATE_DIR):
            os.makedirs(d, exist_ok=True)

        self.YTDL_OPTIONS      = {}
        self.YTDL_OPTIONS_PRESETS = {}
        self._runtime_overrides = {}
        self._load_ytdl_options()
        self._load_ytdl_presets()
        self._load_cookies_from_env()

    def _load_ytdl_options(self):
        raw = os.environ.get("YTDL_OPTIONS","{}") or "{}"
        try:
            opts = json.loads(raw)
            assert isinstance(opts, dict)
            self.YTDL_OPTIONS = opts
        except Exception:
            log.error("YTDL_OPTIONS is invalid JSON"); self.YTDL_OPTIONS = {}

    def _load_ytdl_presets(self):
        raw = os.environ.get("YTDL_OPTIONS_PRESETS","{}") or "{}"
        try:
            presets = json.loads(raw)
            assert isinstance(presets, dict)
            self.YTDL_OPTIONS_PRESETS = presets
        except Exception:
            log.error("YTDL_OPTIONS_PRESETS is invalid JSON"); self.YTDL_OPTIONS_PRESETS = {}

    def _load_cookies_from_env(self):
        """
        Load cookies directly from Railway env var — plain Netscape cookies.txt text,
        no base64/encryption needed.  Writes to STATE_DIR/cookies.txt and injects
        'cookiefile' into YTDL_OPTIONS so every yt-dlp call picks them up.
        """
        cookie_content = (
            os.environ.get("YTDL_COOKIES","") or
            os.environ.get("COOKIES_TXT","") or
            os.environ.get("YT_COOKIES","")
        ).strip()

        cookie_path = os.path.join(self.STATE_DIR, "cookies.txt")

        if cookie_content:
            try:
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(cookie_content)
                self.set_runtime_override("cookiefile", cookie_path)
                log.info(f"Cookies loaded from env → {cookie_path}")
            except OSError as e:
                log.warning(f"Could not write cookies: {e}")
        elif os.path.isfile(cookie_path) and "cookiefile" not in self._runtime_overrides:
            self.set_runtime_override("cookiefile", cookie_path)
            log.info(f"Existing cookies detected: {cookie_path}")

    def set_runtime_override(self, k, v):
        self._runtime_overrides[k] = v
        self.YTDL_OPTIONS[k] = v

    def remove_runtime_override(self, k):
        self._runtime_overrides.pop(k, None)
        self.YTDL_OPTIONS.pop(k, None)


config = Config()
logging.getLogger().setLevel(_parse_level(str(config.LOGLEVEL)) or logging.INFO)

# ── JSON serializer ────────────────────────────────────────────────────────────
class _Enc(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__dict__"): return obj.__dict__
        try: return list(obj)
        except Exception: pass
        return json.JSONEncoder.default(self, obj)
_enc = _Enc()

# ── Socket.IO + aiohttp ────────────────────────────────────────────────────────
_cors = [o.strip() for o in config.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
app  = web.Application(client_max_size=10*1024*1024)
sio  = socketio.AsyncServer(
    cors_allowed_origins=_cors if _cors else "*",
    async_mode="aiohttp",
    logger=False, engineio_logger=False,
)
sio.attach(app, socketio_path="socket.io")
routes = web.RouteTableDef()

# ── Job registry (UUID ↔ URL) ──────────────────────────────────────────────────
_job_by_id:  dict[str, str] = {}   # uuid  → url
_id_by_url:  dict[str, str] = {}   # url   → uuid
_jlock = asyncio.Lock()

async def _register_job(url: str) -> str:
    async with _jlock:
        if url in _id_by_url:
            return _id_by_url[url]
        jid = str(uuid.uuid4())
        _job_by_id[jid] = url
        _id_by_url[url] = jid
        return jid

def _url_of(jid: str)  -> str | None: return _job_by_id.get(jid)
def _jid_of(url: str)  -> str | None: return _id_by_url.get(url)

# ── Progress payload builder ───────────────────────────────────────────────────
def _progress_of(dl, jid: str) -> dict:
    """
    Convert a MeTube DownloadInfo into a frontend-ready progress dict.
    Speed/ETA come from ACTUAL yt-dlp progress_hook bytes — no fake values.
    """
    pct = 0.0
    raw_pct = getattr(dl, "percent", None)
    if raw_pct is not None:
        try: pct = float(str(raw_pct).replace("%","").strip())
        except (ValueError, TypeError): pct = 0.0

    # Speed: yt-dlp reports bytes/s; format for UI
    speed_raw = getattr(dl, "speed", None)
    speed_str = ""
    if speed_raw:
        try:
            sp = float(speed_raw)
            if sp >= 1_048_576:   speed_str = f"{sp/1_048_576:.1f} MB/s"
            elif sp >= 1_024:     speed_str = f"{sp/1_024:.1f} KB/s"
            else:                 speed_str = f"{sp:.0f} B/s"
        except (ValueError, TypeError): speed_str = str(speed_raw)

    # ETA: yt-dlp reports seconds
    eta_raw = getattr(dl, "eta", None)
    eta_str = ""
    if eta_raw is not None:
        try:
            s = int(eta_raw)
            if s >= 3600:   eta_str = f"ETA {s//3600}h {(s%3600)//60}m"
            elif s >= 60:   eta_str = f"ETA {s//60}m {s%60}s"
            else:           eta_str = f"ETA {s}s"
        except (ValueError, TypeError): eta_str = ""

    status   = getattr(dl, "status",   "pending")
    filename = getattr(dl, "filename", "") or ""
    entry    = getattr(dl, "entry",    None)
    thumb    = ""
    if isinstance(entry, dict):
        thumb = entry.get("thumbnail","") or ""

    return {
        "job_id":    jid,
        "url":       getattr(dl, "url",   ""),
        "title":     getattr(dl, "title", ""),
        "status":    status,
        "progress":  pct,
        "speed":     speed_str,
        "eta":       eta_str,
        "filename":  filename,
        "error":     getattr(dl, "error", "") or "",
        "thumbnail": thumb,
        "file_url":  f"/download/file/{jid}" if status in ("finished","completed") else "",
    }

# ── Download notifier → Socket.IO ─────────────────────────────────────────────
class DLNotifier(DownloadQueueNotifier):
    async def added(self, dl):
        jid = _jid_of(dl.url) or dl.url
        p   = _progress_of(dl, jid)
        await sio.emit("lunar_added",    _enc.encode(p))
        await sio.emit("added",          _enc.encode(p))

    async def updated(self, dl):
        jid = _jid_of(dl.url) or dl.url
        p   = _progress_of(dl, jid)
        await sio.emit("lunar_progress", _enc.encode(p))
        await sio.emit("updated",        _enc.encode(p))

    async def completed(self, dl):
        jid = _jid_of(dl.url) or dl.url
        p   = _progress_of(dl, jid)
        p["file_url"] = f"/download/file/{jid}"
        await sio.emit("lunar_completed",_enc.encode(p))
        await sio.emit("completed",      _enc.encode(p))

    async def canceled(self, id_):
        jid = _jid_of(id_) or id_
        d   = {"job_id": jid, "url": id_}
        await sio.emit("lunar_canceled", _enc.encode(d))
        await sio.emit("canceled",       _enc.encode(d))

    async def cleared(self, id_):
        jid = _jid_of(id_) or id_
        await sio.emit("cleared", _enc.encode({"job_id": jid, "url": id_}))

# ── Subscription notifier → Socket.IO ─────────────────────────────────────────
class SubNotifier(SubscriptionNotifier):
    async def subscription_added(self, sub: SubscriptionInfo):
        await sio.emit("sub_added",   _enc.encode(sub.to_public_dict()))

    async def subscription_updated(self, sub: SubscriptionInfo):
        await sio.emit("sub_updated", _enc.encode(sub.to_public_dict()))

    async def subscription_removed(self, sub_id: str):
        await sio.emit("sub_removed", _enc.encode({"id": sub_id}))

    async def subscriptions_all(self, subs: list[SubscriptionInfo]):
        await sio.emit("sub_all", _enc.encode([s.to_public_dict() for s in subs]))

# ── Init queues ────────────────────────────────────────────────────────────────
dl_notifier  = DLNotifier()
sub_notifier = SubNotifier()
dqueue   = DownloadQueue(config, dl_notifier)
subman   = SubscriptionManager(config, dqueue, sub_notifier)

async def _startup(_app):
    await dqueue.initialize()
    subman.start_background_loop()
    log.info("SubscriptionManager background loop started")

async def _cleanup(_app):
    Download.shutdown_manager()

app.on_startup.append(_startup)
app.on_cleanup.append(_cleanup)

# ── Socket.IO: send full state on connect ──────────────────────────────────────
@sio.event
async def connect(sid, environ):
    log.info(f"Socket.IO client connected: {sid}")
    q_items, d_items = dqueue.get()
    items = []
    for url, dl in q_items:
        jid = _jid_of(url) or url
        items.append(_progress_of(dl, jid))
    for url, dl in d_items:
        jid = _jid_of(url) or url
        p   = _progress_of(dl, jid)
        p["file_url"] = f"/download/file/{jid}"
        items.append(p)
    await sio.emit("lunar_state", _enc.encode({"items": items}), to=sid)
    await subman.emit_all()

# ── CORS middleware ────────────────────────────────────────────────────────────
async def _on_prepare(request, response):
    if request.headers.get("Origin"):
        response.headers.update({
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, PUT, OPTIONS",
        })

app.on_response_prepare.append(_on_prepare)

async def _cors_ok(request):
    return web.Response(text='{"status":"ok"}', content_type="application/json")

for _path in ("/metadata","/download","/cancel","/queue","/history",
              "/subscriptions","/subscriptions/{id}","/subscriptions/{id}/check"):
    app.router.add_route("OPTIONS", _path, _cors_ok)

# ══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@routes.get("/health")
async def health(request):
    return web.json_response({"status":"ok","yt_dlp": yt_dlp_version})

@routes.get("/version")
async def version(request):
    return web.json_response({
        "app":"LunarMediaDL","version": os.getenv("APP_VERSION","1.0.0"),
        "yt_dlp": yt_dlp_version,
    })

# ── /metadata ──────────────────────────────────────────────────────────────────
@routes.post("/metadata")
async def metadata(request):
    """
    Extract video metadata WITHOUT downloading.
    Uses yt-dlp Python library directly (no subprocess/shell).
    Body: { "url": "https://…", "playlist": false }
    """
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")

    url = str(body.get("url","")).strip()
    if not url: raise web.HTTPBadRequest(reason="Missing 'url'")

    log.info(f"Extracting metadata: {url}")

    def _extract():
        opts = dict(config.YTDL_OPTIONS)   # includes cookiefile if set
        opts.update({
            "quiet":                    True,
            "no_color":                 True,
            "extract_flat":             "in_playlist" if body.get("playlist") else True,
            "noplaylist":               not body.get("playlist", False),
            "ignore_no_formats_error":  True,
            "socket_timeout":           30,
        })
        # Use yt-dlp Python API — NOT subprocess
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.get_running_loop().run_in_executor(None, _extract)
    except yt_dlp.utils.YoutubeDLError as e:
        log.warning(f"Metadata failed: {e}")
        raise web.HTTPBadRequest(reason=str(e))
    except Exception as e:
        log.error(f"Metadata error: {e}")
        raise web.HTTPInternalServerError(reason="Metadata extraction failed")

    if not info:
        raise web.HTTPBadRequest(reason="Could not extract metadata")

    dur = info.get("duration") or 0
    dur_str = ""
    if dur:
        h, r = divmod(int(dur), 3600); m, s = divmod(r, 60)
        dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # Build formats list for advanced UI
    raw_fmts = info.get("formats") or []
    formats = []
    for f in raw_fmts:
        vcodec = f.get("vcodec","")
        acodec = f.get("acodec","")
        has_v  = vcodec and vcodec != "none"
        has_a  = acodec and acodec != "none"
        cat    = "video" if has_v else ("audio" if has_a else "unknown")
        height = f.get("height") or 0
        fps    = f.get("fps") or 0
        size   = f.get("filesize") or f.get("filesize_approx") or 0
        label  = f.get("format_note") or f.get("ext","")
        if height: label = f"{height}p{f'+{round(fps)}fps' if fps > 30 else ''} {label}"
        formats.append({
            "format_id": f.get("format_id",""),
            "ext":       f.get("ext",""),
            "category":  cat,
            "height":    height,
            "fps":       fps,
            "filesize":  size,
            "label":     label,
            "resolution": f.get("resolution") or (f"{height}p" if height else ""),
            "vcodec":    vcodec,
            "acodec":    acodec,
            "tbr":       f.get("tbr") or 0,
        })

    result = {
        "title":           info.get("title") or info.get("webpage_url_basename") or "Media",
        "thumbnail":       info.get("thumbnail") or "",
        "uploader":        info.get("uploader") or info.get("channel") or info.get("creator") or "",
        "duration":        dur,
        "duration_string": info.get("duration_string") or dur_str,
        "webpage_url":     info.get("webpage_url") or url,
        "original_url":    url,
        "extractor":       info.get("extractor_key") or info.get("extractor") or "",
        "view_count":      info.get("view_count") or 0,
        "like_count":      info.get("like_count") or 0,
        "upload_date":     info.get("upload_date") or "",
        "description":     (info.get("description") or "")[:1000],
        "is_playlist":     info.get("_type") == "playlist",
        "playlist_count":  info.get("playlist_count") or 0,
        "formats":         formats,
    }
    return web.Response(text=json.dumps(result), content_type="application/json")


# ── /download ─────────────────────────────────────────────────────────────────
@routes.post("/download")
async def download_start(request):
    """
    Add URL to MeTube download queue.
    Supports ALL MeTube options:
      download_type: video | audio | captions | thumbnail
      codec:         auto | h264 | h265 | av1 | vp9
      format:        any | mp4 | ios  (video) | mp3|m4a|flac|opus|wav (audio)
                     srt|vtt|ass|... (captions) | jpg (thumbnail)
      quality:       best | worst | 144 | 240 | 360 | 480 | 720 | 1080 | 1440 | 2160
      audio_only:    bool   (shorthand → download_type=audio)
      audio_format:  mp3|m4a|flac|opus|wav
      subtitle_language: en | id | ja | ...
      subtitle_mode:     prefer_manual | prefer_auto | manual_only | auto_only
      split_by_chapters: bool
      clip_start:    float (seconds)
      clip_end:      float (seconds)
      playlist:      bool
      playlist_item_limit: int
      ytdl_options_overrides: dict  (only if ALLOW_YTDL_OPTIONS_OVERRIDES=true)
    """
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")

    url = str(body.get("url","")).strip()
    if not url: raise web.HTTPBadRequest(reason="Missing 'url'")

    # ── Determine download_type + format + codec ──────────────────────────────
    audio_only   = bool(body.get("audio_only", False))
    raw_type     = str(body.get("download_type","")).strip().lower()
    raw_fmt      = str(body.get("format","")).strip().lower()
    raw_audio_fmt= str(body.get("audio_format","mp3")).strip().lower()
    raw_codec    = str(body.get("codec","auto")).strip().lower()
    raw_quality  = str(body.get("quality","best")).strip().lower()

    # Shorthand: audio_only=true → download_type=audio
    if audio_only and not raw_type:
        raw_type = "audio"

    # Resolve type
    valid_types = ("video","audio","captions","thumbnail")
    if raw_type not in valid_types:
        raw_type = "audio" if audio_only else "video"

    # Resolve format
    if raw_type == "audio":
        fmt = raw_audio_fmt if raw_audio_fmt in AUDIO_FORMATS else "mp3"
    elif raw_type == "video":
        fmt = raw_fmt if raw_fmt in ("any","mp4","ios") else "any"
    elif raw_type == "captions":
        fmt = raw_fmt if raw_fmt in ("srt","vtt","ass","lrc","txt","json3") else "srt"
    else:  # thumbnail
        fmt = "jpg"

    # Resolve codec (video only)
    valid_codecs = ("auto","h264","h265","av1","vp9")
    codec = raw_codec if raw_codec in valid_codecs else "auto"

    # Resolve quality
    valid_q = ("best","worst","144","240","360","480","720","1080","1440","2160")
    quality = raw_quality if raw_quality in valid_q else "best"

    # ── Optional advanced params ───────────────────────────────────────────────
    subtitle_language = str(body.get("subtitle_language","en")).strip() or "en"
    subtitle_mode     = str(body.get("subtitle_mode","prefer_manual")).strip()
    valid_modes = ("prefer_manual","prefer_auto","manual_only","auto_only")
    if subtitle_mode not in valid_modes: subtitle_mode = "prefer_manual"

    split_by_chapters = bool(body.get("split_by_chapters", False))
    chapter_template  = str(body.get("chapter_template", config.OUTPUT_TEMPLATE_CHAPTER))

    clip_start = body.get("clip_start")
    clip_end   = body.get("clip_end")
    try: clip_start = float(clip_start) if clip_start is not None else None
    except (ValueError,TypeError): clip_start = None
    try: clip_end   = float(clip_end)   if clip_end   is not None else None
    except (ValueError,TypeError): clip_end = None

    playlist            = bool(body.get("playlist", False))
    playlist_item_limit = int(body.get("playlist_item_limit",
        int(config.DEFAULT_OPTION_PLAYLIST_ITEM_LIMIT)))

    # ytdl_options_overrides only allowed if config flag is on
    ytdl_overrides = {}
    if config.ALLOW_YTDL_OPTIONS_OVERRIDES:
        raw_ov = body.get("ytdl_options_overrides") or {}
        if isinstance(raw_ov, dict):
            ytdl_overrides = raw_ov

    ytdl_presets = []
    raw_presets = body.get("ytdl_options_presets") or []
    if isinstance(raw_presets, list):
        ytdl_presets = [p for p in raw_presets if p in config.YTDL_OPTIONS_PRESETS]

    folder             = str(body.get("folder","")).strip()
    custom_name_prefix = str(body.get("custom_name_prefix","")).strip()

    log.info(f"Download request: {url} type={raw_type} fmt={fmt} codec={codec} quality={quality}")

    jid = await _register_job(url)

    status = await dqueue.add(
        url,
        raw_type,
        codec,
        fmt,
        quality,
        folder,
        custom_name_prefix,
        playlist_item_limit,
        True,              # auto_start
        split_by_chapters,
        chapter_template,
        subtitle_language,
        subtitle_mode,
        ytdl_presets,
        ytdl_overrides,
        clip_start,
        clip_end,
    )

    if isinstance(status, dict) and status.get("status") == "error":
        log.error(f"Download queue error: {status.get('msg')}")
        raise web.HTTPBadRequest(reason=status.get("msg","Download failed to start"))

    return web.Response(
        text=json.dumps({"job_id": jid, "status":"queued",
                         "download_type": raw_type, "format": fmt}),
        content_type="application/json",
    )


# ── /queue ─────────────────────────────────────────────────────────────────────
@routes.get("/queue")
async def queue_status(request):
    q, done = dqueue.get()
    result  = {"queue":[], "done":[]}
    for url, dl in q:
        result["queue"].append(_progress_of(dl, _jid_of(url) or url))
    for url, dl in done:
        jid = _jid_of(url) or url
        p   = _progress_of(dl, jid)
        p["file_url"] = f"/download/file/{jid}"
        result["done"].append(p)
    return web.Response(text=json.dumps(result), content_type="application/json")


# ── /history ───────────────────────────────────────────────────────────────────
@routes.get("/history")
async def history(request):
    _, done = dqueue.get()
    items = []
    for url, dl in done:
        jid = _jid_of(url) or url
        p   = _progress_of(dl, jid)
        p["file_url"] = f"/download/file/{jid}"
        items.append(p)
    return web.Response(
        text=json.dumps({"history": items}), content_type="application/json")


# ── /cancel ────────────────────────────────────────────────────────────────────
@routes.post("/cancel")
async def cancel(request):
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")
    jid = str(body.get("job_id","")).strip()
    if not jid: raise web.HTTPBadRequest(reason="Missing 'job_id'")
    url = _url_of(jid)
    if not url: raise web.HTTPNotFound(reason="Job not found")
    await dqueue.cancel([url])
    return web.Response(text='{"status":"ok"}', content_type="application/json")


# ── /download/file/{job_id} ────────────────────────────────────────────────────
@routes.get("/download/file/{job_id}")
async def serve_file(request):
    """
    Serve completed download file.
    MeTube stores filename RELATIVE to download_dir — resolved to absolute path here.
    """
    jid = request.match_info["job_id"]
    url = _url_of(jid)
    if not url: raise web.HTTPNotFound(reason="Job not found")

    def _abs(dl_info) -> str | None:
        fn = getattr(dl_info, "filename", None)
        if not fn: return None
        if os.path.isabs(fn) and os.path.isfile(fn): return fn
        # MeTube stores relative path from download_dir
        for base in (config.DOWNLOAD_DIR, config.AUDIO_DOWNLOAD_DIR):
            candidate = os.path.normpath(os.path.join(base, fn))
            if os.path.isfile(candidate):
                return candidate
        return None

    q, done = dqueue.get()
    for dl_url, dl in list(done) + list(q):
        if dl_url == url:
            path = _abs(dl)
            if path:
                return web.FileResponse(path, headers={
                    "Content-Disposition": f'attachment; filename="{os.path.basename(path)}"'
                })
            # File not written yet
            return web.Response(status=202, reason="File not ready — retry shortly")

    raise web.HTTPNotFound(reason="Job not found in queue")


# ══════════════════════════════════════════════════════════════════════════════
#  SUBSCRIPTION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@routes.get("/subscriptions")
async def subs_list(request):
    """List all subscriptions."""
    subs = [s.to_public_dict() for s in subman.list_all()]
    return web.Response(text=json.dumps({"subscriptions": subs}),
                        content_type="application/json")


@routes.post("/subscriptions")
async def subs_add(request):
    """
    Add a new subscription (playlist/channel auto-monitor).
    Body: {
      url, download_type, codec, format, quality,
      check_interval_minutes, folder, custom_name_prefix,
      auto_start, playlist_item_limit,
      split_by_chapters, chapter_template,
      subtitle_language, subtitle_mode,
      title_regex, skip_subscriber_only,
      ytdl_options_presets, ytdl_options_overrides
    }
    """
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")

    url = str(body.get("url","")).strip()
    if not url: raise web.HTTPBadRequest(reason="Missing 'url'")

    raw_type = str(body.get("download_type","video")).strip().lower()
    if raw_type not in ("video","audio","captions","thumbnail"): raw_type = "video"

    raw_fmt  = str(body.get("format","any" if raw_type=="video" else "mp3")).strip().lower()
    raw_codec= str(body.get("codec","auto")).strip().lower()
    if raw_codec not in ("auto","h264","h265","av1","vp9"): raw_codec = "auto"

    raw_q    = str(body.get("quality","best")).strip().lower()
    if raw_q not in ("best","worst","144","240","360","480","720","1080","1440","2160"): raw_q = "best"

    interval = int(body.get("check_interval_minutes",
                             int(config.SUBSCRIPTION_DEFAULT_CHECK_INTERVAL)))

    ytdl_ov = body.get("ytdl_options_overrides") or {}
    if not isinstance(ytdl_ov, dict): ytdl_ov = {}
    if not config.ALLOW_YTDL_OPTIONS_OVERRIDES: ytdl_ov = {}

    ytdl_pr = body.get("ytdl_options_presets") or []
    if not isinstance(ytdl_pr, list): ytdl_pr = []
    ytdl_pr = [p for p in ytdl_pr if p in config.YTDL_OPTIONS_PRESETS]

    result = await subman.add_subscription(
        url,
        check_interval_minutes  = interval,
        download_type           = raw_type,
        codec                   = raw_codec,
        format                  = raw_fmt,
        quality                 = raw_q,
        folder                  = str(body.get("folder","")).strip(),
        custom_name_prefix      = str(body.get("custom_name_prefix","")).strip(),
        auto_start              = bool(body.get("auto_start", True)),
        playlist_item_limit     = int(body.get("playlist_item_limit",0)),
        split_by_chapters       = bool(body.get("split_by_chapters", False)),
        chapter_template        = str(body.get("chapter_template", config.OUTPUT_TEMPLATE_CHAPTER)),
        subtitle_language       = str(body.get("subtitle_language","en")).strip() or "en",
        subtitle_mode           = str(body.get("subtitle_mode","prefer_manual")).strip(),
        ytdl_options_presets    = ytdl_pr,
        ytdl_options_overrides  = ytdl_ov,
        title_regex             = body.get("title_regex"),
        skip_subscriber_only    = body.get("skip_subscriber_only"),
    )

    status_code = 200 if result.get("status") == "ok" else 400
    return web.Response(text=json.dumps(result),
                        content_type="application/json", status=status_code)


@routes.delete("/subscriptions/{id}")
async def subs_delete(request):
    """Delete subscription(s). Pass id=all to delete all."""
    sub_id = request.match_info["id"]
    if sub_id == "all":
        ids = [s.id for s in subman.list_all()]
    else:
        ids = [sub_id]
    result = await subman.delete_subscriptions(ids)
    return web.Response(text=json.dumps(result), content_type="application/json")


@routes.put("/subscriptions/{id}")
async def subs_update(request):
    """Update subscription settings."""
    sub_id = request.match_info["id"]
    try: changes = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")
    result = await subman.update_subscription(sub_id, changes)
    status_code = 200 if result.get("status") == "ok" else 400
    return web.Response(text=json.dumps(result),
                        content_type="application/json", status=status_code)


@routes.post("/subscriptions/{id}/check")
async def subs_check(request):
    """Force an immediate subscription check."""
    sub_id = request.match_info["id"]
    ids = None if sub_id == "all" else [sub_id]
    result = await subman.check_now(ids)
    return web.Response(text=json.dumps(result), content_type="application/json")


# ══════════════════════════════════════════════════════════════════════════════
#  STATIC FILE SERVING
# ══════════════════════════════════════════════════════════════════════════════

@routes.get("/")
async def index(request):
    return web.FileResponse(_STATIC_DIR + "/index.html")

@routes.get("/downloader")
@routes.get("/downloader.html")
async def downloader_page(request):
    return web.FileResponse(_STATIC_DIR + "/downloader.html")

@routes.get("/tiktok")
@routes.get("/tiktok.html")
async def tiktok_page(request):
    return web.FileResponse(_STATIC_DIR + "/tiktok.html")

@routes.get("/instagram")
@routes.get("/instagram.html")
async def instagram_page(request):
    return web.FileResponse(_STATIC_DIR + "/instagram.html")

@routes.get("/universal")
@routes.get("/universal.html")
async def universal_page(request):
    return web.FileResponse(_STATIC_DIR + "/universal.html")

routes.static("/static/", _STATIC_DIR + "/")
app.add_routes(routes)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-CLEANUP (3h, runs every 30min)
# ══════════════════════════════════════════════════════════════════════════════
_CLEANUP_INTERVAL = 1800          # 30 min
_FILE_MAX_AGE     = int(os.environ.get("FILE_CLEANUP_HOURS","3")) * 3600

async def _cleanup_loop():
    await asyncio.sleep(120)      # warm-up
    while True:
        try:
            await _do_cleanup()
        except Exception as e:
            log.warning(f"Cleanup error: {e}")
        await asyncio.sleep(_CLEANUP_INTERVAL)

async def _do_cleanup():
    now    = time.time()
    cutoff = now - _FILE_MAX_AGE

    # Protect files currently being downloaded
    active: set[str] = set()
    q, _ = dqueue.get()
    for _, dl in q:
        for attr in ("filename","tmpfilename"):
            fn = getattr(dl, attr, None)
            if fn:
                active.add(os.path.realpath(os.path.join(config.DOWNLOAD_DIR, fn))
                           if not os.path.isabs(fn) else os.path.realpath(fn))

    deleted = errors = 0
    for dl_dir in {os.path.realpath(config.DOWNLOAD_DIR),
                   os.path.realpath(config.AUDIO_DOWNLOAD_DIR)}:
        if not os.path.isdir(dl_dir): continue
        for entry in os.scandir(dl_dir):
            if not entry.is_file(follow_symlinks=False): continue
            if os.path.realpath(entry.path) in active:   continue
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path); deleted += 1
                    log.debug(f"Cleanup removed: {entry.path}")
            except OSError as e:
                errors += 1
                log.debug(f"Cleanup skip {entry.path}: {e}")

    if deleted or errors:
        log.info(f"Cleanup: {deleted} deleted, {errors} errors")

async def _start_cleanup(_app):
    asyncio.create_task(_cleanup_loop())

app.on_startup.append(_start_cleanup)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import socket

    port = int(config.PORT)
    host = config.HOST

    log.info(f"LunarMediaDL starting  {host}:{port}")
    log.info(f"Download dir : {config.DOWNLOAD_DIR}")
    log.info(f"State dir    : {config.STATE_DIR}")
    log.info(f"yt-dlp       : {yt_dlp_version}  (Python library, no shell)")
    log.info(f"File cleanup : {_FILE_MAX_AGE//3600}h  (interval {_CLEANUP_INTERVAL//60}min)")
    log.info(f"Subscriptions: enabled  ({config.SUBSCRIPTION_DEFAULT_CHECK_INTERVAL}min default interval)")
    log.info(f"NTFS fix     : enabled  (via ytdl._sanitize_path_component)")

    def _can_reuse_port():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, getattr(socket,"SO_REUSEPORT",15), 1)
            s.close(); return True
        except (AttributeError, OSError): return False

    access_log = access_logger if config.ENABLE_ACCESSLOG else None
    web.run_app(app, host=host, port=port,
                reuse_port=_can_reuse_port(), access_log=access_log)
