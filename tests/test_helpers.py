"""
Tests for the helper functions and classes in scripts/helpers.py.
"""
import sys
from pathlib import Path
import pytest
import numpy as np
from pandas import DataFrame

# Add the script's parent directory to the Python path
# to allow for importing the helpers module.
sys.path.append(str(Path(__file__).parent.parent))
from scripts.helpers import ChapterSequence

@pytest.fixture
def sample_sequence() -> ChapterSequence:
    """Provides a standard ChapterSequence object for testing."""
    return ChapterSequence(
        start_time=60,
        end_time=75,
        attack_defend=True,
        tie_up="Collar Tie",
        team_moves=["High Crotch", "Lift"],
        op_moves=["Sprawl"],
        team_scores=["T", "N2"],
        op_scores=["E"]
    )

class TestChapterSequence:
    """Tests for the ChapterSequence data class."""

    def test_make_empty_chap(self):
        """Test the MakeEmptyChap factory method."""
        empty_chap = ChapterSequence.MakeEmptyChap(start=10, end=20)
        assert empty_chap.start_time == 10
        assert empty_chap.end_time == 20
        assert empty_chap.tie_up == "EMPTY"
        assert empty_chap.team_moves == []
        assert empty_chap.op_scores == []

    def test_from_csv_row(self):
        """Test the FromCSVRow factory method."""
        csv_row = 'video.mkv:ch1,60.0,75.0,A,Collar Tie,"High Crotch:Lift",Sprawl,"T:N2",E'
        sequence = ChapterSequence.FromCSVRow(csv_row)
        assert sequence.start_time == 60
        assert sequence.end_time == 75
        assert sequence.attack_defend is True
        assert sequence.tie_up == "Collar Tie"
        assert sequence.team_moves == ["High Crotch", "Lift"]
        assert sequence.op_moves == ["Sprawl"]
        assert sequence.team_scores == ["T", "N2"]
        assert sequence.op_scores == ["E"]

    def test_make_title(self, sample_sequence: ChapterSequence):
        """Test the MakeTitle representation method."""
        expected_title = "A,Collar Tie,High Crotch:Lift,Sprawl,T:N2,E"
        assert sample_sequence.MakeTitle() == expected_title

    def test_to_metadata(self, sample_sequence: ChapterSequence):
        """Test the ToMetadata representation method."""
        expected_metadata = (
            "[CHAPTER]\n"
            "TIMEBASE=1/1\n"
            "START=60\n"
            "END=75\n"
            "title=A,Collar Tie,High Crotch:Lift,Sprawl,T:N2,E\n"
        )
        assert sample_sequence.ToMetadata() == expected_metadata

    def test_pretty_chapter(self, sample_sequence: ChapterSequence):
        """Test the PrettyChapter representation method for human-readable output."""
        pretty_string = sample_sequence.PrettyChapter()
        assert "Start Time" in pretty_string
        assert "1:00" in pretty_string
        assert "End Time" in pretty_string
        assert "1:15" in pretty_string
        assert "High Crotch, Lift" in pretty_string
        assert "Sprawl" in pretty_string

class TestHelpers:
    """Tests for standalone helper functions in scripts/helpers.py."""

    def test_get_video_codecs_empty_on_failure(self) -> None:
        import subprocess
        from pathlib import Path
        from scripts.helpers import GetVideoCodecs

        result: dict[str, str] = GetVideoCodecs(Path("/nonexistent/video.mkv"))
        assert result == {}

    def test_load_all_wrestler_data_returns_dict(self) -> None:
        from scripts.helpers import LoadAllWrestlerData, csv_dir

        result: dict[str, DataFrame] = LoadAllWrestlerData(csv_dir)
        assert isinstance(result, dict)

    def test_get_video_codecs_returns_codecs(self, monkeypatch) -> None:
        import subprocess
        from pathlib import Path
        from scripts.helpers import GetVideoCodecs

        def mock_run(cmd: str, **kwargs) -> subprocess.CompletedProcess:
            if "select_streams v:0" in cmd:
                return subprocess.CompletedProcess([], 0, "h264", "")
            return subprocess.CompletedProcess([], 0, "aac", "")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result: dict[str, str] = GetVideoCodecs(Path("fake.mkv"))
        assert result == {"video": "h264", "audio": "aac"}
