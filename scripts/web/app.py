from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

# helpers.py resolves cfg/vids/tmp/stats against the process cwd; pin it to the
# repo root so config/video paths work regardless of how the server is launched.
os.chdir(Path(__file__).resolve().parent.parent.parent)

from scripts.helpers import (
    AppConfig,
    Roster,
    RosterToDict,
    clips_dir,
    csv_dir,
    eval_dir,
    taged_dir,
    untaged_dir,
)
from scripts.web import audit, auth, clips, configs, pca, search, stats, tag, team_eval, transcode
from scripts.web.auth import LoginRequest
from scripts.web.clips import BatchRequest, CombineRequest, FindRequest
from scripts.web.configs import AppConfigUpdate, RosterUpdate
from scripts.web.pca import PcaCompileRequest
from scripts.web.jobs import Job, JobManager, JobContext
from scripts.web.search import SearchQuery
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

_SAFE_NAME_RE: re.Pattern[str] = re.compile(r"^[^/\\]+$")

# HLS segment files are named seg_00000.ts, seg_00001.ts, ...
_SEGMENT_RE: re.Pattern[str] = re.compile(r"^seg_\d{5}\.ts$")

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
    version="0.1.0",
    description="Web frontend for MatStat wrestling film analysis.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.middleware("http")
async def _no_stale_cache(request: Request, call_next) -> Response:
    """Revalidate HTML/CSS/JS on every load so cached assets never go stale.

    A mixed cache (e.g. an old app.js served against a new index.html) can
    crash the SPA boot and blank every tab; forcing revalidation prevents it.
    Video streams (HLS playlists/segments) are left alone.
    """
    response: Response = await call_next(request)
    content_type: str = response.headers.get("content-type", "")
    if "video" not in content_type:
        response.headers.setdefault("Cache-Control", "no-cache")
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
            (request.method == "GET" and path == "/login")
            or (request.method == "POST" and path == "/api/auth/login")
            or (request.method == "POST" and path == "/api/auth/logout")
        )
        if not public and not auth.is_authenticated(request.cookies):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


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

        job_id: str = job_manager.submit(
            "transcode",
            _transcode,
            message=f"Preparing preview: {source.name}",
        )
        transcode.mark_in_flight(source, job_id)


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
async def login(req: LoginRequest, response: Response) -> dict[str, str]:
    """Validate the password and mint a single-session cookie."""
    if not auth.enabled():
        return {"status": "ok"}
    ok: bool
    detail: str
    cookie_value: str | None
    ok, detail, cookie_value = auth.login(req.password)
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
async def logout(response: Response) -> dict[str, str]:
    """Clear the active session and the session cookie."""
    auth.logout()
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


def _index_html() -> str:
    """Render the SPA shell HTML with the log/auth UI stripped as needed."""
    html: str = _INDEX_FILE.read_text(encoding="utf-8")
    if _VISIBLE_LOGGING:
        return _strip_auth_ui(html)
    for start_marker, end_marker in _LOG_UI_MARKERS:
        html = _strip_between(html, start_marker, end_marker)
    return _strip_auth_ui(html)


def _strip_auth_ui(html: str) -> str:
    """Remove the logout button markup when authentication is disabled."""
    if auth.enabled():
        return html
    for start_marker, end_marker in _AUTH_UI_MARKERS:
        html = _strip_between(html, start_marker, end_marker)
    return html


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


@app.post("/api/videos", response_model=None)
async def upload_videos(files: list[UploadFile] | None = File(default=None)) -> dict[str, Any]:
    """Upload one or more video files into the untagged pool.

    Each file is handled independently: per-file results report whether it was
    uploaded, collided with an existing name, or failed, so a bad or duplicate
    file never blocks the rest of the batch. A request with no files is a 400.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for file in files:
        file_name: str = file.filename or ""
        result: dict[str, Any] = {"file": file_name}
        if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
            result["status"] = "error"
            result["detail"] = "Invalid file name"
            results.append(result)
            continue
        dest: Path = _UPLOAD_DIR / file_name
        if dest.exists():
            result["status"] = "conflict"
            result["detail"] = f"File already exists: {file_name}"
            results.append(result)
            continue
        try:
            with open(dest, "wb") as out:
                shutil.copyfileobj(file.file, out)
        except Exception as e:
            dest.unlink(missing_ok=True)
            result["status"] = "error"
            result["detail"] = f"Upload failed: {e}"
            results.append(result)
            continue
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
    if not req.wrestler.strip():
        raise HTTPException(status_code=400, detail="Wrestler name is required")
    if req.match_result not in ("", "W", "L"):
        raise HTTPException(status_code=400, detail="Invalid match result")
    try:
        tag.ValidateMatchDate(req.match_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not req.sequences:
        raise HTTPException(status_code=400, detail="At least one sequence is required")

    def _run(ctx: JobContext) -> dict[str, Any]:
        result: dict[str, Any] = tag.run_tag_job(
            ctx, file_name, req.wrestler, req.sequences,
            opponent=req.opponent, match_result=req.match_result,
            match_date=req.match_date,
        )
        _prewarm_tagged_preview(result["output"])
        return result

    job_id: str = job_manager.submit("tag", _run, message=f"Tagging {file_name}")
    return {"status": "queued", "job_id": job_id}


@app.get("/api/audit")
async def audit_videos() -> dict[str, Any]:
    """List tagged videos with their embedded sequences for auditing."""
    return {"videos": audit.list_tagged_videos()}


@app.post("/api/audit/{file_name}/retag")
async def audit_retag(file_name: str, req: audit.RetagRequest) -> dict[str, Any]:
    """Queue a job that re-embeds chapter metadata into a tagged video."""
    video: str = file_name.strip()
    if _SAFE_NAME_RE.match(video) is None or video.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not (audit.taged_dir / video).is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {video}")
    if not req.wrestler.strip():
        raise HTTPException(status_code=400, detail="Wrestler name is required")
    if req.match_result not in ("", "W", "L"):
        raise HTTPException(status_code=400, detail="Invalid match result")
    try:
        audit.ValidateMatchDate(req.match_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not req.sequences:
        raise HTTPException(status_code=400, detail="At least one sequence is required")

    def _run(ctx: JobContext) -> dict[str, Any]:
        return audit.run_retag_job(
            ctx, video, req.wrestler, req.sequences,
            opponent=req.opponent, match_result=req.match_result,
            match_date=req.match_date,
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


@app.post("/api/compile-stats")
async def compile_stats() -> dict[str, Any]:
    """Queue a job that compiles all tagged videos into wrestler CSVs."""
    wrestler_names: list[str] = configs.load_wrestler_names()
    if not wrestler_names:
        raise HTTPException(
            status_code=404,
            detail="No wrestlers configured (Wrestlers.json not found or empty)",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return stats.run_compile_stats_job(ctx, wrestler_names)

    job_id: str = job_manager.submit("compile_stats", _run, message="Compiling stats")
    return {"status": "queued", "job_id": job_id}


@app.post("/api/team-eval")
async def team_evaluation() -> dict[str, Any]:
    """Queue a job that generates the Team Evaluation Excel report."""
    has_data: bool = any(
        p.name != "UNKNOWN.csv" for p in csv_dir.glob("*.csv")
    )
    if not has_data:
        raise HTTPException(
            status_code=404,
            detail="No wrestler data files found. Run Compile Stats first.",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return team_eval.run_team_eval_job(ctx)

    job_id: str = job_manager.submit("team_eval", _run, message="Generating report")
    return {"status": "queued", "job_id": job_id}


@app.post("/api/clips/find")
async def clips_find(req: FindRequest) -> dict[str, Any]:
    """Return a wrestler's sequences matching a filter, for previewing."""
    wrestler: str = req.wrestler.strip()
    if not wrestler:
        raise HTTPException(status_code=400, detail="Wrestler name is required")
    if not req.filter_item.strip():
        raise HTTPException(status_code=400, detail="Filter item is required")

    try:
        filtered = clips.load_filtered_dataframe(wrestler, req.filter_type, req.filter_item)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    matches: list[dict[str, Any]] = clips.matches_to_json(filtered)
    return {
        "wrestler": wrestler,
        "filter_type": req.filter_type,
        "filter_item": req.filter_item,
        "matches": matches,
        "count": len(matches),
    }


@app.post("/api/combine-clips")
async def combine_clips(req: CombineRequest) -> dict[str, Any]:
    """Queue a job that builds one highlight reel from filtered sequences."""
    wrestler: str = req.wrestler.strip()
    if not wrestler:
        raise HTTPException(status_code=400, detail="Wrestler name is required")
    if not req.filter_item.strip():
        raise HTTPException(status_code=400, detail="Filter item is required")
    if not (csv_dir / f"{wrestler}.csv").is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No compiled data for {wrestler}. Run Compile Stats first.",
        )

    def _run(ctx: JobContext) -> dict[str, Any]:
        return clips.run_combine_clips_job(
            ctx, wrestler, req.filter_type, req.filter_item, req.use_stream_copy
        )

    job_id: str = job_manager.submit(
        "combine_clips", _run, message=f"Combining clips for {wrestler}"
    )
    return {"status": "queued", "job_id": job_id}


@app.post("/api/combine-clips/batch")
async def combine_clips_batch(req: BatchRequest) -> dict[str, Any]:
    """Queue a job that builds a highlight reel for every selected wrestler."""
    wrestlers: list[str] = [w.strip() for w in req.wrestlers if w.strip()]
    if not wrestlers:
        raise HTTPException(status_code=400, detail="At least one wrestler is required")
    if not req.filter_item.strip():
        raise HTTPException(status_code=400, detail="Filter item is required")

    def _run(ctx: JobContext) -> dict[str, Any]:
        return clips.run_batch_clips_job(
            ctx, wrestlers, req.filter_type, req.filter_item, req.use_stream_copy
        )

    job_id: str = job_manager.submit(
        "batch_clips",
        _run,
        message=f"Batch combining clips for {len(wrestlers)} wrestler(s)",
    )
    return {"status": "queued", "job_id": job_id}


@app.get("/api/clips/{file_name}", response_model=None)
async def download_clip(file_name: str) -> FileResponse:
    """Download a generated highlight reel from vids/clips/."""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    clip_path: Path = clips_dir / file_name
    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail=f"Clip not found: {file_name}")
    return FileResponse(str(clip_path), media_type="video/x-matroska")


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
        result: dict[str, Any] = pca.build_pca_figure(scope, name.strip(), layout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/pca/compile", response_model=None)
async def pca_compile(req: PcaCompileRequest) -> dict[str, Any]:
    """Queue a job that extracts selected sequences into a PCA highlight reel."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No sequences selected")
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


@app.get("/api/search/wrestlers")
async def search_wrestlers() -> dict[str, list[str]]:
    """List wrestlers with compiled CSV data (for the search dropdown)."""
    return {"items": search.list_wrestlers()}


@app.post("/api/search")
async def search_all(req: SearchQuery) -> dict[str, Any]:
    """Run a filter query across all compiled wrestler data."""
    matches: list[dict[str, Any]] = search.execute_search(req)
    return {"count": len(matches), "matches": matches}


@app.post("/api/search/export", response_model=None)
async def search_export(req: SearchQuery) -> Response:
    """Export matching sequences as a downloadable CSV file."""
    matches: list[dict[str, Any]] = search.execute_search(req)
    return Response(
        content=search.search_to_csv(matches),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="search_results.csv"'},
    )


@app.get("/api/reports/{file_name}", response_model=None)
async def download_report(file_name: str) -> FileResponse:
    """Download a generated report file (e.g. Team_Stats.xlsx)."""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    report_path: Path = eval_dir / file_name
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail=f"Report not found: {file_name}")
    return FileResponse(
        str(report_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    try:
        await websocket.send_json({"type": "connected", "status": "ok"})
        while True:
            event: dict[str, Any] = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
