"""Tests for helper functions involving subprocess calls and file I/O."""

import sys
import subprocess
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import MakeNameAndCSV, GetVidDuration


# ---------- MakeNameAndCSV ----------

def _make_fake_ffprobe_output(
    chapter_lines: list[str], name: str = "Test Wrestler", match_date: str = ""
) -> str:
    """Build a fake ffprobe -of csv stdout string."""
    lines: list[str] = []
    for i, title in enumerate(chapter_lines, start=1):
        lines.append(
            f"chapter,{i},1/1000000000,0,0.000000,6000000000,6.000000,{title}"
        )
    tail: str = f"format,{name}"
    if match_date:
        tail += f",{match_date}"
    lines.append(tail)
    return "\n".join(lines)


FAKE_CHAPTER_CSV: str = _make_fake_ffprobe_output([
    '"A,collar tie,double,nothing,T,None"',
])


class TestMakeNameAndCSV:
    """Tests for MakeNameAndCSV — ffprobe output parsing."""

    def test_parses_chapter_and_name(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, FAKE_CHAPTER_CSV, "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("fake.mkv"))
        assert name == "Test Wrestler"
        assert match_date == ""
        assert "fake.mkv:1,0,6,A,collar tie,double,nothing,T,None" in data

    def test_parses_match_date(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,collar tie,double,nothing,T,None"',
        ], name="Dated Wrestler", match_date="2026-08-01")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("dated.mkv"))
        assert name == "Dated Wrestler"
        assert match_date == "2026-08-01"
        assert "dated.mkv:1,0,6,A,collar tie,double,nothing,T,None" in data

    def test_multiple_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,collar tie,double,nothing,T,None"',
            '"D,standing,sprawl,single,E,T"',
        ], name="Multi Wrestler")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("multi.mkv"))
        assert name == "Multi Wrestler"
        assert match_date == ""
        lines = data.strip().split("\n")
        assert len(lines) == 2

    def test_filters_empty_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,EMPTY,,,,,"',
            '"A,collar tie,double,nothing,T,None"',
        ], name="Filter Test")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("filter.mkv"))
        lines = data.strip().split("\n")
        assert len(lines) == 1
        assert "EMPTY" not in data

    def test_ffprobe_failure_returns_empty(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            raise subprocess.CalledProcessError(1, "ffprobe")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("fail.mkv"))
        assert name == ""
        assert match_date == ""
        assert data == ""

    def test_empty_output_returns_empty(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("empty.mkv"))
        assert name == ""
        assert match_date == ""
        assert data == ""

    def test_only_empty_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,EMPTY,,,,,"',
        ], name="Only Empty")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        name, match_date, data = MakeNameAndCSV(Path("only_empty.mkv"))
        assert name == "Only Empty"
        assert match_date == ""
        assert data == ""


# ---------- GetVidDuration ----------

class TestGetVidDuration:
    """Tests for GetVidDuration — ffprobe duration parsing."""

    def test_returns_duration_in_seconds(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "123.456", "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 123

    def test_zero_duration(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "0.0", "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0

    def test_failure_returns_zero(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            raise subprocess.CalledProcessError(1, "ffprobe")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0

    def test_bad_float_returns_zero(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "not-a-number", "")

        monkeypatch.setattr("scripts.helpers.run_tracked", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0


