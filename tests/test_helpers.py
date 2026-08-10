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

    def test_load_all_wrestler_data_returns_dict(self, tmp_path, monkeypatch) -> None:
        import scripts.helpers as helpers
        from scripts.helpers import LoadAllWrestlerData

        data_dir = tmp_path / "csv"
        data_dir.mkdir()
        (data_dir / "Alice.csv").write_text(
            'match.mkv:1,0,10,A,collar tie,double,sprawl,T,None\n'
        )
        (data_dir / "UNKNOWN.csv").write_text("")
        monkeypatch.setattr(helpers, "csv_dir", data_dir)

        result: dict[str, DataFrame] = LoadAllWrestlerData()
        assert isinstance(result, dict)
        assert list(result) == ["Alice"]
        assert not result["Alice"].empty

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

    def test_build_tag_title_single(self) -> None:
        from scripts.helpers import BuildTagTitle

        assert BuildTagTitle("Alice") == "Alice"
        assert BuildTagTitle("Alice", match_result="W") == "Alice (W)"
        assert BuildTagTitle("Alice", match_result="L") == "Alice (L)"

    def test_build_tag_title_dual(self) -> None:
        from scripts.helpers import BuildTagTitle

        assert BuildTagTitle("Alice", "Bob") == "Alice / Bob"
        assert BuildTagTitle("Alice", "Bob", "W") == "Alice (W) / Bob"
        assert BuildTagTitle("Alice", "Bob", "L") == "Alice / Bob (W)"

    def test_build_tag_title_invalid_result(self) -> None:
        from scripts.helpers import BuildTagTitle

        with pytest.raises(ValueError):
            BuildTagTitle("Alice", match_result="D")

    def test_parse_tagged_name_single(self) -> None:
        from scripts.helpers import ParseTaggedName

        assert ParseTaggedName("Alice") == ("Alice", None, "")
        assert ParseTaggedName("Alice (W)") == ("Alice", None, "W")
        assert ParseTaggedName("Alice (L)") == ("Alice", None, "L")

    def test_parse_tagged_name_dual(self) -> None:
        from scripts.helpers import ParseTaggedName

        assert ParseTaggedName("Alice / Bob") == ("Alice", "Bob", "")
        assert ParseTaggedName("Alice (W) / Bob") == ("Alice", "Bob", "W")
        assert ParseTaggedName("Alice / Bob (W)") == ("Alice", "Bob", "L")

    def test_build_tie_entry(self) -> None:
        from scripts.helpers import BuildTieEntry

        assert BuildTieEntry("collar tie") == "collar tie"
        assert BuildTieEntry("collar tie", "underhook") == "collar tie:underhook"
        assert BuildTieEntry("collar tie", "  ") == "collar tie"

    def test_swap_perspective_csv(self) -> None:
        from scripts.helpers import SwapPerspectiveCSV

        data: str = (
            "a.mkv:1,0,6,A,collar tie:underhook,double,nothing,T,None,3,3,W\n"
            "a.mkv:2,6,12,D,standing:front headlock,sprawl,single,E,None,-1,-1,L"
        )
        lines: list[str] = SwapPerspectiveCSV(data).splitlines()
        assert lines[0] == (
            "a.mkv:1,0,6,D,underhook:collar tie,nothing,double,None,T,-3,-3,L"
        )
        assert lines[1] == (
            "a.mkv:2,6,12,A,front headlock:standing,single,sprawl,None,E,1,1,W"
        )

    def test_swap_perspective_csv_passes_through_unknown(self) -> None:
        from scripts.helpers import SwapPerspectiveCSV

        assert SwapPerspectiveCSV("bad,row") == "bad,row"

    def test_swap_perspective_csv_keeps_match_date(self) -> None:
        from scripts.helpers import SwapPerspectiveCSV

        data: str = (
            "a.mkv:1,0,6,A,collar tie:underhook,double,nothing,T,None,3,3,W,2026-08-01\n"
            "a.mkv:2,6,12,D,standing:front headlock,sprawl,single,E,None,-1,-1,L,"
        )
        lines: list[str] = SwapPerspectiveCSV(data).splitlines()
        assert lines[0] == (
            "a.mkv:1,0,6,D,underhook:collar tie,nothing,double,None,T,-3,-3,L,2026-08-01"
        )
        # An empty match date is not re-appended, so the row stays 12 fields.
        assert lines[1] == (
            "a.mkv:2,6,12,A,front headlock:standing,single,sprawl,None,E,1,1,W"
        )

    def test_validate_match_date(self) -> None:
        from scripts.helpers import ValidateMatchDate

        assert ValidateMatchDate("") == ""
        assert ValidateMatchDate("  ") == ""
        assert ValidateMatchDate("2026-08-01") == "2026-08-01"
        assert ValidateMatchDate(" 2026-08-01 ") == "2026-08-01"
        with pytest.raises(ValueError, match="match date"):
            ValidateMatchDate("08/01/2026")
        with pytest.raises(ValueError, match="match date"):
            ValidateMatchDate("2026-8-1")
