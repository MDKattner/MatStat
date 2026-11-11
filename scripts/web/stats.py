"""Compile Stats job for the web app — extract chapter data into wrestler CSVs.

Every tagged video in vids/taged/ is probed for its wrestler name and chapter
rows, rows are aggregated per wrestler, and the results are written to
stats/wrestler_data/{name}.csv. Runs inside a JobManager thread; progress is
reported via the JobContext.

The rebuild is fail-safe: a missing tagged directory or an unparseable video
aborts before any write, contradictory match results block the commit unless
``force`` is set, per-wrestler CSVs are replaced atomically, and a wrestler
whose data could not be rebuilt keeps the previous file. A manifest describing
every run is written last.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel

from scripts.helpers import (
    COL_OPPONENT_SCORES,
    COL_TEAM_SCORES,
    AdjustedNetPoints,
    AppConfigToDict,
    CalculateNetPoints,
    LoadAppConfig,
    MakeFormattedDataFrame,
    MakeNameAndCSV,
    MatchConflict,
    MatchOutcomeConflictError,
    ParseTaggedName,
    ReloadScoringMap,
    SwapPerspectiveCSV,
    ValidateMatchOutcomes,
    csv_dir,
    taged_dir,
)
from scripts.web.jobs import JobContext

SCHEMA_VERSION: int = 1
APP_VERSION: str = "0.1.0"
MANIFEST_NAME: str = "manifest.json"


class CompileRequest(BaseModel):
    """Payload for POST /api/compile-stats."""

    force: bool = False


def _process_video_safely(vid_path: Path) -> tuple[str | None, str, str]:
    """Extract a wrestler name, match date, and CSV data from a tagged video.

    Returns (name, match_date, data) on success or (None, "", error).

    Args:
        vid_path: The path to the tagged video file.

    Returns:
        A (name, match_date, data) tuple, or (None, "", error_message) on failure.
    """
    try:
        name: str
        match_date: str
        data: str
        name, match_date, data = MakeNameAndCSV(vid_path)
        return (name, match_date, data)
    except Exception as e:
        error_msg: str = f"Failed to process video '{vid_path.name}': {e}"
        logging.error(error_msg)
        return (None, "", error_msg)


def _enrich_csv_rows(rows: list[str], result: str = "", match_date: str = "") -> list[str]:
    """Append Net Points, Adjusted Net Points, match result, and match date.

    Rows are parsed with the same 9-field schema MakeFormattedDataFrame reads
    (origin, start, end, attacking, tie, team moves, opponent moves, team
    scores, opponent scores); rows that fail to parse are passed through
    unchanged so no data is ever lost. The scoring map is refreshed from the
    active ruleset so the appended values match what MakeFormattedDataFrame
    recomputes on load. ``result`` ("", "W", or "L") is the match result for
    the video these rows came from, stored as the trailing ``W/L`` column, and
    ``match_date`` ("YYYY-MM-DD") is stored after it.

    Args:
        rows: Raw CSV data rows from MakeNameAndCSV.
        result: The tagged wrestler's match result for this video.
        match_date: The match date for this video ("YYYY-MM-DD" or "").

    Returns:
        The rows with `,net,adjusted,result,match_date` appended where parseable.
    """
    ReloadScoringMap()
    enriched: list[str] = []
    for row in rows:
        parts: list[str] = row.split(",")
        if len(parts) != 9:
            enriched.append(row)
            continue
        scores: pd.Series = pd.Series({
            COL_TEAM_SCORES: [s for s in parts[7].split(":") if s],
            COL_OPPONENT_SCORES: [s for s in parts[8].split(":") if s],
        })
        net: np.int16 = CalculateNetPoints(scores)
        adjusted: np.int16 = AdjustedNetPoints(scores)
        enriched.append(f"{row},{net},{adjusted},{result},{match_date}")
    return enriched


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (same-directory temp + replace).

    Args:
        path: Destination file.
        text: Complete new contents.

    Raises:
        OSError: If the temporary write or the replace fails; the previous
            file is left untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path = path.with_name(path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _utf8_safe(text: str) -> str:
    """Replace lone surrogates so a manifest can always be JSON-encoded."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _active_ruleset_metadata() -> tuple[str, str, dict[str, Any]]:
    """Return the active ruleset's name, hash, and serialized definition."""
    cfg = LoadAppConfig()
    name: str = cfg.active_ruleset
    definition: dict[str, Any] = AppConfigToDict(cfg)["rulesets"][name]
    payload: str = json.dumps(definition, sort_keys=True)
    digest: str = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return name, digest, definition


def ruleset_status() -> dict[str, Any]:
    """Compare the compiled manifest's ruleset with the active one.

    Returns:
        A dict with the manifest and active ruleset names/hashes, whether the
        compiled data is stale, and the manifest generation time. Missing or
        unreadable manifests yield ``stale: False`` with blank fields.
    """
    active_name, active_hash, _definition = _active_ruleset_metadata()
    manifest_path: Path = csv_dir / MANIFEST_NAME
    compiled_name: str = ""
    compiled_hash: str = ""
    generated_at: str = ""
    if manifest_path.is_file():
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text())
            compiled_name = str(manifest.get("ruleset", ""))
            compiled_hash = str(manifest.get("ruleset_hash", ""))
            generated_at = str(manifest.get("generated_at", ""))
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Could not read stats manifest: {e}")
    stale: bool = bool(compiled_hash) and compiled_hash != active_hash
    return {
        "compiled_ruleset": compiled_name,
        "compiled_hash": compiled_hash,
        "active_ruleset": active_name,
        "active_hash": active_hash,
        "stale": stale,
        "generated_at": generated_at,
    }


def _existing_conflicts(wrestler_names: list[str]) -> list[MatchConflict]:
    """Validate the compiled CSVs that this run would replace.

    Args:
        wrestler_names: Roster names; only their CSVs are considered because
            only those can be overwritten by this run.

    Returns:
        Conflicts found in existing files; unreadable files are ignored here
        (their load errors are reported by the consumers).
    """
    frames: dict[str, pd.DataFrame] = {}
    for name in wrestler_names:
        csv_path: Path = (csv_dir / name).with_suffix(".csv")
        if not csv_path.is_file():
            continue
        try:
            frames[name] = MakeFormattedDataFrame(csv_path)
        except Exception as e:
            logging.warning(f"Skipping conflict scan for '{csv_path}': {e}")
    return ValidateMatchOutcomes(frames)


def _write_manifest(payload: dict[str, Any]) -> None:
    """Write stats/wrestler_data/manifest.json atomically."""
    manifest_path: Path = csv_dir / MANIFEST_NAME
    _atomic_write_text(
        manifest_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


def run_compile_stats_job(
    ctx: JobContext,
    wrestler_names: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """Compile all tagged videos into per-wrestler CSV files.

    Every tagged video is probed for its format title and chapter rows. The
    title is parsed into (wrestler, opponent, result); rows are enriched with
    the match result and match date and bucketed under the wrestler, and in
    dual mode a perspective-swapped copy is bucketed under the opponent (when
    the opponent is also in the roster). Results are written to
    stats/wrestler_data/{name}.csv.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestler_names: Wrestler names from the roster (Wrestlers.json), in order.
        force: Commit even when an existing CSV contains a contradictory
            match result (the conflict is recorded in the manifest warnings).

    Returns:
        A dict with per-wrestler sequence counts and diagnostics:
        ``{"videos_processed", "videos_scanned", "videos_failed",
        "wrestlers", "wrestlers_stale", "errors", "warnings",
        "manifest"}``.

    Raises:
        FileNotFoundError: If the tagged-video directory does not exist.
        RuntimeError: If a video's metadata cannot be parsed (aborts before
            any write).
        MatchOutcomeConflictError: If existing CSVs contain contradictory
            results and ``force`` is false.
    """
    if not taged_dir.is_dir():
        raise FileNotFoundError(
            f"Tagged video directory not found: {taged_dir}. "
            "Restore the directory or tag videos before tabulating."
        )

    video_paths: list[Path] = sorted(
        p for p in taged_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

    wrestler_to_rows: dict[str, list[str]] = {name: [] for name in wrestler_names}
    sources: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    stale_wrestlers: list[str] = []
    failed_wrestlers: set[str] = set()
    fatal_errors: list[str] = []
    videos_processed: int = 0
    videos_failed: int = 0

    total: int = len(video_paths)
    for i, vid_path in enumerate(video_paths):
        if ctx.cancelled:
            return {
                "videos_processed": videos_processed,
                "videos_scanned": i,
                "videos_failed": videos_failed,
                "wrestlers": {},
                "wrestlers_stale": [],
                "errors": errors,
                "warnings": warnings,
                "cancelled": True,
            }

        name: str | None
        match_date: str
        data: str
        name, match_date, data = _process_video_safely(vid_path)
        if name is None:
            fatal_errors.append(data)
            videos_failed += 1
            sources.append({
                "file": vid_path.name,
                "wrestler": "",
                "match_date": "",
                "result": "",
                "rows": 0,
                "status": "failed",
            })
            ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")
            continue

        wrestler: str
        opponent: str | None
        result: str
        wrestler, opponent, result = ParseTaggedName(name)
        if wrestler not in wrestler_to_rows:
            warning: str = (
                f"Wrestler '{wrestler}' from {vid_path.name} not found in "
                f"the roster (Wrestlers.json). Preserving existing data."
            )
            logging.warning(warning)
            warnings.append(warning)
            if wrestler not in stale_wrestlers:
                stale_wrestlers.append(wrestler)
            sources.append({
                "file": vid_path.name,
                "wrestler": wrestler,
                "match_date": match_date,
                "result": result,
                "rows": 0,
                "status": "roster_skip",
            })
            ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")
            continue

        try:
            lines: list[str] = data.split("\n") if data else []
            enriched: list[str] = (
                _enrich_csv_rows(lines, result, match_date) if lines else []
            )
            wrestler_to_rows[wrestler].extend(enriched)
            if opponent:
                if opponent not in wrestler_to_rows:
                    warning = (
                        f"Opponent '{opponent}' from {vid_path.name} not found "
                        f"in the roster (Wrestlers.json). Skipping opponent data."
                    )
                    logging.warning(warning)
                    warnings.append(warning)
                else:
                    swapped: str = SwapPerspectiveCSV("\n".join(enriched))
                    wrestler_to_rows[opponent].extend(swapped.split("\n"))
        except Exception as e:
            message: str = f"Failed to compile '{vid_path.name}': {e}"
            logging.error(message)
            errors.append(message)
            failed_wrestlers.add(wrestler)
            videos_failed += 1
            sources.append({
                "file": vid_path.name,
                "wrestler": wrestler,
                "match_date": match_date,
                "result": result,
                "rows": 0,
                "status": "failed",
            })
            ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")
            continue

        videos_processed += 1
        sources.append({
            "file": vid_path.name,
            "wrestler": wrestler,
            "match_date": match_date,
            "result": result,
            "rows": len(enriched),
            "status": "ok",
        })
        ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")

    if fatal_errors:
        raise RuntimeError(
            "Tabulation aborted: could not parse tagged video metadata. "
            "No CSVs were written. " + " | ".join(fatal_errors)
        )

    if ctx.cancelled:
        return {
            "videos_processed": videos_processed,
            "videos_scanned": total,
            "videos_failed": videos_failed,
            "wrestlers": {},
            "wrestlers_stale": [],
            "errors": errors,
            "warnings": warnings,
            "cancelled": True,
        }

    conflicts: list[MatchConflict] = _existing_conflicts(wrestler_names)
    if conflicts and not force:
        raise MatchOutcomeConflictError(conflicts)
    if conflicts:
        warnings.append(
            "Contradictory results were replaced because force was set: "
            + "; ".join(conflict.describe() for conflict in conflicts)
        )

    wrestler_counts: dict[str, int] = {}
    for name, rows in wrestler_to_rows.items():
        wrestler_counts[name] = len(rows)
        output_path: Path = (csv_dir / name).with_suffix(".csv")
        try:
            if name in failed_wrestlers:
                warnings.append(
                    f"'{name}' had a failed source video; the previous CSV was kept."
                )
                continue
            if rows:
                _atomic_write_text(output_path, "\n".join(rows))
            elif output_path.exists():
                output_path.unlink()
        except OSError as e:
            error_msg = f"Failed to write data to '{output_path}': {e}"
            logging.error(error_msg)
            errors.append(error_msg)

    ruleset_name: str
    ruleset_hash: str
    _definition: dict[str, Any]
    ruleset_name, ruleset_hash, _definition = _active_ruleset_metadata()
    wrestler_manifest: dict[str, dict[str, Any]] = {
        name: {
            "rows": count,
            "stale": name in stale_wrestlers,
            "points_source": "stored",
        }
        for name, count in wrestler_counts.items()
    }
    for stale_name in stale_wrestlers:
        wrestler_manifest.setdefault(
            stale_name, {"rows": 0, "stale": True, "points_source": "stored"}
        )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ruleset": ruleset_name,
        "ruleset_hash": ruleset_hash,
        "app_version": APP_VERSION,
        "taged_dir": _utf8_safe(str(taged_dir)),
        "sources": sources,
        "wrestlers": wrestler_manifest,
        "errors": errors,
        "warnings": warnings,
    }
    try:
        _write_manifest(manifest)
    except OSError as e:
        error_msg = f"Failed to write manifest: {e}"
        logging.error(error_msg)
        errors.append(error_msg)

    ctx.report(100, f"Compiled {videos_processed} videos.")
    logging.info(
        f"Compiled stats: {videos_processed} videos, "
        f"{len(wrestler_counts)} wrestler files."
    )
    return {
        "videos_processed": videos_processed,
        "videos_scanned": total,
        "videos_failed": videos_failed,
        "wrestlers": wrestler_counts,
        "wrestlers_stale": stale_wrestlers,
        "errors": errors,
        "warnings": warnings,
        "manifest": MANIFEST_NAME,
    }
