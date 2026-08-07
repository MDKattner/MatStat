"""Tests for the web Search flow — query logic and API routes."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import search
from scripts.web.search import SearchQuery


def _redirect_csv_dir(tmp_path: Path) -> Path:
    """Point the search module's csv_dir at a fresh temp dir."""
    csv_path: Path = tmp_path / "csv"
    csv_path.mkdir(parents=True, exist_ok=True)
    return csv_path


def _write_wrestler_csv(csv_path: Path, name: str, rows: list[str]) -> None:
    """Write a compiled-wrestler CSV (no header, helpers.MakeFormattedDataFrame format)."""
    (csv_path / f"{name}.csv").write_text("\n".join(rows) + "\n")


def _seed_search_data(tmp_path: Path, monkeypatch) -> Path:
    """Create Alice/Bob/UNKNOWN CSVs and redirect search.csv_dir to them."""
    csv_path: Path = _redirect_csv_dir(tmp_path)
    monkeypatch.setattr(search, "csv_dir", csv_path)
    _write_wrestler_csv(csv_path, "Alice", [
        "alice.mkv:1,0,10,A,collar tie,high crotch:double,sprawl,T:N2,E",
        "alice.mkv:2,10,20,D,standing,single,sweep single,None,T",
    ])
    _write_wrestler_csv(csv_path, "Bob", [
        "bob.mkv:1,0,10,A,standing,double,sweep single,N2,None",
        "bob.mkv:2,10,20,D,front headlock,whizzer,sprawl,None,None",
    ])
    _write_wrestler_csv(csv_path, "UNKNOWN", [
        "unknown.mkv:1,0,10,A,standing,single,sprawl,T,None",
    ])
    return csv_path


class TestListWrestlers:
    """Tests for search.list_wrestlers — the dropdown provider."""

    def test_skips_unknown_and_sorts(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        assert search.list_wrestlers() == ["Alice", "Bob"]

    def test_empty_dir(self, tmp_path, monkeypatch) -> None:
        csv_path: Path = _redirect_csv_dir(tmp_path)
        monkeypatch.setattr(search, "csv_dir", csv_path)
        assert search.list_wrestlers() == []


class TestExecuteSearch:
    """Tests for search.execute_search — the query engine."""

    def test_default_query_returns_all_except_unknown(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery())
        assert len(rows) == 4
        assert all(row["wrestler"] in ("Alice", "Bob") for row in rows)

    def test_filters_by_wrestler(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery(wrestler="Alice"))
        assert len(rows) == 2
        assert all(row["wrestler"] == "Alice" for row in rows)

    def test_filters_by_attacking(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(
            SearchQuery(attack_mode="Attacking")
        )
        assert len(rows) == 2
        assert all(row["attacking"] == "A" for row in rows)

    def test_filters_by_defending(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(
            SearchQuery(attack_mode="Defending")
        )
        assert len(rows) == 2
        assert all(row["attacking"] == "D" for row in rows)

    def test_filters_by_tie_up(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery(tie_up="standing"))
        assert len(rows) == 2
        assert all(row["tie_up"] == "standing" for row in rows)

    def test_filters_by_team_move_substring(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery(team_move="high"))
        assert len(rows) == 1
        assert rows[0]["origin"] == "alice.mkv:1"

    def test_filters_by_opp_move_substring(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery(opp_move="sprawl"))
        assert len(rows) == 2
        assert {row["origin"] for row in rows} == {"alice.mkv:1", "bob.mkv:2"}

    def test_filters_by_net_points_range(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(
            SearchQuery(min_points=0, max_points=0)
        )
        assert len(rows) == 1
        assert rows[0]["origin"] == "bob.mkv:2"
        assert rows[0]["net_points"] == 0

    def test_combined_filters(self, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        rows: list[dict[str, Any]] = search.execute_search(SearchQuery(
            wrestler="Bob", attack_mode="Defending", opp_move="sprawl",
            min_points=0, max_points=0,
        ))
        assert len(rows) == 1
        assert rows[0]["origin"] == "bob.mkv:2"


class TestSearchToCsv:
    """Tests for search.search_to_csv — the export serializer."""

    def test_writes_header_and_rows(self) -> None:
        rows: list[dict[str, Any]] = [{
            "wrestler": "Alice",
            "origin": "alice.mkv:1",
            "video": "alice.mkv",
            "start_time": 0,
            "end_time": 10,
            "attacking": "A",
            "tie_up": "collar tie",
            "team_moves": "high crotch, double",
            "opponent_moves": "sprawl",
            "net_points": 4,
        }]
        csv_text: str = search.search_to_csv(rows)
        lines: list[str] = csv_text.splitlines()
        assert lines[0] == "wrestler,origin,video,start_time,end_time,attacking,tie_up,team_moves,opponent_moves,net_points"
        assert "alice.mkv:1" in lines[1]
        assert "high crotch, double" in lines[1]

    def test_empty_rows_keeps_header(self) -> None:
        assert search.search_to_csv([]) == (
            "wrestler,origin,video,start_time,end_time,attacking,"
            "tie_up,team_moves,opponent_moves,net_points\r\n"
        )


class TestSearchRoutes:
    """Tests for the search API routes."""

    def test_wrestlers_route(self, client, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        resp = client.get("/api/search/wrestlers")
        assert resp.status_code == 200
        assert resp.json()["items"] == ["Alice", "Bob"]

    def test_search_route(self, client, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        resp = client.post("/api/search", json={"wrestler": "Alice"})
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["count"] == 2
        assert all(match["wrestler"] == "Alice" for match in body["matches"])

    def test_search_route_empty_result(self, client, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        resp = client.post("/api/search", json={"team_move": "kimura"})
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "matches": []}

    def test_export_route(self, client, tmp_path, monkeypatch) -> None:
        _seed_search_data(tmp_path, monkeypatch)
        resp = client.post("/api/search/export", json={"wrestler": "Bob"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        content: str = resp.content.decode()
        assert content.splitlines()[0].startswith("wrestler,")
        assert "bob.mkv:1" in content
