"""
Tests for the helper functions and classes in scripts/helpers.py.
"""
import sys
from pathlib import Path
import pytest
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
            return subprocess.CompletedProcess([], 0, "0,h264\n1,aac", "")

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
        # An empty match date keeps the row at 13 fields so a mix of dated and
        # undated rows stays parseable by MakeFormattedDataFrame.
        assert lines[1] == (
            "a.mkv:2,6,12,A,front headlock:standing,single,sprawl,None,E,1,1,W,"
        )

    def test_swap_perspective_csv_keeps_12_field_rows_unchanged(self) -> None:
        from scripts.helpers import SwapPerspectiveCSV

        data: str = "a.mkv:1,0,6,A,collar tie,double,nothing,T,None,3,3,W"
        assert SwapPerspectiveCSV(data) == (
            "a.mkv:1,0,6,D,collar tie,nothing,double,None,T,-3,-3,L"
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


class TestQueryBuilder:
    """Tests for the QueryBuilder class."""

    @staticmethod
    def _make_df() -> DataFrame:
        """Build a small DataFrame covering every column the QueryBuilder uses."""
        from scripts.helpers import (
            COL_ORIGIN, COL_START_TIME, COL_END_TIME, COL_ATTACKING,
            COL_TIE_UP, COL_TEAM_MOVES, COL_OPPONENT_MOVES,
            COL_NET_POINTS, COL_ADJUSTED_NET_POINTS,
            COL_MATCH_RESULT, COL_MATCH_DATE,
        )
        return DataFrame({
            COL_ORIGIN: ["v1.mkv:1", "v1.mkv:2", "v2.mkv:1", "v3.mkv:1", "v3.mkv:2"],
            COL_START_TIME: [0, 30, 0, 10, 50],
            COL_END_TIME:   [15, 60, 20, 25, 70],
            COL_ATTACKING:  [True, False, True, True, False],
            COL_TIE_UP:     ["front headlock", "collar tie:underhook",
                             "front headlock", "standing", "collar tie:underhook"],
            COL_TEAM_MOVES: [["half nelson", "tilt"], ["single leg"],
                             ["half nelson"], ["high crotch"], ["single leg", "dump"]],
            COL_OPPONENT_MOVES: [["sprawl"], ["front headlock", "whizzer"],
                                 ["whizzer"], ["sprawl"], ["front headlock"]],
            COL_NET_POINTS: [3, -1, 2, 5, -2],
            COL_ADJUSTED_NET_POINTS: [5, -1, 2, 5, -3],
            COL_MATCH_RESULT: ["W", "L", "W", "W", "L"],
            COL_MATCH_DATE: ["2025-01-10", "2025-02-15", "2025-03-01", "2025-01-20", "2025-04-01"],
        })

    # -- attack_filter --

    def test_attack_filter_all(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).attack_filter("All").apply()
        assert len(result) == 5

    def test_attack_filter_attacking(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).attack_filter("Attacking").apply()
        assert len(result) == 3
        assert all(result["Attacking"])

    def test_attack_filter_defending(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).attack_filter("Defending").apply()
        assert len(result) == 2
        assert not any(result["Attacking"])

    # -- tie_filter --

    def test_tie_filter_matches(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).tie_filter("front headlock").apply()
        assert len(result) == 2

    def test_tie_filter_no_match(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).tie_filter("standing").apply()
        assert len(result) == 1

    def test_tie_filter_empty_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).tie_filter("").apply()
        assert len(result) == 5

    def test_tie_filter_whitespace_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).tie_filter("   ").apply()
        assert len(result) == 5

    # -- opp_tie_filter --

    def test_opp_tie_filter_matches(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).opp_tie_filter("underhook").apply()
        assert len(result) == 2

    def test_opp_tie_filter_empty_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).opp_tie_filter("").apply()
        assert len(result) == 5

    # -- team_move_filter --

    def test_team_move_filter_substring(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).team_move_filter("half").apply()
        assert len(result) == 2

    def test_team_move_filter_exact(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).team_move_filter("single leg", exact=True).apply()
        assert len(result) == 2

    def test_team_move_filter_exact_no_substring(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).team_move_filter("single", exact=True).apply()
        assert len(result) == 0

    def test_team_move_filter_empty_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).team_move_filter("").apply()
        assert len(result) == 5

    # -- opp_move_filter --

    def test_opp_move_filter_substring(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).opp_move_filter("sprawl").apply()
        assert len(result) == 2

    def test_opp_move_filter_exact(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).opp_move_filter("whizzer", exact=True).apply()
        assert len(result) == 2

    # -- match_result_filter --

    def test_match_result_filter_wins(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).match_result_filter("W").apply()
        assert len(result) == 3

    def test_match_result_filter_losses(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).match_result_filter("L").apply()
        assert len(result) == 2

    def test_match_result_filter_empty_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).match_result_filter("").apply()
        assert len(result) == 5

    # -- date_range_filter --

    def test_date_range_filter_min_only(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).date_range_filter(min_date="2025-03-01").apply()
        assert len(result) == 2

    def test_date_range_filter_max_only(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).date_range_filter(max_date="2025-02-15").apply()
        assert len(result) == 3

    def test_date_range_filter_both(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).date_range_filter(
            min_date="2025-01-10", max_date="2025-02-15"
        ).apply()
        assert len(result) == 3

    def test_date_range_filter_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).date_range_filter().apply()
        assert len(result) == 5

    # -- duration_filter --

    def test_duration_filter_min(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).duration_filter(min_seconds=20).apply()
        assert len(result) == 3

    def test_duration_filter_max(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).duration_filter(max_seconds=15).apply()
        assert len(result) == 2

    def test_duration_filter_range(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).duration_filter(min_seconds=20, max_seconds=30).apply()
        assert len(result) == 3

    # -- net_points_filter --

    def test_net_points_filter(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).net_points_filter(min=0, max=3).apply()
        assert len(result) == 2

    # -- adjusted_net_points_filter --

    def test_adjusted_net_points_filter(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).adjusted_net_points_filter(min=5, max=5).apply()
        assert len(result) == 2

    # -- apply sort + paginate --

    def test_apply_sort_ascending(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).apply(
            sort_by="Adjusted Net Points", ascending=True
        )
        vals = list(result["Adjusted Net Points"])
        assert vals == sorted(vals)

    def test_apply_sort_descending(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).apply(
            sort_by="Adjusted Net Points", ascending=False
        )
        vals = list(result["Adjusted Net Points"])
        assert vals == sorted(vals, reverse=True)

    def test_apply_limit(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).apply(limit=2)
        assert len(result) == 2

    def test_apply_offset(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        all_rows = QueryBuilder(df).apply()
        offset_rows = QueryBuilder(df).apply(offset=3)
        assert len(offset_rows) == 2
        assert list(offset_rows.index) == list(all_rows.index[3:])

    def test_apply_limit_and_offset(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).apply(limit=2, offset=1)
        assert len(result) == 2

    def test_apply_sort_nonexistent_column_is_noop(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = QueryBuilder(df).apply(sort_by="nonexistent")
        assert len(result) == 5

    # -- chaining --

    def test_chained_filters(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = (
            QueryBuilder(df)
            .attack_filter("Attacking")
            .tie_filter("front headlock")
            .team_move_filter("half")
            .apply()
        )
        assert len(result) == 2

    def test_chained_filters_with_sort_and_limit(self) -> None:
        from scripts.helpers import QueryBuilder
        df = self._make_df()
        result = (
            QueryBuilder(df)
            .attack_filter("Attacking")
            .adjusted_net_points_filter(min=0)
            .apply(sort_by="Net Points", ascending=False, limit=2)
        )
        assert len(result) == 2
        vals = list(result["Net Points"])
        assert vals == sorted(vals, reverse=True)

    # -- duration column --

    def test_duration_column_computed(self) -> None:
        from scripts.helpers import QueryBuilder, COL_MATCH_DURATION
        df = self._make_df()
        result = QueryBuilder(df).duration_filter(min_seconds=0).apply()
        assert COL_MATCH_DURATION in result.columns
        assert list(result[COL_MATCH_DURATION]) == [15, 30, 20, 15, 20]
