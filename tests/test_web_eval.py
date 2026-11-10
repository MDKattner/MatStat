"""Tests for the web Team Evaluation flow — the job body and API routes."""

import sys
import time
from pathlib import Path
from typing import Any

import openpyxl
import pytest

sys.path.append(str(Path(__file__).parent.parent))

from conftest import FakeContext

from scripts.helpers import MatchOutcomeConflictError
from scripts.web import app as web_app
from scripts.web import stats
from scripts.web import team_eval

CSV_ROW: str = '"vid.mkv:1",0,6,A,collar tie,"double","sprawl","T","E"'
CSV_ROW2: str = '"vid.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T"'


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the team_eval and web_app data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "csv": tmp_path / "csv",
        "eval": tmp_path / "eval",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(team_eval, "csv_dir", dirs["csv"])
    monkeypatch.setattr(team_eval, "eval_dir", dirs["eval"])
    monkeypatch.setattr(web_app, "csv_dir", dirs["csv"])
    monkeypatch.setattr(web_app, "eval_dir", dirs["eval"])
    monkeypatch.setattr(stats, "csv_dir", dirs["csv"])
    return dirs


def _write_csv(tmp_path: Path, name: str, content: str = CSV_ROW) -> None:
    (tmp_path / "csv" / f"{name}.csv").write_text(content)


def _report_path(tmp_path: Path) -> Path:
    """Resolve the latest report pointer to the concrete workbook."""
    return (tmp_path / "eval" / team_eval.LATEST_NAME).resolve()


def _reports(tmp_path: Path) -> list[Path]:
    """List the timestamped report workbooks in the eval directory."""
    return sorted(
        p for p in (tmp_path / "eval").iterdir()
        if team_eval._REPORT_RE.match(p.name)
    )


def _wait_for_job(client, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline: float = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        job: dict[str, Any] = resp.json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")


class TestRunTeamEvalJob:
    """Tests for team_eval.run_team_eval_job — the Excel generation job."""

    def test_writes_excel_with_sheet_per_wrestler(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")
        _write_csv(tmp_path, "Bob Smith")
        _write_csv(tmp_path, "UNKNOWN")

        ctx: FakeContext = FakeContext()
        result: dict[str, Any] = team_eval.run_team_eval_job(ctx)

        assert result["output"].startswith("Team_Stats_")
        assert result["output"].endswith(".xlsx")
        assert result["latest"] == team_eval.LATEST_NAME
        assert result["processed"] == ["Alice", "Bob Smith"]
        assert result["errors"] == []
        assert result["failed_sheets"] == []
        assert result["partial"] is False
        report: Path = _report_path(tmp_path)
        assert report.is_file()
        assert report.name == result["output"]
        wb = openpyxl.load_workbook(report)
        assert wb.sheetnames == [
            "Read Me", "Team Summary", "Match Outcomes", "Alice", "Bob Smith",
        ]
        assert ctx.reports[-1] == (100, f"Report generated: {result['output']}")

    def test_section_labels_and_layout(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        team_eval.run_team_eval_job(FakeContext())

        ws = openpyxl.load_workbook(_report_path(tmp_path))["Alice"]
        assert ws["A1"].value == "Initiation"
        assert ws["A1"].font.bold is True
        assert ws["A7"].value == "Rates"
        assert ws["A10"].value == "Defense"
        assert ws["I10"].value == "Offense"
        assert ws["Q10"].value == "Raw Data"
        assert ws["A2"].value == "Segment"
        assert ws["A8"].value == "Metric"
        assert ws["A11"].value == "Move"
        assert ws["I11"].value == "Move"
        assert ws["Q11"].value == "Origin"

    def test_team_summary_sheet(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")
        _write_csv(tmp_path, "Bob", content=CSV_ROW2)

        team_eval.run_team_eval_job(FakeContext())

        ws = openpyxl.load_workbook(_report_path(tmp_path))["Team Summary"]
        assert ws["A1"].value == "Wrestler"
        assert ws["B1"].value == "Matches"
        assert ws["C1"].value == "Net Points per Match"
        assert ws["I1"].value == "Net Points per Match (z)"
        assert ws["A2"].value == "Alice"
        assert ws["A3"].value == "Bob"
        assert ws["A4"].value == "Team Mean"

    def test_initiation_segments_in_report(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        content: str = (
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
            '"b.mkv:1",6,12,D,standing,"sprawl","sweep single","E","T",-4,-4,L\n'
            '"c.mkv:1",12,18,A,front headlock,"single","whizzer","T","",3,3,\n'
        )
        _write_csv(tmp_path, "Alice", content=content)

        team_eval.run_team_eval_job(FakeContext())

        ws = openpyxl.load_workbook(_report_path(tmp_path))["Alice"]
        assert ws["A2"].value == "Segment"
        assert ws["A3"].value == "All"
        assert ws["A4"].value == "Wins"
        assert ws["A5"].value == "Losses"
        assert ws["A6"].value == "Unrecorded"
        # W row is attacking (count 1); L row is not. "Sequences" is column B,
        # so "Attack Count" is column C.
        assert ws["B4"].value == 1
        assert ws["B5"].value == 1
        assert ws["B6"].value == 1
        assert ws["C4"].value == 1
        assert ws["C5"].value == 0
        # All includes all three rows, two of which attack.
        assert ws["B3"].value == 3
        assert ws["C3"].value == 2
        # The unrecorded row is attacking (count 1).
        assert ws["C6"].value == 1

    def test_match_outcomes_sheet(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        content: str = (
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
            '"a.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T",-2,-2,W\n'
            '"b.mkv:1",12,18,A,front headlock,"single","whizzer","T","",3,3,L\n'
            '"c.mkv:1",18,24,D,underhook,"sprawl","double","","E",-1,-1,\n'
        )
        _write_csv(tmp_path, "Alice", content=content)
        _write_csv(tmp_path, "Bob", content='"b.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,')

        team_eval.run_team_eval_job(FakeContext())

        ws = openpyxl.load_workbook(_report_path(tmp_path))["Match Outcomes"]
        assert ws["A1"].value == "Wrestler"
        assert ws["B1"].value == "Matches"
        assert ws["C1"].value == "W"
        assert ws["D1"].value == "L"
        assert ws["E1"].value == "Unrecorded"
        assert ws["F1"].value == "Recorded"
        assert ws["G1"].value == "Win Rate"
        assert ws["A2"].value == "Alice"
        assert ws["B2"].value == 3
        assert ws["C2"].value == 1
        assert ws["D2"].value == 1
        assert ws["E2"].value == 1
        assert ws["F2"].value == 2
        assert ws["G2"].value == 0.5
        assert ws["A3"].value == "Bob"
        assert ws["B3"].value == 1
        assert ws["C3"].value == 0
        assert ws["D3"].value == 0
        assert ws["E3"].value == 1
        assert ws["F3"].value == 0
        assert ws["G3"].value is None

    def test_conflicting_results_abort_before_writing(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        content: str = (
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
            '"a.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T",-2,-2,L\n'
        )
        _write_csv(tmp_path, "Alice", content=content)

        with pytest.raises(MatchOutcomeConflictError, match="a.mkv"):
            team_eval.run_team_eval_job(FakeContext())

        assert not (tmp_path / "eval" / team_eval.LATEST_NAME).exists()
        assert _reports(tmp_path) == []

    def test_per_file_error_keeps_others(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")
        _write_csv(tmp_path, "Broken", content='"v.mkv:1",abc,6,A,collar tie,"double","sprawl","T","E"')

        result: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())

        assert result["processed"] == ["Alice"]
        assert len(result["errors"]) == 1
        assert "Broken" in result["errors"][0]
        wb = openpyxl.load_workbook(_report_path(tmp_path))
        assert wb.sheetnames == [
            "Read Me", "Team Summary", "Match Outcomes", "Alice",
        ]

    def test_no_csv_files_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="No wrestler data files"):
            team_eval.run_team_eval_job(FakeContext())

    def test_skips_unknown_csv(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "UNKNOWN")

        with pytest.raises(ValueError, match="No wrestler data files"):
            team_eval.run_team_eval_job(FakeContext())


class TestReportReliability:
    """DI-5: atomic, versioned, provenance-aware, fail-safe reports."""

    def test_read_me_records_provenance_and_definitions(
        self, tmp_path, monkeypatch
    ) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        team_eval.run_team_eval_job(FakeContext())

        ws = openpyxl.load_workbook(_report_path(tmp_path))["Read Me"]
        fields: dict[str, str] = {
            str(row[0].value): str(row[1].value)
            for row in ws.iter_rows(min_row=2)
            if row[0].value
        }
        assert fields["Active ruleset"] == "Folkstyle"
        assert len(fields["Ruleset hash"]) == 64
        assert fields["Wrestlers"] == "1"
        assert fields["Matches"] == "1"
        assert "Full roster" in fields["Report scope"]
        assert "Alice.csv" in fields["Source files"]
        assert "Win Rate" in fields
        assert "blank when no outcome" in fields["Win Rate"]

    def test_formatting_freeze_filters_and_number_formats(
        self, tmp_path, monkeypatch
    ) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        content: str = (
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
        )
        _write_csv(tmp_path, "Alice", content=content)

        team_eval.run_team_eval_job(FakeContext())

        wb = openpyxl.load_workbook(_report_path(tmp_path))
        summary = wb["Team Summary"]
        assert summary.freeze_panes == "A2"
        assert summary.auto_filter.ref is not None
        assert summary["B2"].number_format == "0.00"
        assert summary["A1"].alignment.wrap_text is True
        assert summary.sheet_properties.tabColor.rgb.endswith("2E7D32")

        outcomes = wb["Match Outcomes"]
        assert outcomes["G2"].number_format == "0%"
        headers: list[Any] = [cell.value for cell in outcomes[1]]
        assert "Pins by Wrestler" in headers
        assert "Times Wrestler Pinned" in headers

    def test_partial_failure_keeps_previous_latest(
        self, tmp_path, monkeypatch
    ) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        first: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())
        previous: str = first["output"]
        previous_bytes: bytes = _report_path(tmp_path).read_bytes()

        def broken(df):
            raise RuntimeError("boom")

        monkeypatch.setattr(team_eval, "GenerateDefenseDF", broken)
        second: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())

        assert second["partial"] is True
        assert second["failed_sheets"] == ["Alice"]
        assert second["output"] != previous
        assert _report_path(tmp_path).name == previous
        assert _report_path(tmp_path).read_bytes() == previous_bytes
        assert team_eval.latest_report_info()["filename"] == previous

    def test_atomic_replace_failure_keeps_previous(
        self, tmp_path, monkeypatch
    ) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        first: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())
        previous: str = first["output"]
        previous_bytes: bytes = _report_path(tmp_path).read_bytes()

        def broken_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(team_eval.os, "replace", broken_replace)
        with pytest.raises(RuntimeError, match="finalize"):
            team_eval.run_team_eval_job(FakeContext())

        assert _report_path(tmp_path).name == previous
        assert _report_path(tmp_path).read_bytes() == previous_bytes
        assert not list((tmp_path / "eval").glob("*.tmp.xlsx"))

    def test_cancel_leaves_previous_intact(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        first: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())
        cancelled: dict[str, Any] = team_eval.run_team_eval_job(
            FakeContext(cancelled=True)
        )

        assert cancelled["cancelled"] is True
        assert cancelled["output"] == ""
        assert _report_path(tmp_path).name == first["output"]
        assert len(_reports(tmp_path)) == 1

    def test_retention_keeps_ten_reports(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        for _ in range(11):
            result: dict[str, Any] = team_eval.run_team_eval_job(FakeContext())

        reports: list[Path] = _reports(tmp_path)
        assert len(reports) == 10
        assert result["output"] in {p.name for p in reports}
        assert _report_path(tmp_path).name == result["output"]
        assert len(list((tmp_path / "eval").glob("*.json"))) == 10


class TestTeamEvalRoute:
    """Tests for POST /api/team-eval and GET /api/reports/{file}."""

    def test_job_completes_and_download_works(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")

        resp = client.post("/api/team-eval")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"
        job_id: str = body["job_id"]

        job: dict[str, Any] = _wait_for_job(client, job_id)
        assert job["status"] == "done"
        assert job["result"]["processed"] == ["Alice"]
        output: str = job["result"]["output"]
        assert _report_path(tmp_path).name == output

        dl = client.get(f"/api/reports/{output}")
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert dl.headers["cache-control"] == "no-store"
        assert output in dl.headers["content-disposition"]
        assert dl.content.startswith(b"PK")  # xlsx is a zip archive

    def test_latest_report_metadata(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(tmp_path, "Alice")
        job: dict[str, Any] = _wait_for_job(
            client, client.post("/api/team-eval").json()["job_id"]
        )

        resp = client.get("/api/reports/latest")

        assert resp.status_code == 200
        info: dict[str, Any] = resp.json()
        assert info["filename"] == job["result"]["output"]
        assert info["latest"] == team_eval.LATEST_NAME
        assert info["wrestlers"] == 1
        assert info["matches"] == 1
        assert info["processed"] == ["Alice"]
        assert info["active_ruleset"] == "Folkstyle"
        assert info["mtime"] > 0

    def test_latest_report_404_when_missing(
        self, client, tmp_path, monkeypatch
    ) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/reports/latest")
        assert resp.status_code == 404

    def test_no_data_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.post("/api/team-eval")
        assert resp.status_code == 404
        assert "No wrestler data" in resp.json()["detail"]

    def test_missing_report_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/reports/Team_Stats_20200101-000000.xlsx")
        assert resp.status_code == 404

    def test_invalid_report_name_400(self, client) -> None:
        resp = client.get("/api/reports/.hidden.xlsx")
        assert resp.status_code == 400
