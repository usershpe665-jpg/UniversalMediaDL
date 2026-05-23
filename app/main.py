#!/usr/bin/env python3
"""
LunarMediaDL — MeTube Engine (exact original) + LunarMediaDL REST/Socket.IO layer
═══════════════════════════════════════════════════════════════════════════════════
Backend : 100% MeTube original (ytdl.py, dl_formats.py, subscriptions.py)
API     : MeTube original routes preserved (/add, /delete, /history, …)
          + LunarMediaDL thin wrapper routes (/metadata, /download, /queue, …)
Player  : tv_embedded + android (bypasses iOS cookie restriction + PO Token req)
Cookies : Read from YTDL_COOKIES env var at startup (Railway-native)
Cleanup : Old files deleted every 30 min (configurable FILE_CLEANUP_HOURS)
"""

# ─── stdlib ──────────────────────────────────────────────────────────────────
import os, sys, asyncio, json, logging, pathlib, re, socket, ssl, time, uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# ─── third-party ─────────────────────────────────────────────────────────────
from aiohttp import web
from aiohttp.log import access_logger
import socketio
from watchfiles import DefaultFilter, Change, awatch

# ─── MeTube engine (unchanged originals) ─────────────────────────────────────
from ytdl import DownloadQueueNotifier, DownloadQueue, Download
from subscriptions import (
    SubscriptionManager, SubscriptionNotifier,
    SubscriptionInfo, coerce_optional_bool,
)
from yt_dlp.version import __version__ as yt_dlp_version

# ─── Resolve static asset directory ──────────────────────────────────────────
_SCRIPT_DIR  = Path(__file__).resolve().parent   # …/app/
_PROJECT_DIR = _SCRIPT_DIR.parent                # project root
_STATIC_DIR  = str(_PROJECT_DIR / "static")      # …/static/

log = logging.getLogger("main")

# ─── Logging ─────────────────────────────────────────────────────────────────
def parseLogLevel(v):
    return getattr(logging, str(v).upper(), None) if isinstance(v, str) else None

if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=parseLogLevel(os.environ.get("LOGLEVEL", "INFO")) or logging.INFO
    )

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (MeTube original + Railway / LunarMediaDL additions)
# ══════════════════════════════════════════════════════════════════════════════
class Config:
    _DEFAULTS = {
        "DOWNLOAD_DIR":               "/app/downloads",
        "AUDIO_DOWNLOAD_DIR":         "%%DOWNLOAD_DIR",
        "TEMP_DIR":                   "%%DOWNLOAD_DIR",
        "DOWNLOAD_DIRS_INDEXABLE":    "false",
        "CUSTOM_DIRS":                "true",
        "CREATE_CUSTOM_DIRS":         "true",
        "CUSTOM_DIRS_EXCLUDE_REGEX":  r"(^|/)[.@].*$",
        "DELETE_FILE_ON_TRASHCAN":    "false",
        "STATE_DIR":                  "/app/state",
        "URL_PREFIX":                 "/",
        "PUBLIC_HOST_URL":            "download/",
        "PUBLIC_HOST_AUDIO_URL":      "audio_download/",
        "OUTPUT_TEMPLATE":            "%(title)s.%(ext)s",
        "OUTPUT_TEMPLATE_CHAPTER":    "%(title)s - %(section_number)02d - %(section_title)s.%(ext)s",
        "OUTPUT_TEMPLATE_PLAYLIST":   "%(playlist_title)s/%(title)s.%(ext)s",
        "OUTPUT_TEMPLATE_CHANNEL":    "%(channel)s/%(title)s.%(ext)s",
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
        "ROBOTS_TXT":                 "",
        "HOST":                       "0.0.0.0",
        "PORT":                       os.environ.get("PORT", "8080"),
        "HTTPS":                      "false",
        "CERTFILE":                   "",
        "KEYFILE":                    "",
        "BASE_DIR":                   "",
        "DEFAULT_THEME":              "auto",
        "MAX_CONCURRENT_DOWNLOADS":   "3",
        "LOGLEVEL":                   "INFO",
        "ENABLE_ACCESSLOG":           "false",
        # LunarMediaDL additions
        "FILE_CLEANUP_HOURS":         "3",
    }

    _BOOLEAN = (
        "DOWNLOAD_DIRS_INDEXABLE", "CUSTOM_DIRS", "CREATE_CUSTOM_DIRS",
        "DELETE_FILE_ON_TRASHCAN", "HTTPS", "ENABLE_ACCESSLOG",
        "ALLOW_YTDL_OPTIONS_OVERRIDES",
    )

    # ── YouTube player clients that work reliably in Railway (server env) ────
    # tv_embedded : no signature solving needed, works with cookies
    # android_vr  : no PO token needed, works with cookies
    # android     : reliable fallback, no PO token
    # web         : last resort (needs Node.js for signature solving)
    # NOTE: "ios" is intentionally NOT here — it is skipped when cookies
    #       are active, causing silent fallthrough and "No formats found".
    _YT_SAFE_CLIENTS = ["tv_embedded", "android_vr", "android", "web"]

    def __init__(self):
        for k, v in self._DEFAULTS.items():
            setattr(self, k, os.environ.get(k, v))

        # Resolve %%-references
        for k, v in self.__dict__.items():
            if isinstance(v, str) and v.startswith("%%"):
                setattr(self, k, getattr(self, v[2:]))
            if k in self._BOOLEAN:
                if v not in ("true","false","True","False","on","off","1","0"):
                    log.error(f'Env "{k}" has non-boolean value "{v}"'); sys.exit(1)
                setattr(self, k, v in ("true","True","on","1"))

        if not self.URL_PREFIX.endswith("/"):
            self.URL_PREFIX += "/"
        for attr in ("PUBLIC_HOST_URL","PUBLIC_HOST_AUDIO_URL"):
            val = getattr(self, attr)
            if val and not val.endswith("/"):
                setattr(self, attr, val + "/")

        if self.YTDL_OPTIONS_FILE and self.YTDL_OPTIONS_FILE.startswith("."):
            self.YTDL_OPTIONS_FILE = str(Path(self.YTDL_OPTIONS_FILE).resolve())
        if self.YTDL_OPTIONS_PRESETS_FILE and self.YTDL_OPTIONS_PRESETS_FILE.startswith("."):
            self.YTDL_OPTIONS_PRESETS_FILE = str(Path(self.YTDL_OPTIONS_PRESETS_FILE).resolve())

        for d in (self.DOWNLOAD_DIR, self.AUDIO_DOWNLOAD_DIR,
                  self.TEMP_DIR, self.STATE_DIR):
            os.makedirs(d, exist_ok=True)

        self._runtime_overrides: dict = {}

        ok, _ = self.load_ytdl_options()
        if not ok: sys.exit(1)
        ok, _ = self.load_ytdl_option_presets()
        if not ok: sys.exit(1)

        # ── Inject safe YouTube player_client AFTER options are loaded ───────
        # Only set if the user has not already configured extractor_args.youtube
        ea = self.YTDL_OPTIONS.setdefault("extractor_args", {})
        yt = ea.setdefault("youtube", {})
        if "player_client" not in yt:
            yt["player_client"] = self._YT_SAFE_CLIENTS
        else:
            # Keep user's list but ensure no ios-only config that breaks with cookies
            existing = yt["player_client"]
            if isinstance(existing, list) and existing == ["ios"]:
                log.warning(
                    "player_client=['ios'] overridden to safe list "
                    "because ios is skipped when cookies are active."
                )
                yt["player_client"] = self._YT_SAFE_CLIENTS

        # ── Load cookies from Railway env var ────────────────────────────────
        self._load_cookies_from_env()

    def _load_cookies_from_env(self):
        """
        Read cookies from Railway environment variable (plain Netscape cookies.txt).
        Writes to STATE_DIR/cookies.txt and registers with yt-dlp via cookiefile.
        No encoding/encryption — paste raw Netscape format directly into Railway.
        """
        cookie_content = (
            os.environ.get("YTDL_COOKIES", "") or
            os.environ.get("COOKIES_TXT", "") or
            os.environ.get("YT_COOKIES", "")
        ).strip()

        cookie_path = os.path.join(self.STATE_DIR, "cookies.txt")

        if cookie_content:
            try:
                with open(cookie_path, "w", encoding="utf-8") as f:
                    f.write(cookie_content)
                # Don't use set_runtime_override here — it's not initialized yet
                self.YTDL_OPTIONS["cookiefile"] = cookie_path
                self._runtime_overrides["cookiefile"] = cookie_path
                log.info(f"Cookies loaded from env → {cookie_path}")
            except OSError as e:
                log.warning(f"Could not write cookies file: {e}")
        elif os.path.isfile(cookie_path):
            if "cookiefile" not in self._runtime_overrides:
                self.YTDL_OPTIONS["cookiefile"] = cookie_path
                self._runtime_overrides["cookiefile"] = cookie_path
                log.info(f"Existing cookies file detected: {cookie_path}")

    # ── MeTube-original option loaders (unchanged) ───────────────────────────
    def set_runtime_override(self, key, value):
        self._runtime_overrides[key] = value
        self.YTDL_OPTIONS[key] = value

    def remove_runtime_override(self, key):
        self._runtime_overrides.pop(key, None)
        self.YTDL_OPTIONS.pop(key, None)

    def _apply_runtime_overrides(self):
        self.YTDL_OPTIONS.update(self._runtime_overrides)

    _FRONTEND_KEYS = (
        "CUSTOM_DIRS", "CREATE_CUSTOM_DIRS", "OUTPUT_TEMPLATE_CHAPTER",
        "PUBLIC_HOST_URL", "PUBLIC_HOST_AUDIO_URL",
        "DEFAULT_OPTION_PLAYLIST_ITEM_LIMIT",
        "SUBSCRIPTION_DEFAULT_CHECK_INTERVAL",
        "ALLOW_YTDL_OPTIONS_OVERRIDES",
    )

    def frontend_safe(self) -> dict:
        return {k: getattr(self, k) for k in self._FRONTEND_KEYS}

    def load_ytdl_options(self) -> tuple[bool, str]:
        try:
            self.YTDL_OPTIONS = json.loads(os.environ.get("YTDL_OPTIONS", "{}"))
            assert isinstance(self.YTDL_OPTIONS, dict)
        except (json.JSONDecodeError, AssertionError):
            log.error("YTDL_OPTIONS is invalid JSON"); return (False, "invalid")

        if self.YTDL_OPTIONS_FILE:
            log.info(f'Loading yt-dlp options from "{self.YTDL_OPTIONS_FILE}"')
            if not os.path.exists(self.YTDL_OPTIONS_FILE):
                log.error(f'File "{self.YTDL_OPTIONS_FILE}" not found')
                return (False, "file not found")
            try:
                with open(self.YTDL_OPTIONS_FILE) as f:
                    opts = json.load(f)
                assert isinstance(opts, dict)
                self.YTDL_OPTIONS.update(opts)
            except (json.JSONDecodeError, AssertionError):
                log.error("YTDL_OPTIONS_FILE contents invalid"); return (False, "invalid")

        self._apply_runtime_overrides()
        return (True, "")

    def load_ytdl_option_presets(self) -> tuple[bool, str]:
        try:
            self.YTDL_OPTIONS_PRESETS = json.loads(
                os.environ.get("YTDL_OPTIONS_PRESETS", "{}"))
            assert isinstance(self.YTDL_OPTIONS_PRESETS, dict)
        except (json.JSONDecodeError, AssertionError):
            log.error("YTDL_OPTIONS_PRESETS invalid"); return (False, "invalid")

        if self.YTDL_OPTIONS_PRESETS_FILE:
            if not os.path.exists(self.YTDL_OPTIONS_PRESETS_FILE):
                log.error(f'Presets file not found'); return (False, "file not found")
            try:
                with open(self.YTDL_OPTIONS_PRESETS_FILE) as f:
                    opts = json.load(f)
                assert isinstance(opts, dict)
                self.YTDL_OPTIONS_PRESETS.update(opts)
            except (json.JSONDecodeError, AssertionError):
                log.error("YTDL_OPTIONS_PRESETS_FILE invalid"); return (False, "invalid")
        return (True, "")


config = Config()
logging.getLogger().setLevel(parseLogLevel(str(config.LOGLEVEL)) or logging.INFO)

# ══════════════════════════════════════════════════════════════════════════════
#  AIOHTTP + SOCKET.IO  (MeTube original setup)
# ══════════════════════════════════════════════════════════════════════════════
class ObjectSerializer(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__dict__"): return obj.__dict__
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            try: return list(obj)
            except Exception: pass
        return json.JSONEncoder.default(self, obj)

serializer = ObjectSerializer()
app = web.Application(client_max_size=10 * 1024 * 1024)

_cors_origins = [
    o.strip() for o in config.CORS_ALLOWED_ORIGINS.split(",") if o.strip()
] if config.CORS_ALLOWED_ORIGINS else []

sio = socketio.AsyncServer(
    cors_allowed_origins=_cors_origins if _cors_origins else "*",
    async_mode="aiohttp",
    logger=False, engineio_logger=False,
)
sio.attach(app, socketio_path="socket.io")
routes = web.RouteTableDef()

# ── Validation constants (MeTube originals) ────────────────────────────────
VALID_SUBTITLE_FORMATS  = {"srt","txt","vtt","ttml","sbv","scc","dfxp"}
VALID_SUBTITLE_MODES    = {"auto_only","manual_only","prefer_manual","prefer_auto"}
SUBTITLE_LANGUAGE_RE    = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,34}$")
VALID_DOWNLOAD_TYPES    = {"video","audio","captions","thumbnail"}
VALID_VIDEO_CODECS      = {"auto","h264","h265","av1","vp9"}
VALID_VIDEO_FORMATS     = {"any","mp4","ios"}
VALID_AUDIO_FORMATS     = {"m4a","mp3","opus","wav","flac"}
VALID_THUMBNAIL_FORMATS = {"jpg"}

# ══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS  (MeTube originals — not changed)
# ══════════════════════════════════════════════════════════════════════════════
def _parse_ytdl_options_overrides(value, *, enabled: bool) -> dict:
    if value is None or value == "": return {}
    if isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(reason="ytdl_options_overrides must be valid JSON") from exc
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(reason="ytdl_options_overrides must be a JSON object")
    if value and not enabled:
        raise web.HTTPBadRequest(reason="ytdl_options_overrides are disabled")
    return value

_YOUTUBE_T_COMPACT_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)(?:s)?)?$", re.IGNORECASE)

def _parse_youtube_t_compact(value: str) -> float | None:
    v = value.strip()
    if not v: return None
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        sec = float(v); return sec if sec >= 0 else None
    m = _YOUTUBE_T_COMPACT_RE.match(v)
    if m and any(m.groups()):
        return float(int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0))
    return None

def _parse_clock_timestamp(s: str) -> float:
    part = s.strip()
    if not part: raise ValueError("empty timestamp")
    segs = part.split(":")
    if len(segs) > 3: raise ValueError("too many segments")
    nums = [float(x) for x in segs]
    if any(x < 0 for x in nums): raise ValueError("negative segment")
    if len(segs) == 1: return nums[0]
    if len(segs) == 2: return nums[0]*60 + nums[1]
    return nums[0]*3600 + nums[1]*60 + nums[2]

def _parse_clip_timestamp_value(value) -> float:
    if isinstance(value, bool): raise web.HTTPBadRequest(reason="clip timestamp must be a number or string")
    if isinstance(value, (int, float)):
        if value < 0: raise web.HTTPBadRequest(reason="clip timestamp must be non-negative")
        return float(value)
    s = str(value).strip()
    if not s: raise web.HTTPBadRequest(reason="clip timestamp cannot be empty")
    if ":" in s:
        try: return _parse_clock_timestamp(s)
        except ValueError as exc: raise web.HTTPBadRequest(reason="invalid clip timestamp format") from exc
    compact = _parse_youtube_t_compact(s)
    if compact is not None: return compact
    raise web.HTTPBadRequest(reason="invalid clip timestamp format")

def _optional_clip_field(raw) -> float | None:
    if raw is None: return None
    if isinstance(raw, str) and not raw.strip(): return None
    return _parse_clip_timestamp_value(raw)

def _clip_field_provided_in_post(raw) -> bool:
    if raw is None: return False
    if isinstance(raw, str) and not raw.strip(): return False
    return True

def _extract_t_query_from_url(url: str) -> tuple[str, float | None]:
    try:
        parsed = urlparse(url); params = parse_qs(parsed.query)
    except Exception: return url, None
    t_values = params.get("t")
    if not t_values: return url, None
    start = _parse_youtube_t_compact(t_values[0])
    if start is None: return url, None
    filtered  = {k: v for k, v in params.items() if k != "t"}
    new_query = urlencode(filtered, doseq=True)
    cleaned   = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                            parsed.params, new_query, parsed.fragment))
    return cleaned, float(start)

def _parse_ytdl_options_presets(post: dict) -> list[str]:
    raw = post.get("ytdl_options_presets") or post.get("ytdl_options_preset")
    if raw is None: return []
    if isinstance(raw, list): return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str): s = raw.strip(); return [s] if s else []
    raise web.HTTPBadRequest(reason="ytdl_options_presets must be a JSON array of strings")

def _migrate_legacy_request(post: dict) -> dict:
    if "download_type" in post: return post
    old_format     = str(post.get("format") or "any").strip().lower()
    old_quality    = str(post.get("quality") or "best").strip().lower()
    old_video_codec= str(post.get("video_codec") or "auto").strip().lower()
    if old_format in VALID_AUDIO_FORMATS:
        post.update(download_type="audio", codec="auto", format=old_format)
    elif old_format == "thumbnail":
        post.update(download_type="thumbnail", codec="auto", format="jpg", quality="best")
    elif old_format == "captions":
        post.update(download_type="captions", codec="auto",
                    format=str(post.get("subtitle_format") or "srt").strip().lower(),
                    quality="best")
    else:
        post["download_type"] = "video"; post["codec"] = old_video_codec
        if old_quality == "best_ios":
            post.update(format="ios", quality="best")
        elif old_quality == "audio":
            post.update(download_type="audio", codec="auto", format="m4a", quality="best")
        else:
            post.update(format=old_format, quality=old_quality)
    return post

async def _read_json_request(request: web.Request) -> dict:
    try: post = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(reason="Invalid JSON request body") from exc
    if not isinstance(post, dict):
        raise web.HTTPBadRequest(reason="JSON request body must be an object")
    return post

def parse_download_options(post: dict) -> dict:
    """MeTube-original validation — unchanged."""
    post = _migrate_legacy_request(dict(post))
    url           = post.get("url")
    download_type = post.get("download_type")
    codec         = post.get("codec")
    format_       = post.get("format")
    quality       = post.get("quality")
    if not url or not quality or not download_type:
        raise web.HTTPBadRequest(reason="missing 'url', 'download_type', or 'quality'")
    url = str(url).strip()
    folder             = post.get("folder")
    custom_name_prefix = post.get("custom_name_prefix") or ""
    playlist_item_limit= post.get("playlist_item_limit")
    auto_start         = post.get("auto_start")
    split_by_chapters  = post.get("split_by_chapters")
    chapter_template   = post.get("chapter_template")
    subtitle_language  = post.get("subtitle_language")
    subtitle_mode      = post.get("subtitle_mode")
    ytdl_options_overrides = post.get("ytdl_options_overrides")

    if custom_name_prefix and (".." in custom_name_prefix
                               or custom_name_prefix.startswith("/")
                               or custom_name_prefix.startswith("\\")):
        raise web.HTTPBadRequest(reason='custom_name_prefix must not contain ".." or start with a path separator')
    if auto_start is None:         auto_start = True
    if playlist_item_limit is None: playlist_item_limit = config.DEFAULT_OPTION_PLAYLIST_ITEM_LIMIT
    if split_by_chapters is None:  split_by_chapters = False
    if chapter_template is None:   chapter_template = config.OUTPUT_TEMPLATE_CHAPTER
    if subtitle_language is None:  subtitle_language = "en"
    if subtitle_mode is None:      subtitle_mode = "prefer_manual"

    download_type    = str(download_type).strip().lower()
    codec            = str(codec or "auto").strip().lower()
    format_          = str(format_ or "").strip().lower()
    quality          = str(quality).strip().lower()
    subtitle_language= str(subtitle_language).strip()
    subtitle_mode    = str(subtitle_mode).strip()
    ytdl_options_presets   = _parse_ytdl_options_presets(post)
    ytdl_options_overrides = _parse_ytdl_options_overrides(
        ytdl_options_overrides, enabled=config.ALLOW_YTDL_OPTIONS_OVERRIDES)

    if chapter_template and (".." in chapter_template
                             or chapter_template.startswith("/")
                             or chapter_template.startswith("\\")):
        raise web.HTTPBadRequest(reason='chapter_template must not contain ".."')
    if not SUBTITLE_LANGUAGE_RE.fullmatch(subtitle_language):
        raise web.HTTPBadRequest(reason="invalid subtitle_language")
    if subtitle_mode not in VALID_SUBTITLE_MODES:
        raise web.HTTPBadRequest(reason=f"subtitle_mode must be one of {sorted(VALID_SUBTITLE_MODES)}")
    for pn in ytdl_options_presets:
        if pn not in config.YTDL_OPTIONS_PRESETS:
            raise web.HTTPBadRequest(reason="unknown preset name")
    if download_type not in VALID_DOWNLOAD_TYPES:
        raise web.HTTPBadRequest(reason=f"download_type must be one of {sorted(VALID_DOWNLOAD_TYPES)}")
    if codec not in VALID_VIDEO_CODECS:
        raise web.HTTPBadRequest(reason=f"codec must be one of {sorted(VALID_VIDEO_CODECS)}")

    if download_type == "video":
        if format_ not in VALID_VIDEO_FORMATS:
            raise web.HTTPBadRequest(reason=f"format must be one of {sorted(VALID_VIDEO_FORMATS)} for video")
        if quality not in {"best","worst","2160","1440","1080","720","480","360","240"}:
            raise web.HTTPBadRequest(reason="invalid quality for video")
    elif download_type == "audio":
        if format_ not in VALID_AUDIO_FORMATS:
            raise web.HTTPBadRequest(reason=f"format must be one of {sorted(VALID_AUDIO_FORMATS)} for audio")
        allowed_q = {"best"}
        if format_ == "mp3": allowed_q |= {"320","192","128"}
        elif format_ == "m4a": allowed_q |= {"192","128"}
        if quality not in allowed_q:
            raise web.HTTPBadRequest(reason=f"invalid quality for {format_}")
        codec = "auto"
    elif download_type == "captions":
        if format_ not in VALID_SUBTITLE_FORMATS:
            raise web.HTTPBadRequest(reason=f"format must be one of {sorted(VALID_SUBTITLE_FORMATS)} for captions")
        quality = "best"; codec = "auto"
    elif download_type == "thumbnail":
        if format_ not in VALID_THUMBNAIL_FORMATS:
            raise web.HTTPBadRequest(reason=f"format must be one of {sorted(VALID_THUMBNAIL_FORMATS)} for thumbnail")
        quality = "best"; codec = "auto"

    try: playlist_item_limit = int(playlist_item_limit)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(reason="playlist_item_limit must be an integer") from exc

    clip_start_raw = post.get("clip_start")
    clip_end_raw   = post.get("clip_end")
    if download_type in ("captions", "thumbnail"):
        if _clip_field_provided_in_post(clip_start_raw) or _clip_field_provided_in_post(clip_end_raw):
            raise web.HTTPBadRequest(reason="clip_start/clip_end not supported for captions/thumbnail")
        clip_start = clip_end = None
    else:
        cleaned_url, url_t = _extract_t_query_from_url(url)
        if url_t is not None: url = cleaned_url
        explicit_start          = _optional_clip_field(clip_start_raw)
        explicit_end            = _optional_clip_field(clip_end_raw)
        explicit_start_provided = _clip_field_provided_in_post(clip_start_raw)
        explicit_end_provided   = _clip_field_provided_in_post(clip_end_raw)
        if explicit_start_provided: clip_start = explicit_start
        elif explicit_end_provided: clip_start = 0.0
        elif url_t is not None:     clip_start = url_t
        else:                        clip_start = None
        clip_end = explicit_end
        if clip_end is not None and clip_start is None: clip_start = 0.0
        if clip_start is not None and clip_end is not None and clip_end <= clip_start:
            raise web.HTTPBadRequest(reason="clip_end must be greater than clip_start")

    return {
        "url": url, "download_type": download_type, "codec": codec,
        "format": format_, "quality": quality, "folder": folder,
        "custom_name_prefix": custom_name_prefix,
        "playlist_item_limit": playlist_item_limit,
        "auto_start": auto_start, "split_by_chapters": split_by_chapters,
        "chapter_template": chapter_template,
        "subtitle_language": subtitle_language, "subtitle_mode": subtitle_mode,
        "ytdl_options_presets": ytdl_options_presets,
        "ytdl_options_overrides": ytdl_options_overrides,
        "clip_start": clip_start, "clip_end": clip_end,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  NOTIFIERS  (MeTube originals)
# ══════════════════════════════════════════════════════════════════════════════
class Notifier(DownloadQueueNotifier):
    async def added(self, dl):
        await sio.emit("added",     serializer.encode(dl))
        jid = _jid_of(dl.url)
        if jid: await sio.emit("lunar_added", serializer.encode(_lunar_progress(dl, jid)))

    async def updated(self, dl):
        await sio.emit("updated",   serializer.encode(dl))
        jid = _jid_of(dl.url)
        if jid: await sio.emit("lunar_progress", serializer.encode(_lunar_progress(dl, jid)))

    async def completed(self, dl):
        await sio.emit("completed", serializer.encode(dl))
        jid = _jid_of(dl.url)
        if jid:
            p = _lunar_progress(dl, jid)
            p["file_url"] = f"/download/file/{jid}"
            await sio.emit("lunar_completed", serializer.encode(p))

    async def canceled(self, id_):
        await sio.emit("canceled",  serializer.encode(id_))
        jid = _jid_of(id_)
        if jid: await sio.emit("lunar_canceled", serializer.encode({"job_id": jid}))

    async def cleared(self, id_):
        await sio.emit("cleared",   serializer.encode(id_))


class MetubeSubscriptionNotifier(SubscriptionNotifier):
    async def subscription_added(self, sub: SubscriptionInfo):
        await sio.emit("subscription_added",  serializer.encode(sub.to_public_dict()))
    async def subscription_updated(self, sub: SubscriptionInfo):
        await sio.emit("subscription_updated",serializer.encode(sub.to_public_dict()))
    async def subscription_removed(self, sub_id: str):
        await sio.emit("subscription_removed",serializer.encode(sub_id))
    async def subscriptions_all(self, subs: list[SubscriptionInfo]):
        await sio.emit("subscriptions_all",   serializer.encode([s.to_public_dict() for s in subs]))


dqueue = DownloadQueue(config, Notifier())
submgr = SubscriptionManager(config, dqueue, MetubeSubscriptionNotifier())

async def _startup(app):
    await dqueue.initialize()
    submgr.start_background_loop()

async def _cleanup(app):
    Download.shutdown_manager()
    submgr.close()

app.on_startup.append(_startup)
app.on_cleanup.append(_cleanup)

# ── Watch YTDL_OPTIONS_FILE (MeTube original) ─────────────────────────────
class FileOpsFilter(DefaultFilter):
    def __call__(self, change_type: int, path: str) -> bool:
        if path != config.YTDL_OPTIONS_FILE: return False
        if os.path.exists(config.YTDL_OPTIONS_FILE):
            try:
                if not os.path.samefile(path, config.YTDL_OPTIONS_FILE): return False
            except (OSError, IOError):
                if path != config.YTDL_OPTIONS_FILE: return False
        return change_type in (Change.modified, Change.added, Change.deleted)

def get_options_update_time(success=True, msg=""):
    result = {"success": success, "msg": msg, "update_time": None}
    if config.YTDL_OPTIONS_FILE and os.path.exists(config.YTDL_OPTIONS_FILE):
        try: result["update_time"] = os.path.getmtime(config.YTDL_OPTIONS_FILE)
        except (OSError, IOError): pass
    return result

async def watch_files():
    async def _watch():
        async for _ in awatch(config.YTDL_OPTIONS_FILE, watch_filter=FileOpsFilter()):
            success, msg = config.load_ytdl_options()
            await sio.emit("ytdl_options_changed", serializer.encode(get_options_update_time(success, msg)))
    log.info(f"Watching: {config.YTDL_OPTIONS_FILE}")
    asyncio.create_task(_watch())

if config.YTDL_OPTIONS_FILE:
    app.on_startup.append(lambda app: watch_files())

# ══════════════════════════════════════════════════════════════════════════════
#  JOB REGISTRY  (LunarMediaDL: UUID ↔ URL mapping)
# ══════════════════════════════════════════════════════════════════════════════
_job_by_id: dict[str, str] = {}
_id_by_url: dict[str, str] = {}
_jlock = asyncio.Lock()

async def _register_job(url: str) -> str:
    async with _jlock:
        if url in _id_by_url: return _id_by_url[url]
        jid = str(uuid.uuid4())
        _job_by_id[jid] = url; _id_by_url[url] = jid
        return jid

def _url_of(jid: str) -> str | None: return _job_by_id.get(jid)
def _jid_of(url: str) -> str | None: return _id_by_url.get(url)

def _lunar_progress(dl, jid: str) -> dict:
    """Convert MeTube DownloadInfo → LunarMediaDL progress payload."""
    pct = 0.0
    raw = getattr(dl, "percent", None)
    if raw is not None:
        try: pct = float(str(raw).replace("%","").strip())
        except (ValueError, TypeError): pct = 0.0

    speed_raw = getattr(dl, "speed", None)
    speed_str = ""
    if speed_raw:
        try:
            sp = float(speed_raw)
            if sp >= 1_048_576:  speed_str = f"{sp/1_048_576:.1f} MB/s"
            elif sp >= 1_024:    speed_str = f"{sp/1_024:.1f} KB/s"
            else:                speed_str = f"{sp:.0f} B/s"
        except (ValueError, TypeError): speed_str = str(speed_raw)

    eta_raw = getattr(dl, "eta", None)
    eta_str = ""
    if eta_raw is not None:
        try:
            s = int(eta_raw)
            if s >= 3600:   eta_str = f"ETA {s//3600}h {(s%3600)//60}m"
            elif s >= 60:   eta_str = f"ETA {s//60}m {s%60}s"
            else:           eta_str = f"ETA {s}s"
        except (ValueError, TypeError): pass

    entry = getattr(dl, "entry", None)
    thumb = (entry.get("thumbnail","") if isinstance(entry, dict) else "")

    return {
        "job_id":    jid,
        "url":       getattr(dl, "url",      ""),
        "title":     getattr(dl, "title",    ""),
        "status":    getattr(dl, "status",   "pending"),
        "progress":  pct,
        "speed":     speed_str,
        "eta":       eta_str,
        "filename":  getattr(dl, "filename", "") or "",
        "error":     getattr(dl, "error",    "") or "",
        "thumbnail": thumb,
        "file_url":  "",
    }

# ══════════════════════════════════════════════════════════════════════════════
#  SOCKET.IO CONNECT  (MeTube original + LunarMediaDL state sync)
# ══════════════════════════════════════════════════════════════════════════════
@sio.event
async def connect(sid, environ):
    log.info(f"Client connected: {sid}")
    # MeTube originals
    await sio.emit("all",               serializer.encode(dqueue.get()),               to=sid)
    await sio.emit("subscriptions_all", serializer.encode([s.to_public_dict() for s in submgr.list_all()]), to=sid)
    await sio.emit("configuration",     serializer.encode(config.frontend_safe()),     to=sid)
    if config.CUSTOM_DIRS:
        await sio.emit("custom_dirs",   serializer.encode(get_custom_dirs()),          to=sid)
    if config.YTDL_OPTIONS_FILE:
        await sio.emit("ytdl_options_changed", serializer.encode(get_options_update_time()), to=sid)
    # LunarMediaDL: send full state in lunar format
    q, done = dqueue.get()
    items = []
    for url, dl in list(q) + list(done):
        jid = _jid_of(url) or url
        p = _lunar_progress(dl, jid)
        if getattr(dl, "status","") in ("finished","completed"):
            p["file_url"] = f"/download/file/{jid}"
        items.append(p)
    await sio.emit("lunar_state", serializer.encode({"items": items}), to=sid)

# ══════════════════════════════════════════════════════════════════════════════
#  METUBE ORIGINAL ROUTES  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════
COOKIES_PATH = os.path.join(config.STATE_DIR, "cookies.txt")

@routes.post("/add")
async def add(request):
    post = await _read_json_request(request)
    try: o = parse_download_options(post)
    except web.HTTPBadRequest as e: log.error("Bad request: %s", e.reason); raise
    status = await dqueue.add(
        o["url"], o["download_type"], o["codec"], o["format"], o["quality"],
        o["folder"], o["custom_name_prefix"], o["playlist_item_limit"],
        o["auto_start"], o["split_by_chapters"], o["chapter_template"],
        o["subtitle_language"], o["subtitle_mode"],
        o["ytdl_options_presets"], o["ytdl_options_overrides"],
        o["clip_start"], o["clip_end"],
    )
    return web.Response(text=serializer.encode(status))

@routes.get("/presets")
async def presets(request):
    return web.Response(
        text=serializer.encode({"presets": sorted(config.YTDL_OPTIONS_PRESETS.keys())}),
        content_type="application/json")

@routes.post("/cancel-add")
async def cancel_add(request):
    dqueue.cancel_add()
    return web.Response(text=serializer.encode({"status":"ok"}), content_type="application/json")

@routes.post("/subscribe")
async def subscribe(request):
    post = await _read_json_request(request)
    o = parse_download_options(post)
    cic = post.get("check_interval_minutes", config.SUBSCRIPTION_DEFAULT_CHECK_INTERVAL)
    try: cic = int(cic)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(reason="check_interval_minutes must be an integer") from exc
    if cic < 1: raise web.HTTPBadRequest(reason="check_interval_minutes must be at least 1")
    if o.get("clip_start") or o.get("clip_end"):
        raise web.HTTPBadRequest(reason="clip options not supported for subscriptions")
    try:
        skip = coerce_optional_bool(post.get("skip_subscriber_only"), default=False,
                                    field_name="skip_subscriber_only")
    except ValueError as exc:
        raise web.HTTPBadRequest(reason=str(exc)) from exc
    result = await submgr.add_subscription(
        o["url"], check_interval_minutes=cic,
        download_type=o["download_type"], codec=o["codec"],
        format=o["format"], quality=o["quality"],
        folder=o["folder"] or "", custom_name_prefix=o["custom_name_prefix"],
        auto_start=o["auto_start"], playlist_item_limit=o["playlist_item_limit"],
        split_by_chapters=o["split_by_chapters"], chapter_template=o["chapter_template"],
        subtitle_language=o["subtitle_language"], subtitle_mode=o["subtitle_mode"],
        ytdl_options_presets=o["ytdl_options_presets"],
        ytdl_options_overrides=o["ytdl_options_overrides"],
        title_regex=post.get("title_regex"), skip_subscriber_only=skip,
    )
    return web.Response(text=serializer.encode(result))

@routes.get("/subscriptions")
async def subscriptions_list(request):
    return web.Response(text=serializer.encode([s.to_public_dict() for s in submgr.list_all()]))

@routes.post("/subscriptions/update")
async def subscriptions_update(request):
    post = await _read_json_request(request)
    sub_id = post.get("id")
    if not sub_id: raise web.HTTPBadRequest(reason="missing subscription id")
    changes = {k: v for k, v in post.items()
               if k != "id" and k in ("enabled","check_interval_minutes","name","title_regex","skip_subscriber_only")}
    if not changes: raise web.HTTPBadRequest(reason="no valid fields to update")
    result = await submgr.update_subscription(str(sub_id), changes)
    return web.Response(text=serializer.encode(result))

@routes.post("/subscriptions/delete")
async def subscriptions_delete(request):
    post = await _read_json_request(request)
    ids = post.get("ids")
    if not ids or not isinstance(ids, list): raise web.HTTPBadRequest(reason="missing ids list")
    result = await submgr.delete_subscriptions([str(i) for i in ids])
    return web.Response(text=serializer.encode(result))

@routes.post("/subscriptions/check")
async def subscriptions_check(request):
    post = await _read_json_request(request)
    ids = post.get("ids")
    if ids is not None and not isinstance(ids, list):
        raise web.HTTPBadRequest(reason="ids must be a list")
    result = await submgr.check_now([str(i) for i in ids] if ids else None)
    return web.Response(text=serializer.encode(result))

@routes.post("/delete")
async def delete(request):
    post = await _read_json_request(request)
    ids   = post.get("ids")
    where = post.get("where")
    if not ids or where not in ("queue","done"): raise web.HTTPBadRequest()
    status = await (dqueue.cancel(ids) if where == "queue" else dqueue.clear(ids))
    return web.Response(text=serializer.encode(status))

@routes.post("/start")
async def start(request):
    post = await _read_json_request(request)
    ids  = post.get("ids")
    status = await dqueue.start_pending(ids)
    return web.Response(text=serializer.encode(status))

@routes.post("/upload-cookies")
async def upload_cookies(request):
    reader = await request.multipart()
    field  = await reader.next()
    if field is None or field.name != "cookies":
        return web.Response(status=400, text=serializer.encode({"status":"error","msg":"No cookies file"}))
    max_size = 1_000_000; size = 0; content = bytearray()
    while True:
        chunk = await field.read_chunk()
        if not chunk: break
        size += len(chunk)
        if size > max_size:
            return web.Response(status=400, text=serializer.encode({"status":"error","msg":"Cookie file too large"}))
        content.extend(chunk)
    tmp = f"{COOKIES_PATH}.tmp"
    with open(tmp, "wb") as f: f.write(content)
    os.replace(tmp, COOKIES_PATH)
    config.set_runtime_override("cookiefile", COOKIES_PATH)
    log.info(f"Cookies uploaded ({size} bytes)")
    return web.Response(text=serializer.encode({"status":"ok","msg":f"Cookies uploaded ({size} bytes)"}))

@routes.post("/delete-cookies")
async def delete_cookies(request):
    has_uploaded = os.path.exists(COOKIES_PATH)
    configured   = config.YTDL_OPTIONS.get("cookiefile")
    has_manual   = isinstance(configured, str) and configured and configured != COOKIES_PATH
    if not has_uploaded:
        if has_manual:
            return web.Response(status=400, text=serializer.encode(
                {"status":"error","msg":"Cookies configured via YTDL_OPTIONS; remove manually"}))
        return web.Response(status=400, text=serializer.encode(
            {"status":"error","msg":"No uploaded cookies to delete"}))
    os.remove(COOKIES_PATH)
    config.remove_runtime_override("cookiefile")
    config.load_ytdl_options()
    log.info("Cookies file deleted")
    return web.Response(text=serializer.encode({"status":"ok"}))

@routes.get("/cookie-status")
async def cookie_status(request):
    cookiefile = config.YTDL_OPTIONS.get("cookiefile")
    has_cookies = isinstance(cookiefile, str) and os.path.exists(cookiefile)
    return web.Response(text=serializer.encode({"status":"ok","has_cookies":has_cookies}))

@routes.get("/history")
async def history_metube(request):
    h = {"done":[], "queue":[], "pending":[]}
    for _, v in dqueue.queue.saved_items():   h["queue"].append(v)
    for _, v in dqueue.done.saved_items():    h["done"].append(v)
    for _, v in dqueue.pending.saved_items(): h["pending"].append(v)
    return web.Response(text=serializer.encode(h))

@routes.get("/version")
async def version(request):
    return web.json_response({
        "yt-dlp": yt_dlp_version,
        "app":    "LunarMediaDL",
        "version": os.getenv("APP_VERSION","1.0.0"),
    })

@routes.get("/health")
async def health(request):
    return web.json_response({"status":"ok","yt_dlp": yt_dlp_version})

def get_custom_dirs():
    cache_ttl = 5
    try: now = asyncio.get_running_loop().time()
    except RuntimeError: now = 0
    key = (config.DOWNLOAD_DIR, config.AUDIO_DOWNLOAD_DIR, config.CUSTOM_DIRS_EXCLUDE_REGEX)
    if (hasattr(get_custom_dirs,"_ck") and get_custom_dirs._ck == key
            and (now - getattr(get_custom_dirs,"_ct",0)) < cache_ttl):
        return get_custom_dirs._cv
    def rdirs(base):
        p = pathlib.Path(base)
        def conv(x):
            s = str(x)
            if s.startswith(base): s = s[len(base):]
            return s.lstrip("/")
        def incl(d):
            return not config.CUSTOM_DIRS_EXCLUDE_REGEX or re.search(config.CUSTOM_DIRS_EXCLUDE_REGEX, d) is None
        dirs = list(filter(incl, map(conv, p.glob("**/"))))
        if "" not in dirs: dirs.insert(0,"")
        return dirs
    dd = rdirs(config.DOWNLOAD_DIR)
    ad = dd if config.DOWNLOAD_DIR == config.AUDIO_DOWNLOAD_DIR else rdirs(config.AUDIO_DOWNLOAD_DIR)
    result = {"download_dir": dd, "audio_download_dir": ad}
    get_custom_dirs._ck = key; get_custom_dirs._ct = now; get_custom_dirs._cv = result
    return result

# ══════════════════════════════════════════════════════════════════════════════
#  LUNARMEDIADL WRAPPER ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@routes.post("/metadata")
async def metadata(request):
    """
    Extract video metadata WITHOUT downloading.
    Uses yt-dlp Python API (no shell/subprocess).
    Inherits config.YTDL_OPTIONS including extractor_args + cookiefile.
    """
    import yt_dlp as _yt_dlp
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")
    url = str(body.get("url","")).strip()
    if not url: raise web.HTTPBadRequest(reason="Missing 'url'")
    log.info(f"Extracting metadata: {url}")

    def _extract():
        opts = dict(config.YTDL_OPTIONS)
        opts.update({
            "quiet":                   True,
            "no_color":                True,
            "noplaylist":              not body.get("playlist", False),
            "ignore_no_formats_error": True,
            "socket_timeout":          30,
        })
        if body.get("playlist"):
            opts["extract_flat"] = "in_playlist"
        else:
            opts.pop("extract_flat", None)
        with _yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.get_running_loop().run_in_executor(None, _extract)
    except Exception as e:
        log.warning(f"Metadata error: {e}")
        raise web.HTTPBadRequest(reason=str(e))
    if not info:
        raise web.HTTPBadRequest(reason="Could not extract metadata")

    dur = info.get("duration") or 0
    dur_str = ""
    if dur:
        h, r = divmod(int(dur),3600); m, s = divmod(r,60)
        dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    return web.Response(
        text=json.dumps({
            "title":           info.get("title") or "Media",
            "thumbnail":       info.get("thumbnail") or "",
            "uploader":        info.get("uploader") or info.get("channel") or "",
            "duration":        dur,
            "duration_string": info.get("duration_string") or dur_str,
            "webpage_url":     info.get("webpage_url") or url,
            "extractor":       info.get("extractor_key") or "",
            "view_count":      info.get("view_count") or 0,
            "like_count":      info.get("like_count") or 0,
            "upload_date":     info.get("upload_date") or "",
            "description":     (info.get("description") or "")[:1000],
            "is_playlist":     info.get("_type") == "playlist",
            "playlist_count":  info.get("playlist_count") or 0,
        }),
        content_type="application/json",
    )


@routes.post("/download")
async def lunar_download(request):
    """
    LunarMediaDL thin wrapper over /add.
    Accepts simplified body, maps to MeTube parse_download_options,
    returns { job_id, status }.
    """
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")
    url = str(body.get("url","")).strip()
    if not url: raise web.HTTPBadRequest(reason="Missing 'url'")

    audio_only    = bool(body.get("audio_only", False))
    raw_type      = str(body.get("download_type","")).strip().lower()
    raw_fmt       = str(body.get("format","")).strip().lower()
    raw_audio_fmt = str(body.get("audio_format","mp3")).strip().lower()
    raw_codec     = str(body.get("codec","auto")).strip().lower()
    raw_quality   = str(body.get("quality","best")).strip().lower()

    if audio_only and not raw_type: raw_type = "audio"
    if raw_type not in VALID_DOWNLOAD_TYPES: raw_type = "audio" if audio_only else "video"

    if raw_type == "audio":
        fmt     = raw_audio_fmt if raw_audio_fmt in VALID_AUDIO_FORMATS else "mp3"
        quality = raw_quality   if raw_quality   in {"best","320","192","128"} else "best"
    elif raw_type == "video":
        fmt     = raw_fmt     if raw_fmt     in VALID_VIDEO_FORMATS else "any"
        quality = raw_quality if raw_quality in {"best","worst","2160","1440","1080","720","480","360","240"} else "best"
    elif raw_type == "captions":
        fmt = raw_fmt if raw_fmt in VALID_SUBTITLE_FORMATS else "srt"
        quality = "best"
    else:
        fmt = "jpg"; quality = "best"

    codec = raw_codec if raw_codec in VALID_VIDEO_CODECS else "auto"

    normalized = {
        "url": url, "download_type": raw_type, "codec": codec,
        "format": fmt, "quality": quality,
        "subtitle_language": str(body.get("subtitle_language","en")).strip() or "en",
        "subtitle_mode":     str(body.get("subtitle_mode","prefer_manual")).strip(),
        "split_by_chapters": bool(body.get("split_by_chapters", False)),
        "playlist_item_limit": body.get("playlist_item_limit", 0),
        "clip_start": body.get("clip_start"),
        "clip_end":   body.get("clip_end"),
        "auto_start": True,
        "custom_name_prefix": "",
        "folder": "",
        "chapter_template": config.OUTPUT_TEMPLATE_CHAPTER,
        "ytdl_options_presets":   [],
        "ytdl_options_overrides": {},
    }
    try: o = parse_download_options(normalized)
    except web.HTTPBadRequest as e:
        raise web.HTTPBadRequest(reason=e.reason)

    jid = await _register_job(o["url"])

    status = await dqueue.add(
        o["url"], o["download_type"], o["codec"], o["format"], o["quality"],
        o["folder"], o["custom_name_prefix"], o["playlist_item_limit"],
        o["auto_start"], o["split_by_chapters"], o["chapter_template"],
        o["subtitle_language"], o["subtitle_mode"],
        o["ytdl_options_presets"], o["ytdl_options_overrides"],
        o["clip_start"], o["clip_end"],
    )
    if isinstance(status, dict) and status.get("status") == "error":
        raise web.HTTPBadRequest(reason=status.get("msg","Download failed to start"))

    return web.Response(
        text=json.dumps({"job_id": jid, "status":"queued",
                         "download_type": raw_type, "format": fmt}),
        content_type="application/json",
    )


@routes.get("/queue")
async def lunar_queue(request):
    q, done = dqueue.get()
    result  = {"queue":[], "done":[]}
    for url, dl in q:
        jid = _jid_of(url) or url
        result["queue"].append(_lunar_progress(dl, jid))
    for url, dl in done:
        jid = _jid_of(url) or url
        p   = _lunar_progress(dl, jid)
        p["file_url"] = f"/download/file/{jid}"
        result["done"].append(p)
    return web.Response(text=json.dumps(result), content_type="application/json")


@routes.post("/cancel")
async def lunar_cancel(request):
    try: body = await request.json()
    except Exception: raise web.HTTPBadRequest(reason="Invalid JSON")
    jid = str(body.get("job_id","")).strip()
    if not jid: raise web.HTTPBadRequest(reason="Missing 'job_id'")
    url = _url_of(jid)
    if not url: raise web.HTTPNotFound(reason="Job not found")
    await dqueue.cancel([url])
    return web.Response(text='{"status":"ok"}', content_type="application/json")


@routes.get("/download/file/{job_id}")
async def serve_file(request):
    """Serve completed download file by UUID job_id."""
    jid = request.match_info["job_id"]
    url = _url_of(jid)
    if not url: raise web.HTTPNotFound(reason="Job not found")

    def _resolve(dl_info) -> str | None:
        fn = getattr(dl_info, "filename", None)
        if not fn: return None
        if os.path.isabs(fn) and os.path.isfile(fn): return fn
        for base in (config.DOWNLOAD_DIR, config.AUDIO_DOWNLOAD_DIR):
            cand = os.path.normpath(os.path.join(base, fn))
            if os.path.isfile(cand): return cand
        return None

    q, done = dqueue.get()
    for dl_url, dl in list(done) + list(q):
        if dl_url == url:
            path = _resolve(dl)
            if path:
                return web.FileResponse(path, headers={
                    "Content-Disposition": f'attachment; filename="{os.path.basename(path)}"'
                })
            return web.Response(status=202, text="File not ready — retry shortly")
    raise web.HTTPNotFound(reason="Job not found in queue")

# ══════════════════════════════════════════════════════════════════════════════
#  STATIC PAGES  (LunarMediaDL frontend)
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

# ── CORS middleware ────────────────────────────────────────────────────────
async def on_prepare(request, response):
    origin = request.headers.get("Origin")
    if origin:
        if not _cors_origins or "*" in _cors_origins or origin in _cors_origins:
            response.headers["Access-Control-Allow-Origin"]  = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, PUT, OPTIONS"

app.on_response_prepare.append(on_prepare)

async def _cors_ok(request):
    return web.Response(text='{"status":"ok"}', content_type="application/json")

for _p in ("/add","/cancel-add","/subscribe","/subscriptions",
           "/subscriptions/update","/subscriptions/delete","/subscriptions/check",
           "/upload-cookies","/delete-cookies",
           "/metadata","/download","/cancel","/queue"):
    app.router.add_route("OPTIONS", _p, _cors_ok)

# ── Register all routes ────────────────────────────────────────────────────
app.add_routes(routes)

# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-CLEANUP (3h default, every 30min)
# ══════════════════════════════════════════════════════════════════════════════
_CLEANUP_INTERVAL = 1800
_FILE_MAX_AGE     = int(os.environ.get("FILE_CLEANUP_HOURS","3")) * 3600

async def _cleanup_loop():
    await asyncio.sleep(120)
    while True:
        try:
            now    = time.time()
            cutoff = now - _FILE_MAX_AGE
            q, _   = dqueue.get()
            active: set[str] = set()
            for _, dl in q:
                for attr in ("filename","tmpfilename"):
                    fn = getattr(dl, attr, None)
                    if not fn: continue
                    if not os.path.isabs(fn):
                        fn = os.path.join(config.DOWNLOAD_DIR, fn)
                    active.add(os.path.realpath(fn))
            deleted = errors = 0
            for dl_dir in {os.path.realpath(config.DOWNLOAD_DIR),
                           os.path.realpath(config.AUDIO_DOWNLOAD_DIR)}:
                if not os.path.isdir(dl_dir): continue
                for entry in os.scandir(dl_dir):
                    if not entry.is_file(follow_symlinks=False): continue
                    if os.path.realpath(entry.path) in active: continue
                    try:
                        if entry.stat().st_mtime < cutoff:
                            os.remove(entry.path); deleted += 1
                    except OSError: errors += 1
            if deleted or errors:
                log.info(f"Cleanup: {deleted} deleted, {errors} errors")
        except Exception as e:
            log.warning(f"Cleanup error: {e}")
        await asyncio.sleep(_CLEANUP_INTERVAL)

async def _start_cleanup(app):
    asyncio.create_task(_cleanup_loop())

app.on_startup.append(_start_cleanup)

# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def _check_nodejs() -> str:
    import subprocess as _sp
    try:
        r = _sp.run(["node","--version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            return f"available ({r.stdout.decode().strip()}) ✓"
        return "not found — tv_embedded/android clients active as fallback"
    except (FileNotFoundError, _sp.TimeoutExpired):
        return "not found — tv_embedded/android clients active as fallback"

def supports_reuse_port():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, getattr(socket,"SO_REUSEPORT",15), 1)
        s.close(); return True
    except (AttributeError, OSError): return False

if __name__ == "__main__":
    port = int(config.PORT)
    host = config.HOST

    ea_clients = (config.YTDL_OPTIONS.get("extractor_args",{})
                                     .get("youtube",{})
                                     .get("player_client","[default]"))
    node_status = _check_nodejs()
    cookies_set = "cookiefile" in config.YTDL_OPTIONS

    log.info(f"LunarMediaDL  {host}:{port}")
    log.info(f"Download dir  : {config.DOWNLOAD_DIR}")
    log.info(f"State dir     : {config.STATE_DIR}")
    log.info(f"yt-dlp        : {yt_dlp_version}")
    log.info(f"Node.js       : {node_status}")
    log.info(f"player_client : {ea_clients}")
    log.info(f"Cookies       : {'active — ios client auto-skipped, using tv_embedded+android' if cookies_set else 'not set'}")
    log.info(f"File cleanup  : {_FILE_MAX_AGE//3600}h (check every {_CLEANUP_INTERVAL//60}min)")

    access_log = access_logger if config.ENABLE_ACCESSLOG else None
    if config.HTTPS:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(certfile=config.CERTFILE, keyfile=config.KEYFILE)
        web.run_app(app, host=host, port=port, reuse_port=supports_reuse_port(),
                    ssl_context=ssl_ctx, access_log=access_log)
    else:
        web.run_app(app, host=host, port=port, reuse_port=supports_reuse_port(),
                    access_log=access_log)
