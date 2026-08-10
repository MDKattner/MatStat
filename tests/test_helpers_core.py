"""Tests for helper functions: data transforms and statistics."""

import sys
from pathlib import Path

import pytest
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import (
    COL_TEAM_MOVES, COL_OPPONENT_MOVES, COL_TEAM_SCORES, COL_OPPONENT_SCORES,
    COL_START_TIME, COL_END_TIME, COL_ATTACKING, COL_NET_POINTS,
    COL_ADJUSTED_NET_POINTS, COL_MATCH_RESULT,
    CalculateNetPoints, TabulateNetPoints, AdjustedNetPoints,
    TabulateAdjustedNetPoints,
    MoveCounts, MoveUsageCounts, MoveDefenseCounts, PinCount,
    _MoveFilter, DidMove, DefendedMove,
    _GenerateMoveDF, GenerateOffenseDF, GenerateDefenseDF, GenerateInitiationDF,
    GenerateInitiationDFBySegment,
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


class TestMoveCounts:
    """Tests for MoveCounts and convenience wrappers."""

    def test_counts_team_moves(self, sample_df: pd.DataFrame) -> None:
        counts: dict[str, int] = MoveCounts(sample_df, COL_TEAM_MOVES)
        assert counts.get("double") == 2
        assert counts.get("sprawl") == 2
        assert counts.get("high crotch") == 1
        assert "nonexistent" not in counts

    def test_counts_opponent_moves(self, sample_df: pd.DataFrame) -> None:
        counts: dict[str, int] = MoveCounts(sample_df, COL_OPPONENT_MOVES)
        assert counts.get("sweep single") == 2
        assert counts.get("sprawl") == 2
        assert "nonexistent" not in counts

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        counts: dict[str, int] = MoveCounts(empty_df, COL_TEAM_MOVES)
        assert counts == {}

    def test_move_usage_counts(self, sample_df: pd.DataFrame) -> None:
        counts: dict[str, int] = MoveUsageCounts(sample_df)
        assert counts.get("double") == 2

    def test_move_defense_counts(self, sample_df: pd.DataFrame) -> None:
        counts: dict[str, int] = MoveDefenseCounts(sample_df)
        assert counts.get("sprawl") == 2


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

    def test_did_move(self, simple_df: pd.DataFrame) -> None:
        filt = DidMove("double")
        result = simple_df.apply(filt, axis=1, result_type="broadcast")
        result = result.dropna(how="all")
        assert len(result) == 1

    def test_defended_move(self, simple_df: pd.DataFrame) -> None:
        filt = DefendedMove("sweep single")
        result = simple_df.apply(filt, axis=1, result_type="broadcast")
        result = result.dropna(how="all")
        assert len(result) == 1

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


class TestGenerateInitiationDF:
    """Tests for GenerateInitiationDF — attack/defense summary."""

    def test_mixed_attack_defend(self, sample_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(sample_df)
        assert df["Attack Count"].iloc[0] == 3
        assert df["Defense Count"].iloc[0] == 3
        assert df["Attacks / Sequences"].iloc[0] == 0.5

    def test_all_attack(self, all_attack_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(all_attack_df)
        assert df["Attack Count"].iloc[0] == 2
        assert df["Defense Count"].iloc[0] == 0
        assert df["Attacks / Sequences"].iloc[0] == 1.0

    def test_all_defend(self, all_defend_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(all_defend_df)
        assert df["Attack Count"].iloc[0] == 0
        assert df["Defense Count"].iloc[0] == 2
        assert df["Attacks / Sequences"].iloc[0] == 0.0

    def test_net_points_computed(self, sample_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(sample_df)
        assert df["Adjusted Net Points Attacking"].iloc[0] == 7  # 4 + 3 + 0
        assert df["Adjusted Net Points Defending"].iloc[0] == -5  # -3 + -2 + 0
        # sample_df has no pins, so raw net equals adjusted net.
        assert df["Net Points Attacking"].iloc[0] == 7
        assert df["Net Points Defending"].iloc[0] == -5
        assert df["Average Net Points Attacking"].iloc[0] == pytest.approx(7 / 3)
        assert df["Average Net Points Defending"].iloc[0] == pytest.approx(-5 / 3)
        assert df["Sequences"].iloc[0] == 6

    def test_index_named(self, sample_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(sample_df)
        assert df.index.name == "Metric"

    def test_empty_dataframe(self, empty_df: pd.DataFrame) -> None:
        df = GenerateInitiationDF(empty_df)
        assert df["Attack Count"].iloc[0] == 0
        assert df["Defense Count"].iloc[0] == 0
        assert df["Attacks / Sequences"].iloc[0] == 0.0


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
        assert out.loc["Wins", "Adjusted Net Points Attacking"] == 4
        # L rows: 2 (attack, +3) and 3 (defend, -2)
        assert out.loc["Losses", "Attack Count"] == 1
        assert out.loc["Losses", "Adjusted Net Points Attacking"] == 3
        # All matches the full-data single-row table
        full = GenerateInitiationDF(df)
        assert out.loc["All", "Attack Count"] == full["Attack Count"].iloc[0]
        assert out.loc["All", "Defense Count"] == full["Defense Count"].iloc[0]
        assert out.loc["All", "Adjusted Net Points Attacking"] == full[
            "Adjusted Net Points Attacking"
        ].iloc[0]
        assert out.loc["All", "Adjusted Net Points Defending"] == full[
            "Adjusted Net Points Defending"
        ].iloc[0]
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
        full = GenerateInitiationDF(sample_df)
        assert out.loc["All", "Attack Count"] == full["Attack Count"].iloc[0]
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
