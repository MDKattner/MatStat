"""Tests for the shared QuerySpec — one filter implementation for every path."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import clips, search
from scripts.web.query import QuerySpec, selected_wrestlers

_CSV_ROWS: list[str] = [
    "a.mkv:1,0,10,A,collar tie,double:high crotch,sprawl,T,None",
    "a.mkv:2,10,20,D,underhook:front headlock,single,whizzer,None,T",
    "b.mkv:1,0,10,A,collar tie,single,double,T,None",
]


def _seed(tmp_path: Path, monkeypatch) -> Path:
    """Write one wrestler CSV and point search/clips at it."""
    csv_path: Path = tmp_path / "csv"
    csv_path.mkdir(parents=True, exist_ok=True)
    (csv_path / "Alice.csv").write_text("\n".join(_CSV_ROWS) + "\n")
    monkeypatch.setattr(search, "csv_dir", csv_path)
    monkeypatch.setattr(clips, "csv_dir", csv_path)
    return csv_path


class TestQuerySpec:
    """Tests for serialization and roster selection semantics."""

    def test_round_trips_through_json_unchanged(self) -> None:
        spec: QuerySpec = QuerySpec(
            wrestlers=["Alice", "Bob"],
            origins=["a.mkv:1"],
            attack_mode="Attacking",
            tie_up="collar tie",
            opp_tie="underhook",
            team_move="double",
            opp_move="sprawl",
            move_match="exact",
            match_result="W",
            min_date="2025-01-01",
            max_date="2025-12-31",
            min_points=-5,
            max_points=10,
            min_raw_points=-10,
            max_raw_points=5,
            min_duration=5,
            max_duration=60,
            sort_by="Adjusted Net Points",
            ascending=False,
            limit=25,
            offset=3,
        )
        restored: QuerySpec = QuerySpec.model_validate_json(spec.model_dump_json())
        assert restored == spec

    def test_unknown_legacy_fields_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="filter_type"):
            QuerySpec(**{"wrestler": "Alice", "filter_type": "Starting Tie"})

    def test_all_means_every_wrestler(self) -> None:
        assert selected_wrestlers(QuerySpec(wrestlers=["All"])) == []
        assert selected_wrestlers(QuerySpec(wrestlers=[" Alice ", ""])) == ["Alice"]


class TestApplyQuery:
    """Tests for apply_query — the only filter implementation."""

    def test_search_and_find_return_identical_rows(
        self, tmp_path, monkeypatch
    ) -> None:
        _seed(tmp_path, monkeypatch)
        spec: QuerySpec = QuerySpec(
            wrestlers=["Alice"],
            attack_mode="Attacking",
            team_move="double",
        )

        search_rows: list[dict[str, Any]] = search.execute_search(spec)
        find_frame = clips.find_with_query(spec, "Alice")

        assert {row["origin"] for row in search_rows} == set(find_frame.index)
        assert [row["origin"] for row in search_rows] == list(find_frame.index)

    def test_substring_is_default_and_exact_is_opt_in(
        self, tmp_path, monkeypatch
    ) -> None:
        _seed(tmp_path, monkeypatch)

        substring: list[dict[str, Any]] = search.execute_search(
            QuerySpec(wrestlers=["Alice"], team_move="dou")
        )
        exact: list[dict[str, Any]] = search.execute_search(
            QuerySpec(wrestlers=["Alice"], team_move="dou", move_match="exact")
        )

        assert len(substring) == 1
        assert substring[0]["origin"] == "a.mkv:1"
        assert exact == []

    def test_opponent_tie_filter(self, tmp_path, monkeypatch) -> None:
        _seed(tmp_path, monkeypatch)

        rows: list[dict[str, Any]] = search.execute_search(
            QuerySpec(wrestlers=["Alice"], opp_tie="front headlock")
        )

        assert [row["origin"] for row in rows] == ["a.mkv:2"]

    def test_origins_are_exact_and_ordered(self, tmp_path, monkeypatch) -> None:
        _seed(tmp_path, monkeypatch)
        spec: QuerySpec = QuerySpec(
            wrestlers=["Alice"],
            origins=["b.mkv:1", "a.mkv:1"],
            tie_up="collar tie",
        )

        frame = clips.find_with_query(spec, "Alice")

        assert list(frame.index) == ["b.mkv:1", "a.mkv:1"]

    def test_unknown_origin_raises(self, tmp_path, monkeypatch) -> None:
        _seed(tmp_path, monkeypatch)

        with pytest.raises(ValueError, match="Unknown origin"):
            clips.find_with_query(
                QuerySpec(wrestlers=["Alice"], origins=["nope.mkv:1"]), "Alice"
            )

    def test_empty_spec_keeps_all_rows(self, tmp_path, monkeypatch) -> None:
        _seed(tmp_path, monkeypatch)

        rows: list[dict[str, Any]] = search.execute_search(
            QuerySpec(wrestlers=["Alice"])
        )

        assert len(rows) == 3


class TestSearchRouteLegacy:
    """The retired request shapes fail validation at the route boundary."""

    def test_search_rejects_single_wrestler_field(self, client) -> None:
        resp = client.post("/api/search", json={"wrestler": "Alice"})
        assert resp.status_code == 422
        assert "wrestler" in resp.text

    def test_export_rejects_legacy_filter_fields(self, client) -> None:
        resp = client.post("/api/search/export", json={
            "wrestler": "Alice",
            "filter_type": "Starting Tie",
            "filter_item": "collar tie",
        })
        assert resp.status_code == 422
