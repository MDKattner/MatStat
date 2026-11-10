"""Team Evaluation job for the web app — generate the multi-sheet Excel report.

Ports ``TeamEvaluationWidget``: each wrestler CSV in stats/wrestler_data/ is
loaded, its Initiation/Rates/Defense/Offense DataFrames are generated, and
everything is written into a timestamped multi-sheet xlsx at stats/reports/.

Reports are atomic and versioned (DI-5): the workbook is written to a temporary
file, moved into place with ``os.replace``, and only then does
``Team_Stats_latest.xlsx`` point at it. A failed sheet keeps the previous
latest report intact and marks the result partial; a contradictory match result
aborts before anything is written. Runs inside a JobManager thread; progress is
reported via the JobContext.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from scripts.helpers import (
    COL_MATCH_DATE,
    GenerateDefenseDF,
    GenerateInitiationDFBySegment,
    GenerateMatchOutcomesDF,
    GenerateOffenseDF,
    GenerateRatesDF,
    GenerateTeamSummaryDF,
    MakeFormattedDataFrame,
    MatchOutcomeConflictError,
    ValidateMatchOutcomes,
    csv_dir,
    eval_dir,
)
from scripts.web import stats
from scripts.web.jobs import JobContext


REPORT_BASE: str = "Team_Stats"
LATEST_NAME: str = "Team_Stats_latest.xlsx"
RETAIN_COUNT: int = 10
_REPORT_RE: re.Pattern[str] = re.compile(
    r"^Team_Stats_\d{8}-\d{6}(?:-\d+)?\.xlsx$"
)

_SECTION_LABELS: list[tuple[int, int, str]] = [
    (0, 0, "Initiation"),
    (6, 0, "Rates"),
    (9, 0, "Defense"),
    (9, 8, "Offense"),
    (9, 16, "Raw Data"),
]

_TAB_COLORS: dict[str, str] = {
    "Read Me": "7F7F7F",
    "Team Summary": "2E7D32",
    "Match Outcomes": "1565C0",
}

_DEFINITIONS: list[tuple[str, str]] = [
    ("Net Points", "Sum of the scoring codes in a sequence under the active ruleset."),
    ("Adjusted Net Points", "Net points plus situational adjustments (e.g. pin bonus)."),
    ("Pin bonus", "Extra points added to the winner of a pin under the active ruleset."),
    ("(z) columns", "Population z-score of a team rate across wrestlers; zeros when no variance."),
    ("Team Mean", "Mean of each base rate column across the wrestlers in this report."),
    ("Pins by Wrestler", "Distinct matches in which the wrestler recorded a pin."),
    ("Times Wrestler Pinned", "Distinct matches in which the wrestler was pinned."),
    ("Unrecorded", "Matches with no W/L outcome recorded; they are excluded from win rate."),
    ("Win Rate", "Wins / Recorded, blank when no outcome was recorded; displayed as a percent."),
]


def _find_csv_files() -> list[Path]:
    """List wrestler CSV files to process, excluding the UNKNOWN sentinel.

    Returns:
        The sorted CSV paths under csv_dir, minus UNKNOWN.csv.
    """
    return sorted(p for p in csv_dir.glob("*.csv") if p.name != "UNKNOWN.csv")


def _write_section_labels(writer: pd.ExcelWriter, sheet_name: str) -> None:
    """Write bold section headers above the report blocks on a sheet.

    Args:
        writer: The active ExcelWriter.
        sheet_name: The sheet whose report blocks get labeled.
    """
    ws = writer.sheets[sheet_name]
    for row, col, label in _SECTION_LABELS:
        cell = ws.cell(row=row + 1, column=col + 1, value=label)
        cell.font = Font(bold=True)


def _format_win_rate(
    writer: pd.ExcelWriter, sheet_name: str, outcomes: pd.DataFrame
) -> None:
    """Display the Match Outcomes win rate as a percentage.

    Args:
        writer: The active ExcelWriter.
        sheet_name: The sheet holding the outcomes block.
        outcomes: The DataFrame written to that sheet.
    """
    try:
        column: int = list(outcomes.columns).index("Win Rate") + 2
    except ValueError:
        return
    ws = writer.sheets[sheet_name]
    for row in range(2, len(outcomes) + 2):
        ws.cell(row=row, column=column).number_format = "0%"


def _style_sheet(
    ws: Worksheet,
    *,
    freeze: str | None = "A2",
    autofilter: bool = False,
    wrap_header: bool = True,
) -> None:
    """Apply the shared report formatting to one worksheet.

    Args:
        ws: The worksheet to style.
        freeze: A freeze-panes cell reference, or None to leave it unfrozen.
        autofilter: Whether to filter the worksheet's full used range.
        wrap_header: Whether to wrap the first row's header text.
    """
    if freeze:
        ws.freeze_panes = freeze
    if autofilter and ws.max_row > 1 and ws.max_column > 1:
        ws.auto_filter.ref = ws.dimensions
    if wrap_header:
        for cell in ws[1]:
            cell.alignment = Alignment(wrap_text=True, vertical="center")
    for column_cells in ws.iter_cols():
        longest: int = 0
        for cell in column_cells:
            if cell.value is None:
                continue
            longest = max(longest, len(str(cell.value)))
        letter: str = column_cells[0].column_letter
        ws.column_dimensions[letter].width = min(max(longest + 2, 10), 42)


def _format_numbers(ws: Worksheet, start_row: int = 2) -> None:
    """Apply a two-decimal number format to every float cell in a sheet."""
    for row in ws.iter_rows(min_row=start_row):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "0.00"


def _unique_report_path(target_dir: Path) -> tuple[Path, str]:
    """Return a unique timestamped report path and its generated-at text."""
    stamp: str = datetime.now().strftime("%Y%m%d-%H%M%S")
    generated_at: str = (
        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )
    candidate: Path = target_dir / f"{REPORT_BASE}_{stamp}.xlsx"
    suffix: int = 1
    while candidate.exists() or candidate.with_suffix(".json").exists():
        candidate = target_dir / f"{REPORT_BASE}_{stamp}-{suffix}.xlsx"
        suffix += 1
    return candidate, generated_at


def _update_latest_pointer(target_dir: Path, report: Path) -> str | None:
    """Point ``Team_Stats_latest.xlsx`` at ``report``; copy when symlinks fail."""
    latest: Path = target_dir / LATEST_NAME
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(report.name)
        return latest.name
    except OSError as e:
        logging.warning(f"Could not create report latest symlink '{latest}': {e}")
    try:
        shutil.copyfile(report, latest)
        return latest.name
    except OSError as e:
        logging.warning(f"Could not create report latest copy '{latest}': {e}")
        return None


def _prune_reports(target_dir: Path) -> None:
    """Keep the newest ``RETAIN_COUNT`` timestamped reports, pruning older ones."""
    reports: list[Path] = sorted(
        (p for p in target_dir.iterdir() if _REPORT_RE.match(p.name)),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
    )
    for stale in reports[:-RETAIN_COUNT] if len(reports) > RETAIN_COUNT else []:
        try:
            stale.unlink()
            stale.with_suffix(".json").unlink(missing_ok=True)
            logging.info(f"Pruned old report: {stale.name}")
        except OSError as e:
            logging.warning(f"Could not prune old report '{stale}': {e}")


def _match_date_range(frames: dict[str, pd.DataFrame]) -> tuple[str, str]:
    """Return the earliest and latest recorded match dates across frames."""
    dates: list[str] = []
    for frame in frames.values():
        if COL_MATCH_DATE in frame.columns:
            dates.extend(
                str(value)
                for value in frame[COL_MATCH_DATE]
                if str(value).strip()
            )
    if not dates:
        return ("", "")
    return (min(dates), max(dates))


def _read_me_rows(
    *,
    generated_at: str,
    processed: list[str],
    matches: int,
    date_range: tuple[str, str],
    ruleset_name: str,
    ruleset_hash: str,
    pin_bonus: str,
    points_source: str,
) -> list[tuple[str, str]]:
    """Build the key/value rows for the Read Me sheet."""
    rows: list[tuple[str, str]] = [
        ("Generated at", generated_at),
        ("App version", stats.APP_VERSION),
        ("Active ruleset", ruleset_name),
        ("Ruleset hash", ruleset_hash),
        ("Pin bonus", pin_bonus),
        ("Compiled points", points_source),
        ("Wrestlers", str(len(processed))),
        ("Matches", str(matches)),
        ("Earliest match date", date_range[0] or "n/a"),
        ("Latest match date", date_range[1] or "n/a"),
        ("Source files", ", ".join(f"{name}.csv" for name in processed) or "n/a"),
        (
            "Report scope",
            "Full roster. Team/date/result scope filters are not applied yet.",
        ),
        ("", ""),
        ("Definition", "Meaning"),
    ]
    rows.extend(_DEFINITIONS)
    return rows


def _capture_points_source(frames: dict[str, pd.DataFrame]) -> str:
    """Summarize whether the loaded frames used stored or computed points."""
    sources: set[str] = {
        str(frame.attrs.get("points_source", "unknown")) for frame in frames.values()
    }
    if sources == {"stored"}:
        return "Stored values from the compiled CSVs"
    if sources == {"computed_on_load"}:
        return "Computed on load (legacy 9-column CSVs)"
    return "Mixed stored and computed values"


def latest_report_info() -> dict[str, Any] | None:
    """Describe the current latest report for the freshness endpoint.

    Returns:
        A dict with the filename, mtime, sidecar metadata, and ruleset status,
        or None when no latest pointer exists.
    """
    latest: Path = eval_dir / LATEST_NAME
    if not latest.exists():
        return None
    resolved: Path = latest.resolve()
    metadata: dict[str, Any] = {}
    sidecar: Path = resolved.with_suffix(".json")
    if sidecar.is_file():
        try:
            metadata = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Could not read report metadata '{sidecar}': {e}")
    status: dict[str, Any] = stats.ruleset_status()
    return {
        "filename": resolved.name,
        "latest": LATEST_NAME,
        "mtime": resolved.stat().st_mtime,
        "generated_at": metadata.get("generated_at", ""),
        "manifest_hash": status.get("compiled_hash", ""),
        "active_hash": status.get("active_hash", ""),
        "compiled_ruleset": status.get("compiled_ruleset", ""),
        "active_ruleset": status.get("active_ruleset", ""),
        "stale": status.get("stale", False),
        "processed": metadata.get("processed", []),
        "failed_sheets": metadata.get("failed_sheets", []),
        "partial": metadata.get("partial", False),
        "matches": metadata.get("matches", 0),
        "wrestlers": metadata.get("wrestlers", 0),
    }


def run_team_eval_job(ctx: JobContext) -> dict[str, Any]:
    """Generate the multi-sheet Excel report from compiled wrestler data.

    Args:
        ctx: Job context for progress reporting and cancellation.

    Returns:
        A dict describing the result: ``output`` (concrete filename), ``latest``
        (pointer filename or None), ``processed``, ``errors``, ``failed_sheets``,
        ``partial``, and ``cancelled``. The workbook's first sheet is the
        cross-wrestler "Read Me"; "Team Summary" and "Match Outcomes" follow,
        then one sheet per wrestler with the Initiation, Rates, Defense,
        Offense, and raw blocks.

    Raises:
        ValueError: If no wrestler CSV files exist in csv_dir.
        MatchOutcomeConflictError: If any match has contradictory W/L results.
        RuntimeError: If the workbook cannot be written or moved into place.
    """
    csv_files: list[Path] = _find_csv_files()
    if not csv_files:
        raise ValueError(
            "No wrestler data files found in stats/wrestler_data/. "
            "Run Compile Stats first."
        )

    errors: list[str] = []
    processed: list[str] = []
    failed_sheets: list[str] = []
    total: int = len(csv_files)

    try:
        eval_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Could not create report directory: {e}")

    frames: dict[str, pd.DataFrame] = {}
    for i, csv_file in enumerate(csv_files):
        if ctx.cancelled:
            return {
                "output": "",
                "latest": None,
                "processed": processed,
                "errors": errors,
                "failed_sheets": failed_sheets,
                "partial": False,
                "cancelled": True,
            }
        sheet_name: str = csv_file.stem
        try:
            frames[sheet_name] = MakeFormattedDataFrame(csv_file)
        except Exception as e:
            error_msg: str = f"Failed to process '{sheet_name}': {e}"
            logging.error(error_msg)
            errors.append(error_msg)
        ctx.report(100.0 * (i + 1) / total, f"Loading {sheet_name}")

    if not frames:
        raise ValueError(
            "No wrestler data files found in stats/wrestler_data/. "
            "Run Compile Stats first."
        )

    # D-9: contradictory results abort before any file is written.
    conflicts = ValidateMatchOutcomes(frames)
    if conflicts:
        raise MatchOutcomeConflictError(conflicts)

    if ctx.cancelled:
        return {
            "output": "",
            "latest": None,
            "processed": processed,
            "errors": errors,
            "failed_sheets": failed_sheets,
            "partial": False,
            "cancelled": True,
        }

    outcomes: pd.DataFrame = GenerateMatchOutcomesDF(frames)
    summary: pd.DataFrame = GenerateTeamSummaryDF(frames)
    # Rename the ambiguous pin columns in the report without changing the
    # helper API used elsewhere.
    outcomes = outcomes.rename(
        columns={"Pins": "Pins by Wrestler", "Times Pinned": "Times Wrestler Pinned"}
    )

    report_path, generated_at = _unique_report_path(eval_dir)
    tmp_path: Path = eval_dir / f"{report_path.stem}.tmp.xlsx"

    ruleset_name, ruleset_hash, definition = stats._active_ruleset_metadata()
    pin_bonus: str = str(definition.get("pin_points", "n/a"))
    date_range: tuple[str, str] = _match_date_range(frames)
    read_me: list[tuple[str, str]] = _read_me_rows(
        generated_at=generated_at,
        processed=list(frames),
        matches=int(outcomes["Matches"].sum()) if not outcomes.empty else 0,
        date_range=date_range,
        ruleset_name=ruleset_name,
        ruleset_hash=ruleset_hash,
        pin_bonus=pin_bonus,
        points_source=_capture_points_source(frames),
    )

    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl", mode="w") as writer:
            pd.DataFrame(read_me, columns=["Field", "Value"]).to_excel(
                writer, sheet_name="Read Me", index=False
            )
            _style_sheet(writer.sheets["Read Me"], autofilter=False)
            writer.sheets["Read Me"].column_dimensions["B"].width = 90

            summary.to_excel(writer, sheet_name="Team Summary")
            _style_sheet(writer.sheets["Team Summary"], autofilter=True)
            _format_numbers(writer.sheets["Team Summary"])

            outcomes.to_excel(writer, sheet_name="Match Outcomes")
            _style_sheet(writer.sheets["Match Outcomes"], autofilter=True)
            _format_numbers(writer.sheets["Match Outcomes"])
            _format_win_rate(writer, "Match Outcomes", outcomes)

            for sheet_name, raw_df in frames.items():
                try:
                    GenerateInitiationDFBySegment(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=1
                    )
                    GenerateRatesDF(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=7
                    )
                    GenerateDefenseDF(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=10
                    )
                    GenerateOffenseDF(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=10, startcol=8
                    )
                    raw_df.to_excel(
                        writer, sheet_name=sheet_name, startrow=10, startcol=16
                    )
                    _write_section_labels(writer, sheet_name)
                    _style_sheet(writer.sheets[sheet_name], freeze=None)
                    processed.append(sheet_name)
                    logging.debug(f"Wrote data for sheet '{sheet_name}'.")
                except Exception as e:
                    error_msg: str = f"Failed to process '{sheet_name}': {e}"
                    logging.error(error_msg)
                    errors.append(error_msg)
                    failed_sheets.append(sheet_name)

            for name, ws in writer.sheets.items():
                ws.sheet_properties.tabColor = _TAB_COLORS.get(name, "8E24AA")
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to create Excel report: {e}")

    partial: bool = bool(failed_sheets)
    try:
        os.replace(tmp_path, report_path)
    except OSError as e:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to finalize Excel report: {e}")

    metadata: dict[str, Any] = {
        "generated_at": generated_at,
        "output": report_path.name,
        "processed": processed,
        "failed_sheets": failed_sheets,
        "errors": errors,
        "partial": partial,
        "matches": int(outcomes["Matches"].sum()) if not outcomes.empty else 0,
        "wrestlers": len(processed),
        "ruleset": ruleset_name,
        "ruleset_hash": ruleset_hash,
    }
    try:
        report_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    except OSError as e:
        logging.warning(f"Could not write report metadata: {e}")

    latest: str | None = None
    if not partial:
        latest = _update_latest_pointer(eval_dir, report_path)
        _prune_reports(eval_dir)

    if partial:
        ctx.report(100, f"Report incomplete: {report_path.name}")
        logging.warning(
            f"Team Evaluation report incomplete ({len(failed_sheets)} failed "
            f"sheet(s)): {report_path}"
        )
    else:
        ctx.report(100, f"Report generated: {report_path.name}")
        logging.info(f"Team Evaluation report written: {report_path}")

    return {
        "output": report_path.name,
        "latest": latest,
        "processed": processed,
        "errors": errors,
        "failed_sheets": failed_sheets,
        "partial": partial,
        "cancelled": False,
        "generated_at": generated_at,
    }
