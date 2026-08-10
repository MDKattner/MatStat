"""Team Evaluation job for the web app — generate the multi-sheet Excel report.

Ports ``TeamEvaluationWidget``: each wrestler CSV in stats/wrestler_data/ is
loaded, its Initiation/Rates/Defense/Offense DataFrames are generated, and
everything is written into a single multi-sheet xlsx at stats/reports/
Team_Stats.xlsx (a cross-wrestler "Team Summary" sheet comes first). Runs
inside a JobManager thread; progress is reported via the JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Font

from scripts.helpers import (
    GenerateDefenseDF,
    GenerateInitiationDFBySegment,
    GenerateMatchOutcomesDF,
    GenerateOffenseDF,
    GenerateRatesDF,
    GenerateTeamSummaryDF,
    MakeFormattedDataFrame,
    csv_dir,
    eval_dir,
)
from scripts.web.jobs import JobContext


_SECTION_LABELS: list[tuple[int, int, str]] = [
    (0, 0, "Initiation"),
    (6, 0, "Rates"),
    (9, 0, "Defense"),
    (9, 8, "Offense"),
    (9, 16, "Raw Data"),
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


def run_team_eval_job(ctx: JobContext) -> dict[str, Any]:
    """Generate the multi-sheet Excel report from compiled wrestler data.

    Args:
        ctx: Job context for progress reporting.

    Returns:
        A dict describing the result: ``{"output": "Team_Stats.xlsx",
        "processed": [names], "errors": [...]}``. The workbook's first sheet is
        the cross-wrestler "Team Summary"; each wrestler sheet holds the
        Initiation, Rates, Defense, Offense, and raw blocks.

    Raises:
        ValueError: If no wrestler CSV files exist in csv_dir.
        RuntimeError: If the Excel file cannot be created or written.
    """
    csv_files: list[Path] = _find_csv_files()
    if not csv_files:
        raise ValueError(
            "No wrestler data files found in stats/wrestler_data/. "
            "Run Compile Stats first."
        )

    excel_file: Path = eval_dir / "Team_Stats.xlsx"
    errors: list[str] = []
    processed: list[str] = []
    total: int = len(csv_files)

    try:
        excel_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Could not create report directory: {e}")

    frames: dict[str, pd.DataFrame] = {}
    for csv_file in csv_files:
        sheet_name: str = csv_file.stem
        try:
            frames[sheet_name] = MakeFormattedDataFrame(csv_file)
        except Exception as e:
            error_msg: str = f"Failed to process '{sheet_name}': {e}"
            logging.error(error_msg)
            errors.append(error_msg)

    if not frames:
        raise ValueError(
            "No wrestler data files found in stats/wrestler_data/. "
            "Run Compile Stats first."
        )

    try:
        with pd.ExcelWriter(excel_file, engine="openpyxl", mode="w") as writer:
            GenerateTeamSummaryDF(frames).to_excel(writer, sheet_name="Team Summary")
            GenerateMatchOutcomesDF(frames).to_excel(writer, sheet_name="Match Outcomes")
            for i, (sheet_name, raw_df) in enumerate(frames.items()):
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
                    processed.append(sheet_name)
                    logging.debug(f"Wrote data for sheet '{sheet_name}'.")
                except Exception as e:
                    error_msg: str = f"Failed to process '{sheet_name}': {e}"
                    logging.error(error_msg)
                    errors.append(error_msg)
                ctx.report(100.0 * (i + 1) / total, f"Processing {sheet_name}")
    except Exception as e:
        raise RuntimeError(f"Failed to create Excel report: {e}")

    ctx.report(100, f"Report generated: {excel_file.name}")
    logging.info(f"Team Evaluation report written: {excel_file}")
    return {"output": excel_file.name, "processed": processed, "errors": errors}
