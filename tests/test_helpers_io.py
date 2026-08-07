"""Tests for helper functions involving subprocess calls and file I/O."""

import sys
import subprocess
from pathlib import Path

import pytest
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import (
    COL_ATTACKING, COL_START_TIME, COL_END_TIME, COL_TIE_UP,
    COL_TEAM_MOVES, COL_OPPONENT_MOVES, COL_TEAM_SCORES, COL_OPPONENT_SCORES, COL_NET_POINTS,
    MakeNameAndCSV, GetVidDuration, NameProbe,
    MakeFormattedDataFrame, LoadAllWrestlerData,
)


# ---------- MakeNameAndCSV ----------

def _make_fake_ffprobe_output(chapter_lines: list[str], name: str = "Test Wrestler") -> str:
    """Build a fake ffprobe -of csv stdout string."""
    lines: list[str] = []
    for i, title in enumerate(chapter_lines, start=1):
        lines.append(
            f"chapter,{i},1/1000000000,0,0.000000,6000000000,6.000000,{title}"
        )
    lines.append(f"format,{name}")
    return "\n".join(lines)


FAKE_CHAPTER_CSV: str = _make_fake_ffprobe_output([
    '"A,collar tie,double,nothing,T,None"',
])


class TestMakeNameAndCSV:
    """Tests for MakeNameAndCSV — ffprobe output parsing."""

    def test_parses_chapter_and_name(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, FAKE_CHAPTER_CSV, "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("fake.mkv"))
        assert name == "Test Wrestler"
        assert "fake.mkv:1,0,6,A,collar tie,double,nothing,T,None" in data

    def test_multiple_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,collar tie,double,nothing,T,None"',
            '"D,standing,sprawl,single,E,T"',
        ], name="Multi Wrestler")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("multi.mkv"))
        assert name == "Multi Wrestler"
        lines = data.strip().split("\n")
        assert len(lines) == 2

    def test_filters_empty_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,EMPTY,,,,,"',
            '"A,collar tie,double,nothing,T,None"',
        ], name="Filter Test")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("filter.mkv"))
        lines = data.strip().split("\n")
        assert len(lines) == 1
        assert "EMPTY" not in data

    def test_ffprobe_failure_returns_empty(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            raise subprocess.CalledProcessError(1, "ffprobe")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("fail.mkv"))
        assert name == ""
        assert data == ""

    def test_empty_output_returns_empty(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("empty.mkv"))
        assert name == ""
        assert data == ""

    def test_only_empty_chapters(self, monkeypatch) -> None:
        csv_output: str = _make_fake_ffprobe_output([
            '"A,EMPTY,,,,,"',
        ], name="Only Empty")

        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, csv_output, "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        name, data = MakeNameAndCSV(Path("only_empty.mkv"))
        assert name == "Only Empty"
        assert data == ""


# ---------- GetVidDuration ----------

class TestGetVidDuration:
    """Tests for GetVidDuration — ffprobe duration parsing."""

    def test_returns_duration_in_seconds(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "123.456", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 123

    def test_zero_duration(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "0.0", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0

    def test_failure_returns_zero(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            raise subprocess.CalledProcessError(1, "ffprobe")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0

    def test_bad_float_returns_zero(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "not-a-number", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: int = GetVidDuration(Path("fake.mkv"))
        assert result == 0


# ---------- NameProbe ----------

class TestNameProbe:
    """Tests for NameProbe — ffprobe title extraction."""

    def test_returns_name(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess([], 0, "John Smith", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: str = NameProbe(Path("fake.mkv"))
        assert result == "John Smith"

    def test_failure_returns_empty(self, monkeypatch) -> None:
        def mock_run(*args, **kwargs) -> subprocess.CompletedProcess:
            raise subprocess.CalledProcessError(1, "ffprobe")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: str = NameProbe(Path("fake.mkv"))
        assert result == ""


# ---------- _ProcessVideoSafely ----------

class TestProcessVideoSafely:
    """Tests for workers._ProcessVideoSafely — multiprocessing-safe wrapper."""

    def test_returns_name_and_data(self, monkeypatch) -> None:
        from scripts.qt_app.workers import _ProcessVideoSafely

        def mock_make_name_and_csv(path: Path) -> tuple[str, str]:
            return ("Alice", "csv data")

        monkeypatch.setattr("scripts.qt_app.workers.MakeNameAndCSV", mock_make_name_and_csv)

        name, data = _ProcessVideoSafely(Path("alice.mkv"))
        assert name == "Alice"
        assert data == "csv data"

    def test_returns_error_on_exception(self, monkeypatch) -> None:
        from scripts.qt_app.workers import _ProcessVideoSafely

        def mock_make_name_and_csv(path: Path) -> tuple[str, str]:
            raise RuntimeError("boom")

        monkeypatch.setattr("scripts.qt_app.workers.MakeNameAndCSV", mock_make_name_and_csv)

        name, data = _ProcessVideoSafely(Path("broken.mkv"))
        assert name is None
        assert "broken.mkv" in data
        assert "boom" in data


# ---------- MakeFormattedDataFrame ----------

class TestMakeFormattedDataFrame:
    """Tests for MakeFormattedDataFrame — CSV parsing into typed DataFrame."""

    CSV_CONTENT: str = (
        '"vid.mkv:1",0,6,A,collar tie,"high crotch:double","sprawl","T:N2","E"\n'
        '"vid.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T"'
    )

    def test_parses_csv_with_correct_columns(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert COL_ATTACKING in df.columns
        assert COL_START_TIME in df.columns
        assert COL_END_TIME in df.columns
        assert COL_TIE_UP in df.columns
        assert COL_TEAM_MOVES in df.columns
        assert COL_OPPONENT_MOVES in df.columns
        assert COL_TEAM_SCORES in df.columns
        assert COL_OPPONENT_SCORES in df.columns

    def test_converts_attack_column_to_bool(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert df[COL_ATTACKING].iloc[0] == True
        assert df[COL_ATTACKING].iloc[1] == False

    def test_splits_list_columns(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert df[COL_TEAM_MOVES].iloc[0] == ["high crotch", "double"]
        assert df[COL_OPPONENT_MOVES].iloc[0] == ["sprawl"]
        assert df[COL_TEAM_SCORES].iloc[0] == ["T", "N2"]

    def test_sets_origin_as_index(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert df.index.name == "Origin"
        assert df.index[0] == "vid.mkv:1"

    def test_adds_net_points_column(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert COL_NET_POINTS in df.columns
        assert df[COL_NET_POINTS].iloc[0] == 4  # T(3) + N2(2) - E(1)

    def test_converts_time_columns_to_int(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "test.csv"
        csv_file.write_text(self.CSV_CONTENT)

        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert df[COL_START_TIME].iloc[0] == 0
        assert df[COL_END_TIME].iloc[0] == 6

    def test_empty_csv(self, tmp_path) -> None:
        csv_file: Path = tmp_path / "empty.csv"
        csv_file.write_text("")
        df: pd.DataFrame = MakeFormattedDataFrame(csv_file)
        assert len(df) == 0


# ---------- LoadAllWrestlerData ----------

class TestLoadAllWrestlerData:
    """Tests for LoadAllWrestlerData — directory globbing and loading."""

    CSV_LINE: str = '"v.mkv:1",0,6,A,collar tie,"double","sprawl","T","E"'

    def test_loads_all_csv_files(self, tmp_path) -> None:
        (tmp_path / "Alice.csv").write_text(self.CSV_LINE)
        (tmp_path / "Bob.csv").write_text(self.CSV_LINE)

        result: dict[str, pd.DataFrame] = LoadAllWrestlerData(tmp_path)
        assert len(result) == 2

    def test_skips_unknown_csv(self, tmp_path) -> None:
        (tmp_path / "Alice.csv").write_text(self.CSV_LINE)
        (tmp_path / "UNKNOWN.csv").write_text(self.CSV_LINE)

        result: dict[str, pd.DataFrame] = LoadAllWrestlerData(tmp_path)
        assert "UNKNOWN" not in result
        assert len(result) == 1

    def test_empty_directory(self, tmp_path) -> None:
        result: dict[str, pd.DataFrame] = LoadAllWrestlerData(tmp_path)
        assert result == {}

    def test_skips_bad_csv_files(self, tmp_path) -> None:
        (tmp_path / "Alice.csv").write_text(self.CSV_LINE)
        (tmp_path / "Bad.csv").write_text("this,is,not,valid,csv,data,for,the,expected,format")

        result: dict[str, pd.DataFrame] = LoadAllWrestlerData(tmp_path)
        assert "Alice" in result
        assert len(result) == 1


# ---------- LoadConfigItems ----------

class TestLoadConfigItems:
    """Tests for LoadConfigItems from helpers.py — config file parser."""

    def test_loads_items_skipping_comments(self, tmp_path) -> None:
        from scripts.helpers import LoadConfigItems

        cfg: Path = tmp_path / "test.config"
        cfg.write_text(
            "# This is a comment\n"
            "  # indented comment\n"
            "\n"
            "  move one  \n"
            "move two\n"
            "   \n"
        )
        items: list[str] = LoadConfigItems(cfg)
        assert items == ["move one", "move two"]

    def test_empty_file(self, tmp_path) -> None:
        from scripts.helpers import LoadConfigItems

        cfg: Path = tmp_path / "empty.config"
        cfg.write_text("")
        items: list[str] = LoadConfigItems(cfg)
        assert items == []

    def test_all_comments(self, tmp_path) -> None:
        from scripts.helpers import LoadConfigItems

        cfg: Path = tmp_path / "comments.config"
        cfg.write_text("# comment 1\n# comment 2\n")
        items: list[str] = LoadConfigItems(cfg)
        assert items == []

    def test_file_not_found(self, tmp_path) -> None:
        from scripts.helpers import LoadConfigItems

        items: list[str] = LoadConfigItems(tmp_path / "nonexistent.config")
        assert items == []
