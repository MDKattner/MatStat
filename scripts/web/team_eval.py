"""Team Evaluation job for the web app — generate the multi-sheet Excel report.

Ports ``TeamEvaluationWidget``: each wrestler CSV in stats/wrestler_data/ is
loaded, its Initiation/Defense/Offense DataFrames are generated, and everything
is written into a single multi-sheet xlsx at stats/reports/Team_Stats.xlsx.
Runs inside a JobManager thread; progress is reported via the JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.helpers import (
    GenerateDefenseDF,
    GenerateInitiationDF,
    GenerateOffenseDF,
    MakeFormattedDataFrame,
    csv_dir,
    eval_dir,
)
from scripts.web.jobs import JobContext


def _find_csv_files() -> list[Path]:
    """List wrestler CSV files to process, excluding the UNKNOWN sentinel.

    Returns:
        The sorted CSV paths under csv_dir, minus UNKNOWN.csv.
    """
    return sorted(p for p in csv_dir.glob("*.csv") if p.name != "UNKNOWN.csv")


def run_team_eval_job(ctx: JobContext) -> dict[str, Any]:
    """Generate the multi-sheet Excel report from compiled wrestler data.

    Args:
        ctx: Job context for progress reporting.

    Returns:
        A dict describing the result: ``{"output": "Team_Stats.xlsx",
        "processed": [names], "errors": [...]}``.

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

    try:
        with pd.ExcelWriter(excel_file, engine="openpyxl", mode="w") as writer:
            for i, csv_file in enumerate(csv_files):
                sheet_name: str = csv_file.stem
                try:
                    raw_df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
                    GenerateInitiationDF(raw_df).to_excel(writer, sheet_name=sheet_name)
                    GenerateDefenseDF(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=4
                    )
                    GenerateOffenseDF(raw_df).to_excel(
                        writer, sheet_name=sheet_name, startrow=4, startcol=8
                    )
                    raw_df.to_excel(
                        writer, sheet_name=sheet_name, startrow=4, startcol=16
                    )
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
