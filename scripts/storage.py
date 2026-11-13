"""SQLite storage for compiled wrestler sequences.

This module replaces the per-wrestler CSV files under ``stats/wrestler_data``
with a single SQLite database. It owns the schema, the typed row model, the
perspective swap, and the read/write helpers every consumer uses, so no module
outside this file needs to know the SQL.

Design constraints that keep the migration behaviour-compatible:

- Rows are stored in source order via ``seq_order``; duplicate ``origin``
  values are allowed because a wrestler can tag the same chapter twice.
- The four move/score columns are JSON arrays, preserving list order and
  empty-list semantics.
- Stored net/adjusted points are trusted on read, exactly like the 13-column
  CSVs. Scoring helpers are only used while compiling.
- A compile is one transaction with a per-wrestler savepoint, so a failed
  wrestler keeps its previous rows while the rest commit.
- Readers use a deferred transaction for multi-statement loads so a concurrent
  compile cannot commit halfway through a PCA/team-eval read.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_ATTACKING,
    COL_END_TIME,
    COL_MATCH_DATE,
    COL_MATCH_RESULT,
    COL_NET_POINTS,
    COL_OPPONENT_MOVES,
    COL_OPPONENT_SCORES,
    COL_ORIGIN,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TEAM_SCORES,
    COL_TIE_UP,
    AdjustedNetPoints,
    CalculateNetPoints,
    ReloadScoringMap,
    csv_dir,
)

DB_FILENAME: str = "matstat.db"
DB_SCHEMA_VERSION: int = 1
MAX_COMPILE_RUNS: int = 50
_BUSY_TIMEOUT_MS: int = 5000

# Resolved at import from the same data directory as the legacy CSVs. Tests and
# alternate launchers replace this module-level binding; every helper reads it
# at call time so a single monkeypatch is enough.
db_path: Path = csv_dir / DB_FILENAME

# Compiles run on a 4-worker pool and a user can queue more than one. SQLite's
# BEGIN IMMEDIATE plus busy_timeout handles cross-process contention; this lock
# keeps in-process writers from bouncing off each other before the timeout.
_WRITE_LOCK: threading.RLock = threading.RLock()

_FRAME_COLUMNS: list[str] = [
    COL_START_TIME,
    COL_END_TIME,
    COL_ATTACKING,
    COL_TIE_UP,
    COL_TEAM_MOVES,
    COL_OPPONENT_MOVES,
    COL_TEAM_SCORES,
    COL_OPPONENT_SCORES,
    COL_NET_POINTS,
    COL_ADJUSTED_NET_POINTS,
    COL_MATCH_RESULT,
    COL_MATCH_DATE,
]

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS wrestlers (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
    id                   INTEGER PRIMARY KEY,
    wrestler_id          INTEGER NOT NULL REFERENCES wrestlers(id) ON DELETE CASCADE,
    seq_order            INTEGER NOT NULL,
    origin               TEXT NOT NULL,
    start_time           INTEGER NOT NULL,
    end_time             INTEGER NOT NULL,
    attacking            INTEGER NOT NULL,
    tie_up               TEXT NOT NULL DEFAULT '',
    team_moves           TEXT NOT NULL DEFAULT '[]',
    opponent_moves       TEXT NOT NULL DEFAULT '[]',
    team_scores          TEXT NOT NULL DEFAULT '[]',
    opponent_scores      TEXT NOT NULL DEFAULT '[]',
    net_points           INTEGER NOT NULL,
    adjusted_net_points  INTEGER NOT NULL,
    match_result         TEXT NOT NULL DEFAULT '',
    match_date           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sequences_wrestler_order
    ON sequences(wrestler_id, seq_order);

CREATE TABLE IF NOT EXISTS compile_runs (
    id                      INTEGER PRIMARY KEY,
    manifest_schema_version INTEGER NOT NULL,
    generated_at            TEXT NOT NULL,
    ruleset                 TEXT NOT NULL,
    ruleset_hash            TEXT NOT NULL,
    app_version             TEXT NOT NULL,
    taged_dir               TEXT NOT NULL,
    manifest_json           TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class SequenceRow:
    """One compiled sequence, typed for direct storage insertion."""

    origin: str
    start_time: int
    end_time: int
    attacking: bool
    tie_up: str
    team_moves: list[str]
    opponent_moves: list[str]
    team_scores: list[str]
    opponent_scores: list[str]
    net_points: int
    adjusted_net_points: int
    match_result: str
    match_date: str


def _split_list(value: str) -> list[str]:
    """Split a colon-delimited chapter field into a list (empty -> [])."""
    return value.split(":") if value else []


def parse_compiled_rows(
    csv_data: str,
    result: str = "",
    match_date: str = "",
    warnings: list[str] | None = None,
) -> list[SequenceRow]:
    """Convert ``MakeNameAndCSV`` rows into typed, scored sequence rows.

    Rows must carry the 9-field compiled schema (origin, start, end, attacking,
    tie, team moves, opponent moves, team scores, opponent scores). The scoring
    map is refreshed from the active ruleset first so appended net/adjusted
    points match a recompile. Malformed rows are skipped with a warning rather
    than corrupting the database.

    Args:
        csv_data: Multi-line CSV data from ``MakeNameAndCSV``.
        result: The tagged wrestler's result for this video ("", "W", or "L").
        match_date: The match date for this video ("YYYY-MM-DD" or "").
        warnings: Optional list that receives one message per skipped row.

    Returns:
        The parsed and scored rows, in source order.
    """
    ReloadScoringMap()
    rows: list[SequenceRow] = []
    for line in csv_data.splitlines():
        stripped: str = line.strip()
        if not stripped:
            continue
        parts: list[str] = stripped.split(",")
        if len(parts) != 9:
            origin_hint: str = parts[0] if parts else stripped
            message: str = (
                f"Skipped malformed chapter row for '{origin_hint}': "
                f"expected 9 fields, got {len(parts)}"
            )
            logging.warning(message)
            if warnings is not None:
                warnings.append(message)
            continue
        (
            origin,
            start_raw,
            end_raw,
            attack,
            tie_up,
            team_moves_raw,
            opponent_moves_raw,
            team_scores_raw,
            opponent_scores_raw,
        ) = parts
        try:
            start_time: int = int(float(start_raw))
            end_time: int = int(float(end_raw))
        except ValueError:
            message = (
                f"Skipped malformed chapter row for '{origin}': "
                f"invalid times ({start_raw!r}-{end_raw!r})"
            )
            logging.warning(message)
            if warnings is not None:
                warnings.append(message)
            continue

        team_moves: list[str] = _split_list(team_moves_raw)
        opponent_moves: list[str] = _split_list(opponent_moves_raw)
        team_scores: list[str] = _split_list(team_scores_raw)
        opponent_scores: list[str] = _split_list(opponent_scores_raw)
        scores: pd.Series = pd.Series({
            COL_TEAM_SCORES: team_scores,
            COL_OPPONENT_SCORES: opponent_scores,
        })
        rows.append(SequenceRow(
            origin=origin,
            start_time=start_time,
            end_time=end_time,
            attacking=attack == "A",
            tie_up=tie_up,
            team_moves=team_moves,
            opponent_moves=opponent_moves,
            team_scores=team_scores,
            opponent_scores=opponent_scores,
            net_points=int(CalculateNetPoints(scores)),
            adjusted_net_points=int(AdjustedNetPoints(scores)),
            match_result=result,
            match_date=match_date,
        ))
    return rows


def swap_perspective(row: SequenceRow) -> SequenceRow:
    """Re-express one sequence from the opponent's perspective.

    Mirrors ``SwapPerspectiveCSV``: flips attacking, swaps move/score lists,
    reverses a colon-paired tie-up, negates net/adjusted points, and inverts
    W/L. Origin, times, and match date are preserved.
    """
    tie_pair: list[str] = row.tie_up.split(":")
    new_tie: str = ":".join(reversed(tie_pair)) if len(tie_pair) > 1 else row.tie_up
    new_result: str = (
        "L" if row.match_result == "W"
        else ("W" if row.match_result == "L" else row.match_result)
    )
    return SequenceRow(
        origin=row.origin,
        start_time=row.start_time,
        end_time=row.end_time,
        attacking=not row.attacking,
        tie_up=new_tie,
        team_moves=list(row.opponent_moves),
        opponent_moves=list(row.team_moves),
        team_scores=list(row.opponent_scores),
        opponent_scores=list(row.team_scores),
        net_points=-row.net_points,
        adjusted_net_points=-row.adjusted_net_points,
        match_result=new_result,
        match_date=row.match_date,
    )


def connect() -> sqlite3.Connection:
    """Open a connection configured for WAL, foreign keys, and short waits."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection = sqlite3.connect(
        str(db_path),
        timeout=_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Yield a connection and always close it."""
    connection: sqlite3.Connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def init_db() -> None:
    """Create the schema when missing and validate the schema version."""
    with _WRITE_LOCK:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connection() as connection:
            connection.executescript(_SCHEMA)
            version: int = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version == 0:
                connection.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
            elif version != DB_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {version} is not supported "
                    f"(expected {DB_SCHEMA_VERSION}); back up and remove "
                    f"{db_path} to rebuild it with Tabulate."
                )


def health() -> None:
    """Raise when the database is missing, unreadable, or schema-mismatched."""
    if not db_path.is_file():
        raise RuntimeError(f"Compiled-data database not found: {db_path}")
    with _connection() as connection:
        version: int = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != DB_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} does not match "
                f"{DB_SCHEMA_VERSION}"
            )
        connection.execute("SELECT 1").fetchone()


def _empty_frame() -> pd.DataFrame:
    """Return an empty DataFrame with the compiled frame contract and dtypes."""
    frame: pd.DataFrame = pd.DataFrame({
        COL_START_TIME: pd.Series([], dtype="int16"),
        COL_END_TIME: pd.Series([], dtype="int16"),
        COL_ATTACKING: pd.Series([], dtype=bool),
        COL_TIE_UP: pd.Series([], dtype=str),
        COL_TEAM_MOVES: pd.Series([], dtype=object),
        COL_OPPONENT_MOVES: pd.Series([], dtype=object),
        COL_TEAM_SCORES: pd.Series([], dtype=object),
        COL_OPPONENT_SCORES: pd.Series([], dtype=object),
        COL_NET_POINTS: pd.Series([], dtype="int16"),
        COL_ADJUSTED_NET_POINTS: pd.Series([], dtype="int16"),
        COL_MATCH_RESULT: pd.Series([], dtype=str),
        COL_MATCH_DATE: pd.Series([], dtype=str),
    }, index=pd.Index([], name=COL_ORIGIN))
    frame.attrs["points_source"] = "stored"
    return frame


def _frame_from_rows(rows: Iterable[sqlite3.Row]) -> pd.DataFrame:
    """Build the compiled DataFrame contract from stored sequence rows."""
    records: list[dict[str, Any]] = []
    for row in rows:
        records.append({
            COL_ORIGIN: row["origin"],
            COL_START_TIME: int(row["start_time"]),
            COL_END_TIME: int(row["end_time"]),
            COL_ATTACKING: bool(row["attacking"]),
            COL_TIE_UP: row["tie_up"],
            COL_TEAM_MOVES: json.loads(row["team_moves"]),
            COL_OPPONENT_MOVES: json.loads(row["opponent_moves"]),
            COL_TEAM_SCORES: json.loads(row["team_scores"]),
            COL_OPPONENT_SCORES: json.loads(row["opponent_scores"]),
            COL_NET_POINTS: int(row["net_points"]),
            COL_ADJUSTED_NET_POINTS: int(row["adjusted_net_points"]),
            COL_MATCH_RESULT: row["match_result"],
            COL_MATCH_DATE: row["match_date"],
        })
    if not records:
        return _empty_frame()
    frame: pd.DataFrame = pd.DataFrame(records)
    frame = frame.set_index(COL_ORIGIN)
    frame.index.name = COL_ORIGIN
    frame = frame[_FRAME_COLUMNS]
    frame = frame.astype({
        COL_START_TIME: "int16",
        COL_END_TIME: "int16",
        COL_ATTACKING: bool,
        COL_NET_POINTS: "int16",
        COL_ADJUSTED_NET_POINTS: "int16",
        COL_TIE_UP: str,
        COL_MATCH_RESULT: str,
        COL_MATCH_DATE: str,
    })
    frame.attrs["points_source"] = "stored"
    return frame


def list_wrestlers() -> list[str]:
    """Return compiled wrestler names (excluding UNKNOWN), sorted alphabetically."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT name FROM wrestlers WHERE name != ? ORDER BY name",
            ("UNKNOWN",),
        ).fetchall()
    return [str(row["name"]) for row in rows]


def wrestler_exists(name: str) -> bool:
    """Return whether the wrestler has a compiled row in the database."""
    with _connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM wrestlers WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    return row is not None


def has_data() -> bool:
    """Return whether any compiled wrestler (other than UNKNOWN) is stored."""
    with _connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM wrestlers WHERE name != ? LIMIT 1", ("UNKNOWN",)
        ).fetchone()
    return row is not None


def load_wrestler_frame(name: str) -> pd.DataFrame:
    """Load one wrestler's compiled sequences as a formatted DataFrame.

    Args:
        name: The wrestler name.

    Returns:
        The compiled DataFrame contract (index = Origin, stored points).

    Raises:
        FileNotFoundError: If no compiled data exists for the wrestler.
    """
    ReloadScoringMap()
    with _connection() as connection:
        connection.execute("BEGIN")
        try:
            wrestler = connection.execute(
                "SELECT id FROM wrestlers WHERE name = ?", (name,)
            ).fetchone()
            if wrestler is None:
                raise FileNotFoundError(
                    f"No compiled data for {name}. Run Compile Stats first."
                )
            rows = connection.execute(
                """
                SELECT origin, start_time, end_time, attacking, tie_up,
                       team_moves, opponent_moves, team_scores, opponent_scores,
                       net_points, adjusted_net_points, match_result, match_date
                FROM sequences
                WHERE wrestler_id = ?
                ORDER BY seq_order
                """,
                (int(wrestler["id"]),),
            ).fetchall()
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
    return _frame_from_rows(rows)


def load_all_frames() -> dict[str, pd.DataFrame]:
    """Load every compiled wrestler, sorted by name, in one read snapshot."""
    ReloadScoringMap()
    frames: dict[str, pd.DataFrame] = {}
    with _connection() as connection:
        connection.execute("BEGIN")
        try:
            names = [
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM wrestlers WHERE name != ? ORDER BY name",
                    ("UNKNOWN",),
                ).fetchall()
            ]
            for name in names:
                wrestler = connection.execute(
                    "SELECT id FROM wrestlers WHERE name = ?", (name,)
                ).fetchone()
                if wrestler is None:
                    continue
                rows = connection.execute(
                    """
                    SELECT origin, start_time, end_time, attacking, tie_up,
                           team_moves, opponent_moves, team_scores,
                           opponent_scores, net_points, adjusted_net_points,
                           match_result, match_date
                    FROM sequences
                    WHERE wrestler_id = ?
                    ORDER BY seq_order
                    """,
                    (int(wrestler["id"]),),
                ).fetchall()
                frames[name] = _frame_from_rows(rows)
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
    return frames


def _insert_rows(
    connection: sqlite3.Connection,
    wrestler_id: int,
    rows: list[SequenceRow],
) -> None:
    """Insert sequence rows for one wrestler in order."""
    connection.executemany(
        """
        INSERT INTO sequences (
            wrestler_id, seq_order, origin, start_time, end_time, attacking,
            tie_up, team_moves, opponent_moves, team_scores, opponent_scores,
            net_points, adjusted_net_points, match_result, match_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                wrestler_id,
                order,
                row.origin,
                int(row.start_time),
                int(row.end_time),
                int(row.attacking),
                row.tie_up,
                json.dumps(row.team_moves, ensure_ascii=True),
                json.dumps(row.opponent_moves, ensure_ascii=True),
                json.dumps(row.team_scores, ensure_ascii=True),
                json.dumps(row.opponent_scores, ensure_ascii=True),
                int(row.net_points),
                int(row.adjusted_net_points),
                row.match_result,
                row.match_date,
            )
            for order, row in enumerate(rows)
        ],
    )


def apply_compile(
    rows_by_wrestler: dict[str, list[SequenceRow]],
    *,
    skip: set[str],
    manifest: dict[str, Any],
) -> list[str]:
    """Replace compiled data in one transaction and record provenance.

    Args:
        rows_by_wrestler: Roster-ordered mapping of wrestler name to new rows.
        skip: Wrestlers whose source video failed; their existing rows stay.
        manifest: The run manifest, stored in ``compile_runs`` for provenance.

    Returns:
        Per-wrestler write errors. An unexpected transaction failure raises.
    """
    init_db()
    errors: list[str] = []
    manifest_json: str = json.dumps(manifest, ensure_ascii=True)
    now: str = datetime.now(timezone.utc).isoformat()

    with _WRITE_LOCK:
        with _connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for index, (name, rows) in enumerate(rows_by_wrestler.items()):
                    if name in skip:
                        continue
                    savepoint: str = f"wrestler_{index}"
                    connection.execute(f"SAVEPOINT {savepoint}")
                    try:
                        existing = connection.execute(
                            "SELECT id FROM wrestlers WHERE name = ?", (name,)
                        ).fetchone()
                        if existing is not None:
                            connection.execute(
                                "DELETE FROM sequences WHERE wrestler_id = ?",
                                (int(existing["id"]),),
                            )
                        if rows:
                            if existing is None:
                                connection.execute(
                                    "INSERT INTO wrestlers (name, updated_at) "
                                    "VALUES (?, ?)",
                                    (name, now),
                                )
                                wrestler_id: int = int(
                                    connection.execute(
                                        "SELECT last_insert_rowid()"
                                    ).fetchone()[0]
                                )
                            else:
                                wrestler_id = int(existing["id"])
                                connection.execute(
                                    "UPDATE wrestlers SET updated_at = ? "
                                    "WHERE id = ?",
                                    (now, wrestler_id),
                                )
                            _insert_rows(connection, wrestler_id, rows)
                        elif existing is not None:
                            connection.execute(
                                "DELETE FROM wrestlers WHERE id = ?",
                                (int(existing["id"]),),
                            )
                        connection.execute(f"RELEASE {savepoint}")
                    except sqlite3.Error as error:
                        connection.execute(f"ROLLBACK TO {savepoint}")
                        connection.execute(f"RELEASE {savepoint}")
                        message: str = f"Failed to write data for '{name}': {error}"
                        logging.error(message)
                        errors.append(message)

                connection.execute(
                    """
                    INSERT INTO compile_runs (
                        manifest_schema_version, generated_at, ruleset,
                        ruleset_hash, app_version, taged_dir, manifest_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(manifest.get("schema_version", 0)),
                        str(manifest.get("generated_at", "")),
                        str(manifest.get("ruleset", "")),
                        str(manifest.get("ruleset_hash", "")),
                        str(manifest.get("app_version", "")),
                        str(manifest.get("taged_dir", "")),
                        manifest_json,
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM compile_runs
                    WHERE id NOT IN (
                        SELECT id FROM compile_runs ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (MAX_COMPILE_RUNS,),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    return errors
