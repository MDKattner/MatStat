from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

# helpers.py resolves cfg/vids/tmp/stats against the process cwd; pin it to the
# repo root so config/video paths work regardless of how the server is launched.
# MATSTAT_ROOT lets tests (and alternate deployments) point at an isolated tree.
# The package root (where VERSION lives) is always derived from this file, so
# MATSTAT_ROOT does not hide the version when the app runs from the store.
_package_root: Path = Path(__file__).resolve().parent.parent.parent
_project_root: Path = Path(
    os.environ.get("MATSTAT_ROOT", "") or _package_root
).resolve()
os.chdir(_project_root)


def _ReadVersion() -> str:
    """Return the shared VERSION string, or a placeholder if it is missing."""
    try:
        return (_package_root / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION: str = _ReadVersion()

from scripts import storage
from scripts.helpers import (
    AppConfig,
    GetVideoCodecs,
    Roster,
    RosterToDict,
    clips_dir,
    eval_dir,
    taged_dir,
    untaged_dir,
)
from scripts.web import audit, auth, clips, configs, pca, search, stats, tag, team_eval, transcode, trend
from scripts.web.auth import LoginRequest
from scripts.web.clips import BatchRequest, CombineRequest, FindRequest
from scripts.web.configs import AppConfigUpdate, RosterUpdate
from scripts.web.pca import PcaCompileRequest
from scripts.web.jobs import Job, JobManager, JobContext
from scripts.web.query import QuerySpec, selected_wrestlers
from scripts.web.stats import CompileRequest
from scripts.web.tag import TagRequest
from scripts.web.ws import EventHub, WebLogHandler

# ---- Paths / static frontend ----

_STATIC_DIR: Path = Path(__file__).resolve().parent / "static"
_INDEX_FILE: Path = _STATIC_DIR / "index.html"
_LOGIN_FILE: Path = _STATIC_DIR / "login.html"

# Which video directories are exposed for preview streaming.
_PREVIEW_DIRS: dict[str, Path] = {
    "untaged": untaged_dir,
    "taged": taged_dir,
    "clips": clips_dir,
}

# Where uploaded videos are saved (the untagged pool).
_UPLOAD_DIR: Path = untaged_dir

# File/name validation: names must not contain path separators, the field /
# title delimiters (":" and ","), or control characters (which would corrupt
# generated chapter data and embedded ffmpeg metadata).
_SAFE_NAME_RE: re.Pattern[str] = re.compile(r"^[^/\\:,\x00-\x1f\x7f]+$")

# HLS segment files are named seg_00000.ts, seg_00001.ts, ...
_SEGMENT_RE: re.Pattern[str] = re.compile(r"^seg_\d{5}\.ts$")


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back on garbage."""
    raw: str = os.environ.get(name, "").strip()
    try:
        value: int = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Upload guards. Limits are configurable so a deployment can match its disk
# and proxy capacity without editing code. ``MAX_REQUEST_BYTES`` is a coarse
# Content-Length gate; ``MAX_UPLOAD_BYTES`` is enforced per file while copying
# and is the number that actually protects the media pool.
_UPLOAD_MAX_BYTES: int = _env_positive_int("MATSTAT_MAX_UPLOAD_MB", 4096) * 1024 * 1024
_REQUEST_MAX_BYTES: int = _env_positive_int("MATSTAT_MAX_REQUEST_MB", 8192) * 1024 * 1024
_MIN_FREE_BYTES: int = _env_positive_int("MATSTAT_MIN_FREE_MB", 1024) * 1024 * 1024
_MAX_UPLOAD_FILES: int = _env_positive_int("MATSTAT_MAX_UPLOAD_FILES", 20)

# Only real video containers may enter the pool. Extend the allowlist with
# ``MATSTAT_ALLOWED_EXTENSIONS=".mkv,.mp4,..."`` when a deployment needs it.
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    ext.strip().lower()
    for ext in os.environ.get(
        "MATSTAT_ALLOWED_EXTENSIONS", ".mkv,.mp4,.mov,.webm,.m4v"
    ).split(",")
    if ext.strip()
)

# HSTS is only sent over HTTPS and is disabled with MATSTAT_HSTS_MAX_AGE=0.
try:
    _HSTS_MAX_AGE: int = max(
        0, int(os.environ.get("MATSTAT_HSTS_MAX_AGE", "31536000") or 0)
    )
except ValueError:
    _HSTS_MAX_AGE = 31536000

# A strict, same-origin policy. Inline styles remain allowed for Plotly (which
# injects them) and script-src is limited to same-origin files.
_CSP: str = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' blob:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Unauthenticated GETs: probes plus the login page's external script (the page
# itself is public, so its script must be loadable before a session exists).
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/healthz", "/readyz", "/static/js/login.js"}
)

# The log panel is removed from the web UI unless logging is explicitly
# enabled (the web analog of the pre-web --visible-logging flag).
_VISIBLE_LOGGING: bool = os.environ.get("MATSTAT_VISIBLE_LOGGING", "").lower() in (
    "1", "true", "yes", "on",
)

# The log toggle and log dock markup live between these comment markers so the
# index route can strip them out when logging is disabled.
_LOG_UI_MARKERS: list[tuple[str, str]] = [
    ("<!-- log-toggle:start -->", "<!-- log-toggle:end -->"),
    ("<!-- log-dock:start -->", "<!-- log-dock:end -->"),
]

# The logout button markup lives between these markers so the index route can
# strip it out when authentication is disabled.
_AUTH_UI_MARKERS: list[tuple[str, str]] = [
    ("<!-- logout-btn:start -->", "<!-- logout-btn:end -->"),
]

# ---- Shared singletons ----

hub: EventHub = EventHub()
job_manager: JobManager = JobManager(publish=hub.publish)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bind the event hub to the running loop and wire the log bridge."""
    auth.check_configured()
    hub.attach_loop(asyncio.get_running_loop())
    _attach_log_handler()
    storage.init_db()
    # uvicorn's log records stop at the "uvicorn" logger (propagate=False), so
    # shutdown diagnostics such as "Waiting for background tasks to complete"
    # never reach MatStat.log and a stalled stop looks like a silent freeze.
    # Route them to the root handlers; access logs stay terminal-only.
    logging.getLogger("uvicorn").propagate = True
    job_manager.reset()
    logging.info("MatStat web server started.")
    yield
    logging.info("MatStat web server shutting down.")
    job_manager.shutdown()


def _attach_log_handler() -> None:
    """Add the WebLogHandler to the root logger exactly once.

    Skipped entirely when logging is disabled (the pre-web behavior under
    ``--visible-logging``).
    """
    if not _VISIBLE_LOGGING:
        return
    if not any(isinstance(h, WebLogHandler) for h in logging.getLogger().handlers):
        logging.getLogger().addHandler(WebLogHandler(hub))


app: FastAPI = FastAPI(
    title="MatStat",
    version=APP_VERSION,
    description="Web frontend for MatStat wrestling film analysis.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.middleware("http")
async def _request_size_guard(request: Request, call_next) -> Response:
    """Reject oversized upload requests before multipart parsing spools them.

    A chunked request without ``Content-Length`` still gets the per-file limit
    inside the upload handler; this is the cheap first line of defense.
    """
    if request.method == "POST" and request.url.path == "/api/videos":
        declared: str = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > _REQUEST_MAX_BYTES:
            return JSONResponse(
                {
                    "detail": (
                        "Upload request exceeds the configured "
                        f"{_REQUEST_MAX_BYTES // (1024 * 1024)} MB limit"
                    )
                },
                status_code=413,
            )
    return await call_next(request)


@app.middleware("http")
async def _no_stale_cache(request: Request, call_next) -> Response:
    """Revalidate HTML/CSS/JS on every load so cached assets never go stale.

    A mixed cache (e.g. an old app.js served against a new index.html) can
    crash the SPA boot and blank every tab; forcing revalidation prevents it.
    Video streams (HLS playlists/segments) are left alone.
    """
    response: Response = await call_next(request)
    content_type: str = response.headers.get("content-type", "")
    if "video" not in content_type and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def _auth_gate(request: Request, call_next) -> Response:
    """Require a valid session for every request except public endpoints.

    Unauthenticated API calls get a JSON 401 so the SPA can react; everything
    else (pages, static assets, previews, downloads) is bounced to /login.
    WebSocket connections do not pass through HTTP middleware, so the /ws
    endpoint checks authentication itself.
    """
    if auth.enabled():
        path: str = request.url.path
        public: bool = (
            (request.method == "GET" and path in _PUBLIC_PATHS)
            or (request.method == "GET" and path == "/login")
            or (request.method == "POST" and path == "/api/auth/login")
            or (request.method == "POST" and path == "/api/auth/logout")
        )
        if not public and not auth.is_authenticated(request.cookies):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


@app.middleware("http")
async def _csrf_origin_guard(request: Request, call_next) -> Response:
    """Reject state-changing requests whose ``Origin`` disagrees with Host.

    Browsers send ``Origin`` on cross-site POST/PUT/DELETE requests, so a
    mismatch is a CSRF attempt. Missing ``Origin`` is allowed for non-browser
    clients (curl, scripts, tests); SameSite=Lax still protects cookies.
    """
    if request.method not in _SAFE_METHODS:
        origin: str = request.headers.get("origin", "")
        if origin:
            origin_host: str = urlsplit(origin).netloc.lower()
            request_host: str = request.headers.get("host", "").lower()
            if not request_host or origin_host != request_host:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected"},
                    status_code=403,
                )
    return await call_next(request)


# Defined last so it is the outermost middleware (FastAPI inserts new
# middleware at the front of the stack): headers cover redirects and the
# short-circuit 401/403/413 responses too.
@app.middleware("http")
async def _security_headers(request: Request, call_next) -> Response:
    """Attach baseline browser-hardening headers to every response.

    ``Content-Security-Policy`` is strict: scripts must be same-origin files
    (the login page's script lives in ``static/js/login.js``), while inline
    styles stay allowed for Plotly. HSTS is only sent when the request arrived
    over HTTPS (directly or via a trusted proxy).
    """
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Content-Security-Policy", _CSP)
    if _HSTS_MAX_AGE > 0 and request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", f"max-age={_HSTS_MAX_AGE}"
        )
    return response


def _resolve_preview_source(dir_name: str, file_name: str) -> Path:
    """Validate and resolve a video path inside an allowed preview directory."""
    directory: Path | None = _PREVIEW_DIRS.get(dir_name)
    if directory is None:
        raise HTTPException(status_code=404, detail=f"Unknown directory: {dir_name}")
    if _SAFE_NAME_RE.match(file_name) is None:
        raise HTTPException(status_code=400, detail="Invalid file name")
    source: Path = directory / file_name
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_name}")
    return source


def _require_person_name(name: str, label: str) -> str:
    """Validate a wrestler/opponent name that will be embedded in ffmpeg metadata.

    Names feed the ffmpeg title tag and generated chapter data, so they must not
    contain the field/title delimiters (":", ",") or control characters.

    Raises:
        HTTPException: 400 when the name is empty or contains a bad character.
    """
    cleaned: str = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{label} name is required")
    if _SAFE_NAME_RE.match(cleaned) is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} name must not contain ':', ',', or control characters",
        )
    return cleaned


def _is_transcoding(source: Path) -> bool:
    """True if a live transcode job is currently producing a preview.

    Stale in-flight entries (job already finished or failed) are ignored so a
    broken transcode can be retried on the next request.
    """
    job_id: str | None = transcode.in_flight_job(source)
    if job_id is None:
        return False
    job: Job | None = job_manager.get(job_id)
    return job is not None and job.status in ("queued", "running")


# Serializes the "is a preview ready / in flight?" check with the
# prepare+submit sequence so two concurrent first-time requests for the same
# source cannot both wipe the HLS workspace and launch overlapping transcodes.
_PREVIEW_LOCK: threading.Lock = threading.Lock()


def _ensure_transcode(source: Path) -> None:
    """Start an HLS transcode for ``source`` unless one is ready or in flight."""
    with _PREVIEW_LOCK:
        if transcode.hls_is_ready(source) or _is_transcoding(source):
            return
        transcode.prepare_hls(source)

        def _transcode(ctx: JobContext) -> None:
            transcode.transcode_hls(source, ctx.job_id, on_progress=ctx.report, cancel_event=ctx.cancel_event)

        # Mark in-flight with a pre-generated id before dispatch so a job that
        # finishes (or fails) instantly cannot leave a stale in-flight entry.
        job_id: str = uuid.uuid4().hex[:8]
        transcode.mark_in_flight(source, job_id)
        job_manager.submit(
            "transcode",
            _transcode,
            message=f"Preparing preview: {source.name}",
            job_id=job_id,
        )


# ---- Routes ----


@app.get("/", response_model=None)
async def index() -> HTMLResponse:
    """Serve the SPA shell, omitting the log panel unless logging is enabled."""
    return HTMLResponse(_index_html())


@app.get("/login", response_model=None)
async def login_page(request: Request) -> HTMLResponse:
    """Serve the standalone login page, or bounce to / if already signed in."""
    if not auth.enabled() or auth.is_authenticated(request.cookies):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_LOGIN_FILE.read_text(encoding="utf-8"))


@app.post("/api/auth/login", response_model=None)
async def login(req: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    """Validate the password and mint a single-session cookie."""
    if not auth.enabled():
        return {"status": "ok"}
    ip: str = request.client.host if request.client else ""
    if not auth.login_allowed(ip):
        raise HTTPException(
            status_code=429, detail="Too many login attempts. Try again later."
        )
    ok: bool
    detail: str
    cookie_value: str | None
    ok, detail, cookie_value = auth.login(req.password, ip)
    if not ok:
        raise HTTPException(status_code=401, detail=detail)
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=cookie_value,
        max_age=int(auth.SESSION_TTL),
        httponly=True,
        samesite="lax",
        secure=auth.SECURE_COOKIE,
    )
    logging.info("MatStat login successful.")
    return {"status": "ok"}


@app.post("/api/auth/logout", response_model=None)
async def logout(request: Request, response: Response) -> dict[str, str]:
    """Clear the active session and the session cookie.

    Only the owner of the active session can log out; an unauthenticated
    caller is rejected so the single-session lock cannot be DoS'd.
    """
    if not auth.logout(request.cookies):
        raise HTTPException(status_code=401, detail="Not authenticated")
    response.delete_cookie(auth.COOKIE_NAME)
    logging.info("MatStat logout.")
    return {"status": "ok"}


def _strip_between(html: str, start_marker: str, end_marker: str) -> str:
    """Remove a marked block (markers inclusive) from the page markup."""
    start: int = html.find(start_marker)
    end: int = html.find(end_marker, start)
    if start == -1 or end == -1:
        return html
    return html[:start] + html[end + len(end_marker):]


_CACHED_INDEX: tuple[bool, bool, str | None] = (False, False, None)


def _index_html() -> str:
    """Render the SPA shell HTML with the log/auth UI stripped as needed."""
    global _CACHED_INDEX
    cached_logging, cached_auth, cached_html = _CACHED_INDEX
    if cached_html is not None and cached_logging == _VISIBLE_LOGGING and cached_auth == auth.enabled():
        return cached_html
    html: str = _INDEX_FILE.read_text(encoding="utf-8")
    if _VISIBLE_LOGGING:
        result = _strip_auth_ui(html)
    else:
        for start_marker, end_marker in _LOG_UI_MARKERS:
            html = _strip_between(html, start_marker, end_marker)
        result = _strip_auth_ui(html)
    _CACHED_INDEX = (_VISIBLE_LOGGING, auth.enabled(), result)
    return result


def _strip_auth_ui(html: str) -> str:
    """Remove the logout button markup when authentication is disabled."""
    if auth.enabled():
        return html
    for start_marker, end_marker in _AUTH_UI_MARKERS:
        html = _strip_between(html, start_marker, end_marker)
    return html


def _dir_writable(path: Path) -> bool:
    """Return True when a probe file can be created and removed in ``path``."""
    probe: Path = path / f".matstat-ready-{uuid.uuid4().hex}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        probe.unlink(missing_ok=True)
        return False


@app.get("/healthz", response_model=None)
async def liveness() -> dict[str, str]:
    """Public liveness probe: the process is up and serving HTTP."""
    return {"status": "ok"}


@app.get("/readyz", response_model=None)
async def readiness() -> Response:
    """Public readiness probe with no sensitive detail.

    Checks the data root is writable, the config can be loaded (missing
    config falls back to defaults), ffmpeg is on PATH, and the compiled-data
    database is present and readable.
    """
    checks: dict[str, str] = {
        "data_root": "ok" if await run_in_threadpool(_dir_writable, _UPLOAD_DIR) else "error",
        "config": "ok",
        "ffmpeg": "ok" if shutil.which("ffmpeg") is not None else "error",
        "database": "ok",
    }
    try:
        configs.load_app_config()
    except Exception:
        logging.exception("Readiness config check failed")
        checks["config"] = "error"
    try:
        await run_in_threadpool(storage.health)
    except Exception:
        logging.exception("Readiness database check failed")
        checks["database"] = "error"
    ready: bool = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "unavailable", "checks": checks},
        status_code=200 if ready else 503,
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe used by the frontend and tests."""
    return {"status": "ok"}


@app.get("/api/videos/{dir_name}")
async def list_videos(dir_name: str) -> dict[str, list[str]]:
    """List video file names in one of the three video directories."""
    directory: Path | None = _PREVIEW_DIRS.get(dir_name)
    if directory is None:
        raise HTTPException(status_code=404, detail=f"Unknown directory: {dir_name}")
    if not directory.exists():
        return {"files": []}
    files: list[str] = sorted(
        p.name for p in directory.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )
    return {"files": files}


@app.delete("/api/videos/{dir_name}/{file_name}")
async def delete_video(
    dir_name: str, file_name: str, recompile: bool = False
) -> dict[str, Any]:
    """Delete an untagged or tagged video and its cached preview.

    Tagged videos feed the compiled database, so deleting one leaves the stats
    stale until a tabulation runs. ``recompile=true`` queues that tabulation
    (off by default so deleting several files does not trigger several runs).
    """
    if dir_name not in ("untaged", "taged"):
        raise HTTPException(status_code=404, detail=f"Unknown directory: {dir_name}")
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    directory: Path = _PREVIEW_DIRS[dir_name]
    video_path: Path = directory / file_name
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {file_name}")

    transcode.purge_preview(video_path)
    try:
        video_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete {file_name}: {e}")

    needs_recompile: bool = dir_name == "taged"
    job_id: str | None = None
    if recompile and needs_recompile:
        wrestler_names: list[str] = configs.load_wrestler_names()

        def _run(ctx: JobContext) -> dict[str, Any]:
            return stats.run_compile_stats_job(ctx, wrestler_names)

        job_id = job_manager.submit(
            "compile_stats", _run, message="Re-tabulating after delete"
        )
    return {
        "deleted": file_name,
        "needs_recompile": needs_recompile,
        "job_id": job_id,
    }


def _is_valid_video(path: Path) -> bool:
    """True when ffprobe can find at least one video stream in ``path``."""
    return bool(GetVideoCodecs(path).get("video"))


def _copy_upload(file: UploadFile, dest: Path, max_bytes: int) -> str | None:
    """Copy a spooled upload to ``dest`` with a hard byte cap.

    Returns an error message (and removes the partial file) on failure, or
    ``None`` on success.
    """
    written: int = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk: bytes = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("too large")
                out.write(chunk)
    except ValueError:
        dest.unlink(missing_ok=True)
        return f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit"
    except Exception as e:
        dest.unlink(missing_ok=True)
        return f"Upload failed: {e}"
    return None


def _disk_has_room(required_bytes: int) -> bool:
    """True when the upload can land without crossing the free-space reserve."""
    free: int = shutil.disk_usage(_UPLOAD_DIR).free
    return free - required_bytes >= _MIN_FREE_BYTES


@app.post("/api/videos", response_model=None)
async def upload_videos(files: list[UploadFile] | None = File(default=None)) -> dict[str, Any]:
    """Upload one or more video files into the untagged pool.

    Each file is handled independently: per-file results report whether it was
    uploaded, collided with an existing name, or failed, so a bad or duplicate
    file never blocks the rest of the batch. A request with no files is a 400.
    Files must have an allowed video extension, fit the size cap, and pass an
    ffprobe check before they join the pool.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files in one request (max {_MAX_UPLOAD_FILES})",
        )

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    existing: set[str] = {
        p.name.lower() for p in _UPLOAD_DIR.iterdir() if p.is_file()
    }
    results: list[dict[str, Any]] = []
    for file in files:
        file_name: str = file.filename or ""
        result: dict[str, Any] = {"file": file_name}
        if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
            result["status"] = "error"
            result["detail"] = "Invalid file name"
            results.append(result)
            continue
        extension: str = Path(file_name).suffix.lower()
        if extension not in _ALLOWED_EXTENSIONS:
            result["status"] = "error"
            result["detail"] = (
                f"Unsupported video type '{extension or 'none'}'. Allowed: "
                + ", ".join(sorted(_ALLOWED_EXTENSIONS))
            )
            results.append(result)
            continue
        if file_name.lower() in existing:
            result["status"] = "conflict"
            result["detail"] = f"File already exists: {file_name}"
            results.append(result)
            continue
        required: int = getattr(file, "size", None) or 0
        if not _disk_has_room(required):
            result["status"] = "error"
            result["detail"] = "Not enough free disk space for this upload"
            results.append(result)
            continue

        dest: Path = _UPLOAD_DIR / file_name
        error: str | None = await run_in_threadpool(
            _copy_upload, file, dest, _UPLOAD_MAX_BYTES
        )
        if error is None and not await run_in_threadpool(_is_valid_video, dest):
            dest.unlink(missing_ok=True)
            error = "File is not a readable video"
        if error is not None:
            result["status"] = "error"
            result["detail"] = error
            results.append(result)
            continue

        existing.add(file_name.lower())
        logging.info(f"Uploaded video: {file_name}")
        result["status"] = "ok"
        results.append(result)

    uploaded: int = sum(1 for r in results if r["status"] == "ok")
    return {"results": results, "uploaded": uploaded}


@app.get("/api/config", response_model=None)
async def app_config() -> dict[str, object]:
    """Return the app config (moves, ties, rulesets) merged with the roster."""
    cfg_data: dict[str, object] = configs.app_config_to_dict(configs.load_app_config())
    roster_data: dict[str, object] = RosterToDict(configs.load_roster())
    return {**cfg_data, **roster_data}


@app.put("/api/config", response_model=None)
async def update_app_config(req: AppConfigUpdate) -> dict[str, object]:
    """Save the app config (moves, ties, rulesets, active ruleset)."""
    try:
        saved: AppConfig = configs.save_app_config(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write config: {e}")

    logging.info("Updated app config")
    return {"status": "ok", **configs.app_config_to_dict(saved)}


@app.get("/api/config/wrestlers", response_model=None)
async def roster_config() -> dict[str, object]:
    """Return the wrestler roster (names + team membership)."""
    return RosterToDict(configs.load_roster())


@app.put("/api/config/wrestlers", response_model=None)
async def update_roster_config(req: RosterUpdate) -> dict[str, object]:
    """Save the wrestler roster, dropping team members not in the wrestler list."""
    try:
        saved: Roster = configs.save_roster(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write roster: {e}")

    logging.info("Updated wrestler roster")
    return {"status": "ok", "wrestlers": saved.wrestlers, "teams": saved.teams}


@app.post("/api/tag")
async def tag_video(req: TagRequest) -> dict[str, Any]:
    """Queue a tag job that embeds chapter metadata into an untagged video."""
    file_name: str = req.video.strip()
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not (tag.untaged_dir / file_name).is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_name}")
    wrestler: str = _require_person_name(req.wrestler, "Wrestler")
    opponent: str = req.opponent.strip()
    if opponent:
        _require_person_name(opponent, "Opponent")
    if req.match_result not in ("", "W", "L"):
        raise HTTPException(status_code=400, detail="Invalid match result")
    try:
        tag.ValidateMatchDate(req.match_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not req.sequences:
        raise HTTPException(status_code=400, detail="At least one sequence is required")
    output_name: str = Path(file_name).with_suffix(".mkv").name
    if (tag.taged_dir / output_name).exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tagged video already exists: {output_name} — "
                "use the Auditing tab to re-tag it or delete it first"
            ),
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        result: dict[str, Any] = tag.run_tag_job(
            ctx, file_name, wrestler, req.sequences,
            opponent=opponent, match_result=req.match_result,
            match_date=req.match_date,
        )
        _prewarm_tagged_preview(result["output"])
        return result

    job_id: str = job_manager.submit("tag", _run, message=f"Tagging {file_name}")
    return {"status": "queued", "job_id": job_id}


@app.get("/api/audit")
async def audit_videos() -> dict[str, Any]:
    """List tagged videos with their embedded sequences for auditing."""
    return {"videos": await run_in_threadpool(audit.list_tagged_videos)}


@app.post("/api/audit/{file_name}/retag")
async def audit_retag(file_name: str, req: audit.RetagRequest) -> dict[str, Any]:
    """Queue a job that re-embeds chapter metadata into a tagged video."""
    video: str = file_name.strip()
    if _SAFE_NAME_RE.match(video) is None or video.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not (audit.taged_dir / video).is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {video}")
    wrestler: str = _require_person_name(req.wrestler, "Wrestler")
    opponent: str = req.opponent.strip()
    if opponent:
        _require_person_name(opponent, "Opponent")
    if req.match_result not in ("", "W", "L"):
        raise HTTPException(status_code=400, detail="Invalid match result")
    try:
        audit.ValidateMatchDate(req.match_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    def _run(ctx: JobContext) -> dict[str, Any]:
        return audit.run_retag_job(
            ctx, video, wrestler, req.sequences,
            opponent=opponent, match_result=req.match_result,
            match_date=req.match_date, recompile=req.recompile,
        )

    job_id: str = job_manager.submit(
        "audit_retag", _run, message=f"Re-tagging {video}"
    )
    return {"status": "queued", "job_id": job_id}


def _prewarm_tagged_preview(output_name: str) -> None:
    """Queue an HLS transcode for a freshly tagged video so its preview is instant."""
    tagged_source: Path = tag.taged_dir / output_name
    if not tagged_source.is_file():
        return
    try:
        _ensure_transcode(tagged_source)
    except OSError as e:
        logging.warning(f"Could not prepare preview for {output_name}: {e}")


@app.get("/api/stats/provenance")
async def stats_provenance() -> dict[str, Any]:
    """Report the compiled ruleset versus the active one (never mutates data)."""
    return stats.ruleset_status()


@app.post("/api/compile-stats")
async def compile_stats(req: CompileRequest | None = None) -> dict[str, Any]:
    """Queue a job that compiles all tagged videos into the compiled-data store."""
    wrestler_names: list[str] = configs.load_wrestler_names()
    if not wrestler_names:
        raise HTTPException(
            status_code=404,
            detail="No wrestlers configured (Wrestlers.json not found or empty)",
        )
    force: bool = bool(req.force) if req else False

    def _run(ctx: JobContext) -> dict[str, Any]:
        return stats.run_compile_stats_job(ctx, wrestler_names, force=force)

    job_id: str = job_manager.submit("compile_stats", _run, message="Compiling stats")
    return {"status": "queued", "job_id": job_id}


@app.post("/api/team-eval")
async def team_evaluation() -> dict[str, Any]:
    """Queue a job that generates the Team Evaluation Excel report."""
    has_data: bool = storage.has_data()
    if not has_data:
        raise HTTPException(
            status_code=404,
            detail="No wrestler data files found. Run Tabulate Sequences first.",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return team_eval.run_team_eval_job(ctx)

    job_id: str = job_manager.submit("team_eval", _run, message="Generating report")
    return {"status": "queued", "job_id": job_id}


@app.post("/api/clips/find")
async def clips_find(req: FindRequest) -> dict[str, Any]:
    """Return the selected wrestlers' sequences matching a QuerySpec."""
    wrestlers: list[str] = selected_wrestlers(req)
    if not wrestlers:
        raise HTTPException(
            status_code=400,
            detail="At least one wrestler is required (send the 'wrestlers' list)",
        )

    matches: list[dict[str, Any]] = []
    try:
        for wrestler in wrestlers:
            filtered = clips.find_with_query(req, wrestler)
            for match in clips.matches_to_json(filtered):
                match["wrestler"] = wrestler
                matches.append(match)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "wrestlers": wrestlers,
        "matches": matches,
        "count": len(matches),
    }


@app.post("/api/combine-clips")
async def combine_clips(req: CombineRequest) -> dict[str, Any]:
    """Queue a job that builds one highlight reel from a QuerySpec."""
    wrestlers: list[str] = selected_wrestlers(req)
    if len(wrestlers) != 1:
        raise HTTPException(
            status_code=400,
            detail="Exactly one wrestler is required (send the 'wrestlers' list)",
        )
    wrestler: str = wrestlers[0]
    if not storage.wrestler_exists(wrestler):
        raise HTTPException(
            status_code=404,
            detail=f"No compiled data for {wrestler}. Run Compile Stats first.",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return clips.run_combine_clips_job(
            ctx, wrestler, req, req.use_stream_copy
        )

    job_id: str = job_manager.submit(
        "combine_clips", _run, message=f"Combining clips for {wrestler}"
    )
    return {"status": "queued", "job_id": job_id}


@app.post("/api/combine-clips/batch")
async def combine_clips_batch(req: BatchRequest) -> dict[str, Any]:
    """Queue a job that builds a highlight reel for every selected wrestler."""
    wrestlers: list[str] = selected_wrestlers(req)
    if not wrestlers:
        raise HTTPException(
            status_code=400,
            detail="At least one wrestler is required (send the 'wrestlers' list)",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return clips.run_batch_clips_job(
            ctx, wrestlers, req, req.use_stream_copy
        )

    job_id: str = job_manager.submit(
        "batch_clips",
        _run,
        message=f"Batch combining clips for {len(wrestlers)} wrestler(s)",
    )
    return {"status": "queued", "job_id": job_id}


@app.get("/api/reels")
async def list_reels() -> dict[str, Any]:
    """List generated highlight reels, grouped by base name and version."""
    return {"reels": clips.list_reels()}


@app.get("/api/clips/{file_name}", response_model=None)
async def download_clip(file_name: str) -> FileResponse:
    """Download a generated highlight reel from vids/clips/."""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    clip_path: Path = clips_dir / file_name
    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail=f"Clip not found: {file_name}")
    return FileResponse(str(clip_path), media_type="video/x-matroska")


@app.delete("/api/clips/{file_name}")
async def delete_clip(file_name: str) -> dict[str, Any]:
    """Delete a generated highlight reel and its cached preview."""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    clip_path: Path = clips_dir / file_name
    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail=f"Clip not found: {file_name}")
    transcode.purge_preview(clip_path)
    try:
        clip_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete {file_name}: {e}")
    return {"deleted": file_name}


@app.get("/api/pca", response_model=None)
async def pca_figure(
    scope: str = "all",
    name: str = "",
    layout: str = "matches",
) -> dict[str, Any]:
    """Return the interactive PCA scatter figure for a scope and layout."""
    if scope not in ("all", "team", "wrestler"):
        raise HTTPException(status_code=400, detail="Invalid scope")
    if layout not in ("sequences", "matches"):
        raise HTTPException(status_code=400, detail="Invalid layout")
    if scope != "all" and not name.strip():
        raise HTTPException(
            status_code=400, detail="Name is required for team/wrestler scope"
        )
    try:
        result: dict[str, Any] = await run_in_threadpool(
            pca.build_pca_figure, scope, name.strip(), layout
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/pca/compile", response_model=None)
async def pca_compile(req: PcaCompileRequest) -> dict[str, Any]:
    """Queue a job that extracts selected sequences into a PCA highlight reel."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No sequences selected")
    if req.name.strip() and _SAFE_NAME_RE.match(req.name.strip()) is None:
        raise HTTPException(
            status_code=400,
            detail="Reel name must not contain ':', ',', or control characters",
        )
    for item in req.items:
        if _SAFE_NAME_RE.match(item.video) is None or item.video.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid video name")
        if item.start_time < 0 or item.start_time >= item.end_time:
            raise HTTPException(status_code=400, detail="Invalid sequence times")

    def _run(ctx: JobContext) -> dict[str, Any]:
        return pca.run_pca_compile_job(ctx, req)

    job_id: str = job_manager.submit(
        "pca_compile", _run, message=f"Compiling PCA reel: {req.name}"
    )
    return {"status": "queued", "job_id": job_id}


@app.get("/api/trend", response_model=None)
async def trend_figure(
    wrestler: str = "",
    metric: str = "net",
    window: int = 5,
) -> dict[str, Any]:
    """Return the per-match trend figure for a wrestler."""
    if not wrestler.strip():
        raise HTTPException(status_code=400, detail="Wrestler is required")
    if metric not in ("net", "adjusted"):
        raise HTTPException(status_code=400, detail="Invalid metric")
    if window < 1:
        raise HTTPException(status_code=400, detail="Window must be at least 1")
    try:
        result: dict[str, Any] = await run_in_threadpool(
            trend.build_trend_figure, wrestler.strip(), metric, window
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/api/search/wrestlers")
async def search_wrestlers() -> dict[str, list[str]]:
    """List wrestlers with compiled data (for the search dropdown)."""
    return {"items": search.list_wrestlers()}


@app.post("/api/search")
async def search_all(req: QuerySpec) -> dict[str, Any]:
    """Run a shared QuerySpec across the selected compiled wrestler data."""
    matches: list[dict[str, Any]] = await run_in_threadpool(search.execute_search, req)
    return {"count": len(matches), "matches": matches}


@app.post("/api/search/export", response_model=None)
async def search_export(req: QuerySpec) -> Response:
    """Export matching sequences as a downloadable CSV file."""
    matches: list[dict[str, Any]] = await run_in_threadpool(search.execute_search, req)
    return Response(
        content=search.search_to_csv(matches),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="search_results.csv"'},
    )


@app.get("/api/reports/latest")
async def latest_report() -> dict[str, Any]:
    """Return freshness metadata for the current latest report."""
    info: dict[str, Any] | None = team_eval.latest_report_info()
    if info is None:
        raise HTTPException(status_code=404, detail="No report has been generated yet")
    return info


@app.get("/api/reports/{file_name}", response_model=None)
async def download_report(file_name: str) -> FileResponse:
    """Download a generated report file (e.g. Team_Stats_20260101-120000.xlsx)."""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    report_path: Path = eval_dir / file_name
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail=f"Report not found: {file_name}")
    return FileResponse(
        str(report_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{file_name}"',
        },
    )


@app.get("/api/preview/{dir_name}/{file_name}", response_model=None)
async def preview(
    dir_name: str,
    file_name: str,
) -> dict[str, Any]:
    """Return a playable HLS playlist URL for a source video.

    A ready preview is served immediately. If no usable playlist exists yet, a
    transcode job is started (deduplicated via the in-flight map) and the
    response returns a ``ready`` playlist URL anyway — playback begins as soon
    as the first ~4-second segment is produced, without waiting for the whole
    video to encode.
    """
    source: Path = _resolve_preview_source(dir_name, file_name)

    _ensure_transcode(source)

    return {
        "status": "ready",
        "playlist": f"/api/preview/{dir_name}/{quote(file_name, safe='')}/prog.m3u8",
    }


@app.get("/api/preview/{dir_name}/{file_name}/{name}", response_model=None)
async def preview_file(
    dir_name: str,
    file_name: str,
    name: str,
) -> FileResponse:
    """Serve an HLS playlist or segment for a source video.

    The playlist references its segments by bare filename (``seg_00000.ts``),
    which the browser resolves against the playlist URL, so segments are served
    at the same path level as ``prog.m3u8``.
    """
    source: Path = _resolve_preview_source(dir_name, file_name)
    if name == "prog.m3u8":
        playlist: Path = transcode.playlist_path(source)
        if not playlist.is_file():
            raise HTTPException(status_code=404, detail=f"No preview for: {file_name}")
        return FileResponse(
            str(playlist),
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"},
        )
    if _SEGMENT_RE.match(name) is None:
        raise HTTPException(status_code=400, detail="Invalid segment name")
    segment_path: Path = transcode.segments_dir(source) / name
    if not segment_path.is_file():
        raise HTTPException(status_code=404, detail=f"Segment not found: {name}")
    return FileResponse(str(segment_path), media_type="video/mp2t")


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> Job:
    """Return the current state of a background job."""
    job: Job | None = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return job


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, bool]:
    """Request cancellation of a running job."""
    cancelled: bool = job_manager.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return {"cancelled": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Stream log lines and job events to the browser."""
    if auth.enabled() and not auth.is_authenticated(websocket.cookies):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = hub.subscribe()
    # The browser never sends application messages, so `receive()` resolves
    # only when it disconnects (or uvicorn closes the socket during shutdown).
    # A handler that only awaits the event queue never notices either case:
    # uvicorn tracks it as a background task and its graceful shutdown waits
    # for it with no timeout, which is what made Ctrl+C hang. Watch both
    # channels and leave as soon as the socket goes away.
    watcher: asyncio.Task[dict[str, Any]] = asyncio.create_task(websocket.receive())
    try:
        await websocket.send_json({"type": "connected", "status": "ok"})
        while True:
            pending: asyncio.Task[dict[str, Any]] = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {watcher, pending}, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                # Cancel the queue waiter without awaiting it: during client
                # teardown a second cancellation can land in this await and
                # leave anyio's cancel scope with a cancellation it cannot
                # absorb.
                pending.cancel()
                message: dict[str, Any] = watcher.result()
                if message.get("type") == "websocket.disconnect":
                    break
                # Unexpected client message: ignore it and keep listening.
                watcher = asyncio.create_task(websocket.receive())
                continue
            event: dict[str, Any] = pending.result()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        # Do not await cleanup here: a cancellation that arrives during client
        # teardown would otherwise turn into a plain CancelledError that
        # anyio's cancel scope cannot absorb.
        watcher.cancel()
        hub.unsubscribe(queue)
