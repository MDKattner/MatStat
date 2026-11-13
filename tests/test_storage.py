"""Tests for scripts.storage — the SQLite compiled-data layer."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts import storage
from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_ATTACKING,
    COL_MATCH_DATE,
    COL_MATCH_RESULT,
    COL_NET_POINTS,
    COL_OPPONENT_MOVES,
    COL_OPPONENT_SCORES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TEAM_SCORES,
    COL_TIE_UP,
)


def _row(
    origin: str = "v.mkv:1",
    *,
    attacking: bool = True,
    tie_up: str = "collar tie",
    net: int = 3,
    adjusted: int = 3,
    result: str = "W",
    date: str = "2026-08-01",
) -> storage.SequenceRow:
    return storage.SequenceRow(
        origin=origin,
        start_time=0,
        end_time=6,
        attacking=attacking,
        tie_up=tie_up,
        team_moves=["double"],
        opponent_moves=["sprawl"],
        team_scores=["T"],
        opponent_scores=["E"],
        net_points=net,
        adjusted_net_points=adjusted,
        match_result=result,
        match_date=date,
    )


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "ruleset": "Folkstyle",
        "ruleset_hash": "abc",
        "app_version": "test",
        "taged_dir": "/tmp/taged",
    }


def test_init_db_creates_schema_and_health_passes(isolated_db: Path) -> None:
    assert isolated_db.is_file()
    storage.health()
    with storage.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == storage.DB_SCHEMA_VERSION


def test_health_rejects_schema_mismatch(isolated_db: Path) -> None:
    with storage.connect() as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="schema version"):
        storage.health()


def test_health_rejects_missing_database(isolated_db: Path) -> None:
    isolated_db.unlink()
    with pytest.raises(RuntimeError, match="not found"):
        storage.health()


def test_apply_compile_round_trips_rows_and_order() -> None:
    rows = [
        _row("v.mkv:2", attacking=False, tie_up="underhook:collar tie", net=-3),
        _row("v.mkv:1"),
        _row("v.mkv:1", result="L", date=""),
    ]

    errors = storage.apply_compile({"Alice": rows}, skip=set(), manifest=_manifest())
    assert errors == []

    frame = storage.load_wrestler_frame("Alice")
    assert list(frame.index) == ["v.mkv:2", "v.mkv:1", "v.mkv:1"]
    assert frame[COL_ADJUSTED_NET_POINTS].tolist() == [3, 3, 3]
    assert frame[COL_NET_POINTS].tolist() == [-3, 3, 3]
    assert frame[COL_ATTACKING].tolist() == [False, True, True]
    assert frame[COL_TIE_UP].iloc[0] == "underhook:collar tie"
    assert frame[COL_TEAM_MOVES].iloc[0] == ["double"]
    assert frame[COL_OPPONENT_MOVES].iloc[0] == ["sprawl"]
    assert frame[COL_TEAM_SCORES].iloc[0] == ["T"]
    assert frame[COL_OPPONENT_SCORES].iloc[0] == ["E"]
    assert frame[COL_MATCH_RESULT].tolist() == ["W", "W", "L"]
    assert frame[COL_MATCH_DATE].tolist() == ["2026-08-01", "2026-08-01", ""]
    assert frame[COL_START_TIME].iloc[0] == 0


def test_apply_compile_replaces_previous_rows() -> None:
    storage.apply_compile(
        {"Alice": [_row("old.mkv:1"), _row("old.mkv:2")]},
        skip=set(),
        manifest=_manifest(),
    )
    storage.apply_compile(
        {"Alice": [_row("new.mkv:1")]}, skip=set(), manifest=_manifest()
    )

    frame = storage.load_wrestler_frame("Alice")
    assert list(frame.index) == ["new.mkv:1"]


def test_apply_compile_zero_rows_deletes_wrestler() -> None:
    storage.apply_compile({"Alice": [_row()]}, skip=set(), manifest=_manifest())

    storage.apply_compile({"Alice": []}, skip=set(), manifest=_manifest())

    assert not storage.wrestler_exists("Alice")
    assert storage.list_wrestlers() == []


def test_apply_compile_skip_preserves_previous_rows() -> None:
    storage.apply_compile({"Alice": [_row("old.mkv:1")]}, skip=set(), manifest=_manifest())

    storage.apply_compile(
        {"Alice": [_row("new.mkv:1")]}, skip={"Alice"}, manifest=_manifest()
    )

    assert list(storage.load_wrestler_frame("Alice").index) == ["old.mkv:1"]


def test_failed_wrestler_keeps_previous_rows_and_others_commit(monkeypatch) -> None:
    storage.apply_compile(
        {"Alice": [_row("old-a.mkv:1")], "Broken": [_row("old-b.mkv:1")]},
        skip=set(),
        manifest=_manifest(),
    )
    real_insert = storage._insert_rows

    def failing_insert(connection, wrestler_id, rows):
        if any(row.origin.startswith("new-b") for row in rows):
            raise sqlite3.OperationalError("disk I/O error")
        return real_insert(connection, wrestler_id, rows)

    monkeypatch.setattr(storage, "_insert_rows", failing_insert)

    errors = storage.apply_compile(
        {
            "Alice": [_row("new-a.mkv:1")],
            "Broken": [_row("new-b.mkv:1")],
        },
        skip=set(),
        manifest=_manifest(),
    )

    assert len(errors) == 1
    assert "Broken" in errors[0]
    assert list(storage.load_wrestler_frame("Alice").index) == ["new-a.mkv:1"]
    assert list(storage.load_wrestler_frame("Broken").index) == ["old-b.mkv:1"]


def test_list_wrestlers_excludes_unknown_and_sorts() -> None:
    storage.apply_compile(
        {
            "Bob": [_row()],
            "Alice": [_row()],
            "UNKNOWN": [_row()],
        },
        skip=set(),
        manifest=_manifest(),
    )

    assert storage.list_wrestlers() == ["Alice", "Bob"]
    assert storage.has_data() is True
    assert storage.wrestler_exists("Alice") is True
    assert storage.wrestler_exists("UNKNOWN") is True
    assert storage.wrestler_exists("Missing") is False


def test_has_data_false_when_only_unknown() -> None:
    storage.apply_compile({"UNKNOWN": [_row()]}, skip=set(), manifest=_manifest())

    assert storage.has_data() is False
    assert storage.list_wrestlers() == []


def test_load_all_frames_returns_every_compiled_wrestler() -> None:
    storage.apply_compile(
        {"Bob": [_row("b.mkv:1")], "Alice": [_row("a.mkv:1")]},
        skip=set(),
        manifest=_manifest(),
    )

    frames = storage.load_all_frames()

    assert list(frames) == ["Alice", "Bob"]
    assert list(frames["Alice"].index) == ["a.mkv:1"]


def test_load_wrestler_frame_missing_raises() -> None:
    with pytest.raises(FileNotFoundError, match="Compile Stats"):
        storage.load_wrestler_frame("Ghost")


def test_manifest_is_recorded_in_compile_runs() -> None:
    manifest = _manifest()
    storage.apply_compile({"Alice": [_row()]}, skip=set(), manifest=manifest)

    with storage.connect() as connection:
        row = connection.execute(
            "SELECT manifest_json FROM compile_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row is not None
    assert json.loads(row["manifest_json"])["ruleset"] == "Folkstyle"


def test_compile_runs_are_capped() -> None:
    for _ in range(storage.MAX_COMPILE_RUNS + 5):
        storage.apply_compile({"Alice": [_row()]}, skip=set(), manifest=_manifest())

    with storage.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM compile_runs").fetchone()[0]

    assert count == storage.MAX_COMPILE_RUNS


def test_swap_perspective_swaps_everything() -> None:
    row = _row(
        "v.mkv:1",
        attacking=True,
        tie_up="collar tie:underhook",
        net=3,
        adjusted=16,
        result="W",
        date="2026-08-01",
    )

    swapped = storage.swap_perspective(row)

    assert swapped.origin == "v.mkv:1"
    assert swapped.start_time == 0
    assert swapped.end_time == 6
    assert swapped.attacking is False
    assert swapped.tie_up == "underhook:collar tie"
    assert swapped.team_moves == ["sprawl"]
    assert swapped.opponent_moves == ["double"]
    assert swapped.team_scores == ["E"]
    assert swapped.opponent_scores == ["T"]
    assert swapped.net_points == -3
    assert swapped.adjusted_net_points == -16
    assert swapped.match_result == "L"
    assert swapped.match_date == "2026-08-01"


def test_swap_perspective_handles_blank_result_and_single_tie() -> None:
    swapped = storage.swap_perspective(
        _row(tie_up="collar tie", result="", date="")
    )

    assert swapped.tie_up == "collar tie"
    assert swapped.match_result == ""
    assert swapped.match_date == ""
