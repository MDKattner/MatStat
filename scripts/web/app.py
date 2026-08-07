from __future__ import annotations

import asyncio
import logging
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from scripts.helpers import clips_dir, csv_dir, eval_dir, taged_dir, untaged_dir
from scripts.web import clips, configs, search, stats, tag, team_eval
from scripts.web.clips import BatchRequest, CombineRequest, FindRequest
from scripts.web.jobs import Job, JobManager, JobContext
from scripts.web.search import SearchQuery
from scripts.web.tag import TagRequest
from scripts.web.transcode import ensure_preview, preview_cache_path, preview_is_fresh
from scripts.web.ws import EventHub, WebLogHandler

# ---- Paths / static frontend ----

_STATIC_DIR: Path = Path(__file__).resolve().parent / "static"
_INDEX_FILE: Path = _STATIC_DIR / "index.html"

# Which video directories are exposed for preview streaming.
_PREVIEW_DIRS: dict[str, Path] = {
    "untaged": untaged_dir,
    "taged": taged_dir,
    "clips": clips_dir,
}

# Where uploaded videos are saved (the untagged pool).
_UPLOAD_DIR: Path = untaged_dir

_SAFE_NAME_RE: re.Pattern[str] = re.compile(r"^[^/\\]+$")

# ---- Shared singletons ----

hub: EventHub = EventHub()
job_manager: JobManager = JobManager(publish=hub.publish)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Bind the event hub to the running loop and wire the log bridge."""
    hub.attach_loop(asyncio.get_running_loop())
    _attach_log_handler()
    job_manager.reset()
    logging.info("MatStat web server started.")
    yield
    logging.info("MatStat web server shutting down.")
    job_manager.shutdown()


def _attach_log_handler() -> None:
    """Add the WebLogHandler to the root logger exactly once."""
    if not any(isinstance(h, WebLogHandler) for h in logging.getLogger().handlers):
        logging.getLogger().addHandler(WebLogHandler(hub))


app: FastAPI = FastAPI(
    title="MatStat",
    version="0.1.0",
    description="Web frontend for MatStat wrestling film analysis.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


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


# ---- Routes ----


@app.get("/")
async def index() -> FileResponse:
    """Serve the SPA shell."""
    return FileResponse(str(_INDEX_FILE))


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


@app.post("/api/videos")
async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a video file into the untagged pool.

    Rejects names that are unsafe or already present so an existing match is
    never silently overwritten.
    """
    file_name: str = file.filename or ""
    if _SAFE_NAME_RE.match(file_name) is None or file_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid file name")
    dest: Path = _UPLOAD_DIR / file_name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"File already exists: {file_name}")

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    logging.info(f"Uploaded video: {file_name}")
    return {"status": "ok", "file": file_name}


@app.get("/api/configs/{name}")
async def config_items(name: str) -> dict[str, list[str]]:
    """Return the parsed entries of a config file (e.g. Moves.config)."""
    config_path: Path | None = configs.resolve_config_path(name)
    if config_path is None:
        raise HTTPException(status_code=400, detail=f"Invalid config name: {name}")
    if not config_path.is_file():
        raise HTTPException(status_code=404, detail=f"Config not found: {name}")
    return {"items": configs.load_config_items(name)}


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
    if not req.sequences:
        raise HTTPException(status_code=400, detail="At least one sequence is required")

    def _run(ctx: JobContext) -> dict[str, Any]:
        return tag.run_tag_job(ctx, file_name, req.wrestler, req.sequences)

    job_id: str = job_manager.submit("tag", _run, message=f"Tagging {file_name}")
    return {"status": "queued", "job_id": job_id}


@app.post("/api/compile-stats")
async def compile_stats() -> dict[str, Any]:
    """Queue a job that compiles all tagged videos into wrestler CSVs."""
    wrestler_names: list[str] = configs.load_config_items("Wrestlers.config")
    if not wrestler_names:
        raise HTTPException(status_code=404, detail="Wrestlers.config not found or empty")

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
) -> FileResponse | dict[str, Any]:
    """Stream a browser-playable preview MP4 for a source video.

    Serves the cached MP4 directly (Starlette FileResponse supports HTTP Range
    requests for seeking). If no fresh cache exists, a transcode job is started
    and the response returns ``{"status": "transcoding", "job_id": ...}``.
    """
    source: Path = _resolve_preview_source(dir_name, file_name)
    cache: Path = preview_cache_path(source)

    if preview_is_fresh(source, cache):
        return FileResponse(str(cache), media_type="video/mp4")

    def _transcode(ctx: JobContext) -> None:
        ensure_preview(source, on_progress=ctx.report, cancel_event=None)

    job_id: str = job_manager.submit(
        "transcode",
        _transcode,
        message=f"Preparing preview: {source.name}",
    )
    return {"status": "transcoding", "job_id": job_id}


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
