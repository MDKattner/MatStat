"""Tests for helper functions: data transforms and statistics."""

import sys
from pathlib import Path

import pytest
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import (
    COL_TEAM_MOVES, COL_OPPONENT_MOVES, COL_TEAM_SCORES, COL_OPPONENT_SCORES,
    COL_START_TIME, COL_END_TIME, COL_ATTACKING, COL_TIE_UP, COL_NET_POINTS,
    COL_ADJUSTED_NET_POINTS, COL_MATCH_RESULT, COL_ORIGIN,
    CalculateNetPoints, TabulateNetPoints, AdjustedNetPoints,
    TabulateAdjustedNetPoints,
    PinCount,
    _MoveFilter,
    _GenerateMoveDF, GenerateOffenseDF, GenerateDefenseDF,
    GenerateInitiationDFBySegment, GenerateRatesDF, GenerateTeamSummaryDF,
    GenerateMatchOutcomesDF, MatchOutcomeConflictError, ValidateMatchOutcomes,
)


class TestCalculateNetPoints:
    """Tests for CalculateNetPoints — scoring per sequence."""

    def test_team_scores_only(self, sample_df: pd.DataFrame) -> None:
        row = sample_df.iloc[0]
        result: np.int16 = CalculateNetPoints(row)
        assert result == 4  # T(3) + N2(2) - E(1) = 4

    def test_opponent_scores_only(self, sample_df: pd.DataFrame) -> None:
        row = sample_df.iloc[1]
        result: np.int16 = CalculateNetPoints(row)
        assert result == -3  # 0 - T(3) = -3

    def test_both_scores(self, sample_df: pd.DataFrame) -> None:
        row = sample_df.iloc[2]
        result: np.int16 = CalculateNetPoints(row)
        assert result == 3  # T(3) - 0 = 3

    def test_no_scores(self, sample_df: pd.DataFrame) -> None:
        row = sample_df.iloc[4]
        result: np.int16 = CalculateNetPoints(row)
        assert result == 0  # None(0) - 0 = 0

    def test_empty_lists(self) -> None:
        row = pd.Series({
            COL_TEAM_SCORES: [],
            COL_OPPONENT_SCORES: [],
        })
        result: np.int16 = CalculateNetPoints(row)
        assert result == 0

    def test_unknown_score_is_skipped(self, monkeypatch) -> None:
        import logging
        from scripts.helpers import score_to_pts
        row = pd.Series({
            COL_TEAM_SCORES: ["UNKNOWN_CODE"],
            COL_OPPONENT_SCORES: [],
        })
        warnings: list[str] = []
        monkeypatch.setattr(logging, "warning", lambda msg: warnings.append(str(msg)))
        result: np.int16 = CalculateNetPoints(row)
        assert result == 0
        assert len(warnings) == 1
        assert "UNKNOWN_CODE" in warnings[0]


class TestTabulateNetPoints:
    """Tests for TabulateNetPoints — in-place column mutation."""

    def test_adds_column(self, simple_df: pd.DataFrame) -> None:
        df = simple_df.copy()
        if COL_NET_POINTS in df.columns:
            df.drop(columns=[COL_NET_POINTS], inplace=True)
        TabulateNetPoints(df)
        assert COL_NET_POINTS in df.columns
        assert df[COL_NET_POINTS].iloc[0] == 3

    def test_preserves_existing_data(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        original_shape = df.shape
        TabulateNetPoints(df)
        assert df.shape == original_shape
        assert COL_NET_POINTS in df.columns

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        df = empty_df.copy()
        TabulateNetPoints(df)
        assert COL_NET_POINTS in df.columns
        assert len(df) == 0


class TestPinCount:
    """Tests for PinCount."""

    def test_pin_in_team_scores(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        df.at[df.index[0], COL_TEAM_SCORES] = ["T", "PIN"]
        assert PinCount(df, COL_TEAM_SCORES) == 1

    def test_pin_in_opponent_scores(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        df.at[df.index[0], COL_OPPONENT_SCORES] = ["PIN"]
        assert PinCount(df, COL_OPPONENT_SCORES) == 1

    def test_no_pins(self, sample_df: pd.DataFrame) -> None:
        assert PinCount(sample_df, COL_TEAM_SCORES) == 0

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        assert PinCount(empty_df, COL_TEAM_SCORES) == 0

    def test_non_list_returns_zero(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        df[COL_TEAM_SCORES] = "not_a_list"
        assert PinCount(df, COL_TEAM_SCORES) == 0


class TestMoveFilter:
    """Tests for _MoveFilter and convenience wrappers."""

    def test_filter_finds_move(self, simple_df: pd.DataFrame) -> None:
        filt = _MoveFilter("double", COL_TEAM_MOVES)
        result = simple_df.apply(filt, axis=1, result_type="broadcast")
        result = result.dropna(how="all")
        assert len(result) == 1
        assert result[COL_START_TIME].iloc[0] == 0

    def test_filter_no_match(self, simple_df: pd.DataFrame) -> None:
        filt = _MoveFilter("nonexistent", COL_TEAM_MOVES)
        result = simple_df.apply(filt, axis=1, result_type="broadcast")
        result = result.dropna(how="all")
        assert len(result) == 0

    def test_exact_match_not_substring(self, simple_df: pd.DataFrame) -> None:
        filt = _MoveFilter("single", COL_TEAM_MOVES)
        result = simple_df.apply(filt, axis=1, result_type="broadcast")
        result = result.dropna(how="all")
        assert len(result) == 0


class TestGenerateMoveDF:
    """Tests for _GenerateMoveDF, GenerateOffenseDF, GenerateDefenseDF."""

    def test_generates_stats_dataframe(self, sample_df: pd.DataFrame) -> None:
        df = _GenerateMoveDF(sample_df, COL_TEAM_MOVES)
        assert "Count (occ.)" in df.columns
        assert "Net Points" in df.columns
        assert "Adjusted Net Pts" in df.columns
        assert "Average Net Points" in df.columns
        assert "Average Adjusted Net Points" in df.columns
        assert "Number of Pins" in df.columns
        assert "Times Pinned" in df.columns
        assert df.index.name == "Move"
        assert len(df) > 0

    def test_offense_df(self, sample_df: pd.DataFrame) -> None:
        df = GenerateOffenseDF(sample_df)
        assert len(df) > 0
        assert "Count (occ.)" in df.columns

    def test_defense_df(self, sample_df: pd.DataFrame) -> None:
        df = GenerateDefenseDF(sample_df)
        assert len(df) > 0
        assert "Count (occ.)" in df.columns

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        df = _GenerateMoveDF(empty_df, COL_TEAM_MOVES)
        assert len(df) == 0

    def test_average_net_points(self) -> None:
        df = pd.DataFrame({
            COL_START_TIME: [0, 10],
            COL_END_TIME: [10, 20],
            COL_TEAM_MOVES: [["double"], ["double"]],
            COL_OPPONENT_MOVES: [[], []],
            COL_TEAM_SCORES: [["T"], []],
            COL_OPPONENT_SCORES: [[], ["E"]],
            COL_NET_POINTS: [3, -1],
            COL_ADJUSTED_NET_POINTS: [3, -1],
        }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name="Origin"))
        result = _GenerateMoveDF(df, COL_TEAM_MOVES)
        assert result.loc["double", "Count (occ.)"] == 2
        assert result.loc["double", "Net Points"] == 2
        assert result.loc["double", "Adjusted Net Pts"] == 2
        assert result.loc["double", "Average Net Points"] == 1.0
        assert result.loc["double", "Average Adjusted Net Points"] == 1.0

    def test_duplicate_move_in_sequence_uses_occurrence_denominator(self) -> None:
        df = pd.DataFrame({
            COL_START_TIME: [0, 10],
            COL_END_TIME: [10, 20],
            COL_TEAM_MOVES: [["double", "double"], ["double"]],
            COL_OPPONENT_MOVES: [[], []],
            COL_TEAM_SCORES: [["T"], []],
            COL_OPPONENT_SCORES: [[], ["E"]],
            COL_NET_POINTS: [10, 4],
            COL_ADJUSTED_NET_POINTS: [10, 4],
        }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name="Origin"))
        result = _GenerateMoveDF(df, COL_TEAM_MOVES)
        # Sequence 1 has 2 occurrences of "double" so its 10 points are split
        # 5/5 across the occurrences; sequence 2 contributes 4 to its one
        # occurrence. Average = (5 + 5 + 4) / 3 = 4.667 (not (10 + 4) / 2).
        assert result.loc["double", "Count (occ.)"] == 3
        assert result.loc["double", "Net Points"] == 14
        assert result.loc["double", "Average Net Points"] == pytest.approx(14 / 3)

    def test_nothing_move_is_dropped(self) -> None:
        df = pd.DataFrame({
            COL_START_TIME: [0],
            COL_END_TIME: [10],
            COL_TEAM_MOVES: [["nothing", "double"]],
            COL_OPPONENT_MOVES: [[]],
            COL_TEAM_SCORES: [["T"]],
            COL_OPPONENT_SCORES: [[]],
            COL_NET_POINTS: [3],
            COL_ADJUSTED_NET_POINTS: [3],
        }, index=pd.Index(["v.mkv:1"], name="Origin"))
        result = _GenerateMoveDF(df, COL_TEAM_MOVES)
        assert "nothing" not in result.index
        assert result.loc["double", "Count (occ.)"] == 1
        assert result.loc["double", "Net Points"] == 3.0


class TestGenerateInitiationDFBySegment:
    """Tests for GenerateInitiationDFBySegment — W/L split rows."""

    def test_segment_split_correctness(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        df[COL_MATCH_RESULT] = ["W", "W", "L", "L", "", ""]
        out = GenerateInitiationDFBySegment(df)
        assert list(out.index) == ["All", "Wins", "Losses", "Unrecorded"]
        assert out.index.name == "Segment"
        # W rows: 0 (attack, +4) and 1 (defend, -3)
        assert out.loc["Wins", "Attack Count"] == 1
        assert out.loc["Wins", "Defense Count"] == 1
        assert out.loc["Wins", "Adjusted Net Points Attacking"] == 4
        assert out.loc["Wins", "Adjusted Net Points Defending"] == -3
        # L rows: 2 (attack, +3) and 3 (defend, -2)
        assert out.loc["Losses", "Attack Count"] == 1
        assert out.loc["Losses", "Defense Count"] == 1
        assert out.loc["Losses", "Adjusted Net Points Attacking"] == 3
        assert out.loc["Losses", "Adjusted Net Points Defending"] == -2
        # All covers all six rows: attacks +4, +3, 0; defends -3, -2, 0.
        assert out.loc["All", "Attack Count"] == 3
        assert out.loc["All", "Defense Count"] == 3
        assert out.loc["All", "Adjusted Net Points Attacking"] == 7
        assert out.loc["All", "Adjusted Net Points Defending"] == -5
        # Sequences column reports the per-segment row counts
        assert out.loc["All", "Sequences"] == 6
        assert out.loc["Wins", "Sequences"] == 2
        assert out.loc["Losses", "Sequences"] == 2
        assert out.loc["Unrecorded", "Sequences"] == 2

    def test_unrecorded_rows_only_in_all(self, sample_df: pd.DataFrame) -> None:
        df = sample_df.copy()
        df[COL_MATCH_RESULT] = ["W", "W", "L", "L", "", ""]
        out = GenerateInitiationDFBySegment(df)
        # Unrecorded rows 4 (attack, +0) and 5 (defend, +0) add no attacking/
        # defending counts or net to the W/L rows beyond the recorded ones.
        assert out.loc["Wins", "Attack Count"] == 1
        assert out.loc["Wins", "Defense Count"] == 1
        assert out.loc["Losses", "Attack Count"] == 1
        assert out.loc["Losses", "Defense Count"] == 1
        # ...while All includes all six rows.
        assert out.loc["All", "Attack Count"] == 3
        assert out.loc["All", "Defense Count"] == 3
        # The two unrecorded rows land in the Unrecorded segment.
        assert out.loc["Unrecorded", "Attack Count"] == 1
        assert out.loc["Unrecorded", "Defense Count"] == 1

    def test_missing_result_column(self, sample_df: pd.DataFrame) -> None:
        out = GenerateInitiationDFBySegment(sample_df)
        # No result column: everything falls into All/Unrecorded.
        assert out.loc["All", "Attack Count"] == 3
        assert out.loc["All", "Defense Count"] == 3
        assert out.loc["Wins", "Attack Count"] == 0
        assert out.loc["Losses", "Attack Count"] == 0
        assert out.loc["Wins", "Defense Count"] == 0
        assert out.loc["Losses", "Defense Count"] == 0
        assert out.loc["Unrecorded", "Sequences"] == 6

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        out = GenerateInitiationDFBySegment(empty_df)
        assert list(out.index) == ["All", "Wins", "Losses", "Unrecorded"]
        assert out.index.name == "Segment"
        assert (out == 0).all().all()


class TestGenerateRatesDF:
    """Tests for GenerateRatesDF — per-match rate metrics."""

    def _rates_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            COL_START_TIME: [0, 6, 12, 18],
            COL_END_TIME: [6, 12, 18, 24],
            COL_ATTACKING: [True, False, True, False],
            COL_TIE_UP: ["collar tie", "standing", "front headlock", "regular ride"],
            COL_TEAM_MOVES: [["double"], ["sprawl"], ["single"], ["whizzer"]],
            COL_OPPONENT_MOVES: [["sprawl"], ["sweep single"], ["whizzer"], ["double"]],
            COL_TEAM_SCORES: [["T"], [], ["PIN"], []],
            COL_OPPONENT_SCORES: [[], ["T"], [], ["E"]],
            COL_NET_POINTS: [3, -3, 6, -1],
            COL_ADJUSTED_NET_POINTS: [3, -3, 19, -1],  # PIN row nets 6 + 13 pin bonus
        }, index=pd.Index(
            ["a.mkv:1", "a.mkv:2", "b.mkv:1", "b.mkv:2"], name=COL_ORIGIN
        ))

    def test_rates_divide_by_match_count(self) -> None:
        out = GenerateRatesDF(self._rates_frame())
        assert out["Matches"].iloc[0] == 2
        assert out["Net Points per Match"].iloc[0] == 2.5  # (3 - 3 + 6 - 1) / 2
        assert out["Adj. Net Points per Match"].iloc[0] == 9.0  # (3 - 3 + 19 - 1) / 2
        assert out["Attacks per Match"].iloc[0] == 1.0  # 2 attacks / 2 matches
        assert out["Defenses per Match"].iloc[0] == 1.0
        assert out["Pins per Match"].iloc[0] == 0.5  # 1 pin / 2 matches
        assert out["Times Pinned per Match"].iloc[0] == 0.0

    def test_single_video_matches_one(self, sample_df: pd.DataFrame) -> None:
        out = GenerateRatesDF(sample_df)
        assert out["Matches"].iloc[0] == 1
        assert out["Net Points per Match"].iloc[0] == 2.0
        assert out["Attacks per Match"].iloc[0] == 3.0

    def test_index_named_metric(self, sample_df: pd.DataFrame) -> None:
        assert GenerateRatesDF(sample_df).index.name == "Metric"

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        out = GenerateRatesDF(empty_df)
        assert out["Matches"].iloc[0] == 0
        assert out["Net Points per Match"].iloc[0] == 0.0
        assert out["Times Pinned per Match"].iloc[0] == 0.0


class TestGenerateTeamSummaryDF:
    """Tests for GenerateTeamSummaryDF — cross-wrestler rates and z-scores."""

    def _frames(self) -> dict[str, pd.DataFrame]:
        alice: pd.DataFrame = pd.DataFrame({
            COL_START_TIME: [0, 6],
            COL_END_TIME: [6, 12],
            COL_ATTACKING: [True, True],
            COL_TIE_UP: ["collar tie", "front headlock"],
            COL_TEAM_MOVES: [["double"], ["single"]],
            COL_OPPONENT_MOVES: [["sprawl"], ["whizzer"]],
            COL_TEAM_SCORES: [["T"], ["N2"]],
            COL_OPPONENT_SCORES: [[], []],
            COL_NET_POINTS: [3, 2],
            COL_ADJUSTED_NET_POINTS: [3, 2],
        }, index=pd.Index(["a.mkv:1", "a.mkv:2"], name=COL_ORIGIN))
        bob: pd.DataFrame = alice.copy()
        bob.index = pd.Index(["b.mkv:1", "b.mkv:2"], name=COL_ORIGIN)
        return {"Alice": alice, "Bob": bob}

    def test_rows_team_mean_and_z_scores(self) -> None:
        out = GenerateTeamSummaryDF(self._frames())
        assert list(out.index) == ["Alice", "Bob", "Team Mean"]
        assert out.index.name == "Wrestler"
        assert out.loc["Alice", "Matches"] == 1
        assert out.loc["Bob", "Matches"] == 1
        assert out.loc["Alice", "Net Points per Match"] == 5.0
        assert out.loc["Bob", "Net Points per Match"] == 5.0
        assert out.loc["Alice", "Attacks per Match"] == 2.0
        assert out.loc["Team Mean", "Net Points per Match"] == 5.0
        # Identical wrestlers -> no variance -> z-scores are all zero.
        assert out.loc["Alice", "Net Points per Match (z)"] == 0.0
        assert out.loc["Bob", "Net Points per Match (z)"] == 0.0
        # The team mean row has no z-scores.
        assert pd.isna(out.loc["Team Mean", "Net Points per Match (z)"])

    def test_z_scores_reflect_differences(self) -> None:
        alice, bob = self._frames().values()
        bob[COL_NET_POINTS] = [1, 1]
        bob[COL_ADJUSTED_NET_POINTS] = [1, 1]
        out = GenerateTeamSummaryDF({"Alice": alice, "Bob": bob})
        assert out.loc["Alice", "Net Points per Match"] == 5.0
        assert out.loc["Bob", "Net Points per Match"] == 2.0
        assert out.loc["Alice", "Net Points per Match (z)"] == pytest.approx(1.0)
        assert out.loc["Bob", "Net Points per Match (z)"] == pytest.approx(-1.0)

    def test_empty_frames(self) -> None:
        out = GenerateTeamSummaryDF({})
        assert list(out.index) == ["Team Mean"]


class TestGenerateMatchOutcomesDF:
    """Tests for GenerateMatchOutcomesDF — match-level W/L/unrecorded counts."""

    _COLUMNS: list[str] = [
        "Matches", "W", "L", "Unrecorded", "Recorded", "Win Rate",
        "Pins", "Times Pinned",
    ]

    def _frame(
        self,
        origins: list[str],
        results: list[str],
        team_scores: list[list[str]] | None = None,
        opponent_scores: list[list[str]] | None = None,
    ) -> pd.DataFrame:
        count: int = len(origins)
        return pd.DataFrame({
            COL_START_TIME: list(range(0, 6 * count, 6)),
            COL_END_TIME: list(range(6, 6 * count + 1, 6)),
            COL_ATTACKING: [True] * count,
            COL_TIE_UP: ["collar tie"] * count,
            COL_TEAM_MOVES: [["double"]] * count,
            COL_OPPONENT_MOVES: [["sprawl"]] * count,
            COL_TEAM_SCORES: team_scores or [["T"]] * count,
            COL_OPPONENT_SCORES: opponent_scores or [[]] * count,
            COL_NET_POINTS: [3] * count,
            COL_ADJUSTED_NET_POINTS: [3] * count,
            COL_MATCH_RESULT: results,
        }, index=pd.Index(origins, name=COL_ORIGIN))

    def test_one_match_with_many_sequences_counts_once(self) -> None:
        frame: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2", "a.mkv:3", "a.mkv:4"], ["W", "W", "W", "W"]
        )
        out = GenerateMatchOutcomesDF({"Alice": frame})
        assert list(out.columns) == self._COLUMNS
        assert out.index.name == "Wrestler"
        assert out.loc["Alice", "Matches"] == 1
        assert out.loc["Alice", "W"] == 1
        assert out.loc["Alice", "L"] == 0
        assert out.loc["Alice", "Recorded"] == 1
        assert out.loc["Alice", "Win Rate"] == 1.0

    def test_two_matches(self) -> None:
        frame: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2", "b.mkv:1", "b.mkv:2"],
            ["W", "W", "L", "L"],
        )
        out = GenerateMatchOutcomesDF({"Alice": frame})
        assert out.loc["Alice", "Matches"] == 2
        assert out.loc["Alice", "W"] == 1
        assert out.loc["Alice", "L"] == 1
        assert out.loc["Alice", "Recorded"] == 2
        assert out.loc["Alice", "Unrecorded"] == 0
        assert out.loc["Alice", "Win Rate"] == 0.5

    def test_contradictory_result_raises(self) -> None:
        frame: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2", "a.mkv:3", "a.mkv:4"],
            ["W", "W", "L", ""],
        )
        with pytest.raises(MatchOutcomeConflictError) as excinfo:
            GenerateMatchOutcomesDF({"Alice": frame})
        conflicts = excinfo.value.conflicts
        assert len(conflicts) == 1
        assert conflicts[0].wrestler == "Alice"
        assert conflicts[0].video == "a.mkv"
        assert conflicts[0].results == ("L", "W")

    def test_blank_beside_result_is_not_a_conflict(self) -> None:
        frame: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2"], ["W", ""]
        )
        assert ValidateMatchOutcomes({"Alice": frame}) == []
        out = GenerateMatchOutcomesDF({"Alice": frame})
        assert out.loc["Alice", "Matches"] == 1
        assert out.loc["Alice", "W"] == 1
        assert out.loc["Alice", "Recorded"] == 1
        assert out.loc["Alice", "Unrecorded"] == 0

    def test_no_results_has_blank_win_rate(self) -> None:
        frame: pd.DataFrame = self._frame(["a.mkv:1", "b.mkv:1"], ["", ""])
        out = GenerateMatchOutcomesDF({"Bob": frame})
        assert out.loc["Bob", "Matches"] == 2
        assert out.loc["Bob", "Recorded"] == 0
        assert out.loc["Bob", "Unrecorded"] == 2
        assert pd.isna(out.loc["Bob", "Win Rate"])

    def test_no_result_column_counts_all_unrecorded(self) -> None:
        legacy: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2"], ["W", "W"]
        ).drop(columns=[COL_MATCH_RESULT])
        out = GenerateMatchOutcomesDF({"Legacy": legacy})
        assert out.loc["Legacy", "Matches"] == 1
        assert out.loc["Legacy", "Unrecorded"] == 1
        assert pd.isna(out.loc["Legacy", "Win Rate"])

    def test_pins_count_per_match(self) -> None:
        frame: pd.DataFrame = self._frame(
            ["a.mkv:1", "a.mkv:2", "b.mkv:1", "b.mkv:2"],
            ["W", "W", "L", "L"],
            team_scores=[["PIN"], ["PIN"], ["T"], ["T"]],
            opponent_scores=[["T"], ["T"], ["PIN"], ["PIN"]],
        )
        out = GenerateMatchOutcomesDF({"Alice": frame})
        assert out.loc["Alice", "Pins"] == 1
        assert out.loc["Alice", "Times Pinned"] == 1

    def test_empty_frames(self) -> None:
        out = GenerateMatchOutcomesDF({})
        assert list(out.columns) == self._COLUMNS
        assert len(out) == 0


class TestAdjustedNetPoints:
    """Tests for AdjustedNetPoints — net points plus the pin bonus."""

    def _row(self, team_scores, opponent_scores) -> pd.Series:
        return pd.Series({
            COL_TEAM_SCORES: team_scores,
            COL_OPPONENT_SCORES: opponent_scores,
        })

    def test_no_pins_equals_net(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.helpers.score_to_pts", {"T": np.int16(3), "E": np.int16(1)})
        monkeypatch.setattr("scripts.helpers._ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr("scripts.helpers.active_pin_points", 13)
        row = self._row(["T"], ["E"])
        assert AdjustedNetPoints(row) == 2

    def test_team_pin_adds_bonus(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.helpers.score_to_pts", {"PIN": np.int16(0)})
        monkeypatch.setattr("scripts.helpers._ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr("scripts.helpers.active_pin_points", 13)
        assert AdjustedNetPoints(self._row(["PIN"], [])) == 13

    def test_opponent_pin_subtracts_bonus(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.helpers.score_to_pts", {"PIN": np.int16(0)})
        monkeypatch.setattr("scripts.helpers._ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr("scripts.helpers.active_pin_points", 9)
        assert AdjustedNetPoints(self._row([], ["PIN"])) == -9

    def test_both_pins_cancel(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.helpers.score_to_pts", {"PIN": np.int16(0)})
        monkeypatch.setattr("scripts.helpers._ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr("scripts.helpers.active_pin_points", 13)
        assert AdjustedNetPoints(self._row(["PIN"], ["PIN"])) == 0

    def test_non_pin_codes_no_bonus(self, monkeypatch) -> None:
        monkeypatch.setattr("scripts.helpers.score_to_pts", {"T": np.int16(3), "None": np.int16(0)})
        monkeypatch.setattr("scripts.helpers._ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr("scripts.helpers.active_pin_points", 13)
        assert AdjustedNetPoints(self._row(["T"], ["None"])) == 3


class TestTabulateAdjustedNetPoints:
    """Tests for TabulateAdjustedNetPoints — in-place column addition."""

    def test_adds_column(self, simple_df: pd.DataFrame) -> None:
        df = simple_df.copy()
        df.drop(columns=[COL_ADJUSTED_NET_POINTS], inplace=True)
        TabulateAdjustedNetPoints(df)
        assert COL_ADJUSTED_NET_POINTS in df.columns
        assert df[COL_ADJUSTED_NET_POINTS].iloc[0] == 3

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        df = empty_df.copy()
        TabulateAdjustedNetPoints(df)
        assert COL_ADJUSTED_NET_POINTS in df.columns
        assert len(df) == 0
