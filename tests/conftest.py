import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

# helpers.py configures rotating file logging at import. Point it at a per-run
# temp directory so tests never append to the repository's MatStat.log.
os.environ.setdefault(
    "MATSTAT_LOG_FILE",
    str(Path(tempfile.mkdtemp(prefix="matstat-tests-")) / "MatStat.log"),
)


class FakeContext:
    """Minimal stand-in for scripts.web.jobs.JobContext."""

    def __init__(self, cancelled: bool = False) -> None:
        self.job_id: str = "testjob"
        self.cancel_event: threading.Event = threading.Event()
        self.reports: list[tuple[float, str]] = []
        self.cancelled: bool = cancelled

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))

from fastapi.testclient import TestClient

from scripts import storage
from scripts.helpers import (
    COL_ORIGIN, COL_START_TIME, COL_END_TIME, COL_ATTACKING, COL_TIE_UP,
    COL_TEAM_MOVES, COL_OPPONENT_MOVES, COL_TEAM_SCORES, COL_OPPONENT_SCORES,
    COL_NET_POINTS, COL_ADJUSTED_NET_POINTS, COL_MATCH_RESULT, COL_MATCH_DATE,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch) -> Path:
    """Point the compiled-data store at a per-test temp database.

    Every test gets an empty, initialized SQLite file so no test can read or
    write the repository's stats/wrestler_data/matstat.db.
    """
    db_path: Path = tmp_path / "stats" / "wrestler_data" / storage.DB_FILENAME
    monkeypatch.setattr(storage, "db_path", db_path)
    storage.init_db()
    return db_path


@pytest.fixture(autouse=True)
def fast_shutdown(monkeypatch) -> None:
    """Keep TestClient teardown short; bounded shutdown has its own tests.

    The production grace is 10 s, which would otherwise be paid by any test
    that leaves a job running when its TestClient exits.
    """
    from scripts.web import app as web_app

    monkeypatch.setattr(web_app, "SHUTDOWN_GRACE_SECONDS", 0.2)


def seed_wrestler(
    name: str,
    frame: pd.DataFrame,
    *,
    match_result: str = "",
    match_date: str = "",
) -> None:
    """Write a DataFrame's rows into the compiled store for one wrestler.

    The frame's stored points are preserved (unlike a real compile, which
    recomputes them from the score codes), so tests can assert exact values.

    Args:
        name: The wrestler name.
        frame: A compiled-contract DataFrame (index = Origin).
        match_result: Default W/L result when the frame does not carry one.
        match_date: Default match date when the frame does not carry one.
    """
    rows: list[storage.SequenceRow] = []
    for origin, row in frame.iterrows():
        rows.append(storage.SequenceRow(
            origin=str(origin),
            start_time=int(row[COL_START_TIME]),
            end_time=int(row[COL_END_TIME]),
            attacking=bool(row[COL_ATTACKING]),
            tie_up=str(row.get(COL_TIE_UP, "")),
            team_moves=list(row.get(COL_TEAM_MOVES, [])),
            opponent_moves=list(row.get(COL_OPPONENT_MOVES, [])),
            team_scores=list(row.get(COL_TEAM_SCORES, [])),
            opponent_scores=list(row.get(COL_OPPONENT_SCORES, [])),
            net_points=int(row.get(COL_NET_POINTS, 0)),
            adjusted_net_points=int(row.get(COL_ADJUSTED_NET_POINTS, 0)),
            match_result=str(row.get(COL_MATCH_RESULT, match_result)),
            match_date=str(row.get(COL_MATCH_DATE, match_date)),
        ))
    storage.apply_compile(
        {name: rows},
        skip=set(),
        manifest={
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "ruleset": "Folkstyle",
            "ruleset_hash": "test",
            "app_version": "test",
            "taged_dir": "test",
        },
    )


def seed_compiled_lines(name: str, lines: list[str]) -> None:
    """Seed one wrestler from compiled CSV-shaped rows.

    Accepts the 9-field compile format plus the 12/13-field enriched format
    that includes stored net points, adjusted points, result, and date. Quotes
    around fields are tolerated. Nine-field rows are scored from their codes
    exactly like a real compile.

    Args:
        name: The wrestler name.
        lines: Compiled CSV rows (one string per sequence).
    """
    import csv

    rows: list[storage.SequenceRow] = []
    for line in lines:
        if not line.strip():
            continue
        fields: list[str] = next(csv.reader([line]))
        if len(fields) == 9:
            rows.extend(storage.parse_compiled_rows(",".join(fields)))
            continue
        if len(fields) not in (10, 11, 12, 13):
            raise ValueError(f"Unsupported test row width: {len(fields)}")
        base: list[str] = fields[:9]
        origin, start, end, attack, tie_up, team_moves, opponent_moves, \
            team_scores, opponent_scores = base
        net: str = fields[9]
        adjusted: str = fields[10] if len(fields) > 10 else net
        result: str = fields[11] if len(fields) > 11 else ""
        date: str = fields[12] if len(fields) > 12 else ""
        rows.append(storage.SequenceRow(
            origin=origin,
            start_time=int(float(start)),
            end_time=int(float(end)),
            attacking=attack == "A",
            tie_up=tie_up,
            team_moves=team_moves.split(":") if team_moves else [],
            opponent_moves=opponent_moves.split(":") if opponent_moves else [],
            team_scores=team_scores.split(":") if team_scores else [],
            opponent_scores=opponent_scores.split(":") if opponent_scores else [],
            net_points=int(float(net)),
            adjusted_net_points=int(float(adjusted)),
            match_result=result,
            match_date=date,
        ))
    storage.apply_compile(
        {name: rows},
        skip=set(),
        manifest={
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "ruleset": "Folkstyle",
            "ruleset_hash": "test",
            "app_version": "test",
            "taged_dir": "test",
        },
    )


def _make_test_client(tmp_path, monkeypatch, logging_enabled: bool, auth_enabled: bool = False):
    """Shared TestClient setup with all video/data dirs redirected to tmp_path.

    Authentication is disabled by default so the existing tests run without a
    session; ``auth_enabled=True`` (used by the auth tests) enables it with a
    known test password.
    """
    from scripts import helpers as helpers_module
    from scripts.web import app as web_app
    from scripts.web import tag as tag_module
    from scripts.web import transcode as transcode_module
    from scripts.web import auth as auth_module
    from scripts.web import configs as configs_module

    preview_dirs: dict[str, Path] = {
        "untaged": tmp_path / "untaged",
        "taged": tmp_path / "taged",
        "clips": tmp_path / "clips",
    }
    for directory in preview_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)

    cfg_tmp: Path = tmp_path / "cfg"
    cfg_tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(helpers_module, "cfg_dir", cfg_tmp)
    monkeypatch.setattr(configs_module, "cfg_dir", cfg_tmp)

    monkeypatch.setattr(web_app, "_PREVIEW_DIRS", preview_dirs)
    monkeypatch.setattr(web_app, "_UPLOAD_DIR", preview_dirs["untaged"])
    monkeypatch.setattr(web_app, "_VISIBLE_LOGGING", logging_enabled)
    monkeypatch.setattr(auth_module, "_AUTH_OFF", not auth_enabled)
    monkeypatch.setattr(auth_module, "_PASSWORD", "test-secret" if auth_enabled else None)
    monkeypatch.setattr(auth_module, "_CURRENT_SESSION", None)
    monkeypatch.setattr(auth_module, "_LOGIN_ATTEMPTS", {})
    monkeypatch.setattr(tag_module, "untaged_dir", preview_dirs["untaged"])
    monkeypatch.setattr(tag_module, "taged_dir", preview_dirs["taged"])
    monkeypatch.setattr(tag_module, "tmp_dir", tmp_path / "tmp")
    monkeypatch.setattr(transcode_module, "PREVIEW_DIR", tmp_path / "preview")

    with TestClient(web_app.app) as test_client:
        yield test_client


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with the log panel disabled (the shipped default)."""
    yield from _make_test_client(tmp_path, monkeypatch, logging_enabled=False)


@pytest.fixture
def logging_client(tmp_path, monkeypatch):
    """TestClient with the log panel + WebSocket log streaming enabled."""
    yield from _make_test_client(tmp_path, monkeypatch, logging_enabled=True)


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    """TestClient with authentication enabled (MATSTAT_PASSWORD set)."""
    yield from _make_test_client(tmp_path, monkeypatch, logging_enabled=False, auth_enabled=True)



@pytest.fixture
def sample_df() -> pd.DataFrame:
    """A 6-row DataFrame with known moves and scores for stats tests."""
    return pd.DataFrame({
        COL_START_TIME: [0, 10, 20, 30, 40, 50],
        COL_END_TIME: [10, 20, 30, 40, 50, 60],
        COL_ATTACKING: [True, False, True, False, True, False],
        COL_TIE_UP: [
            "collar tie", "standing", "front headlock",
            "regular ride", "standing", "in base",
        ],
        COL_TEAM_MOVES: [
            ["high crotch", "double"],
            ["sprawl"],
            ["single"],
            ["whizzer", "sprawl"],
            ["double"],
            ["quad pod", "kick out"],
        ],
        COL_OPPONENT_MOVES: [
            ["sprawl"],
            ["sweep single"],
            ["whizzer"],
            ["sweep single", "double"],
            ["sprawl", "whizzer"],
            ["stand up"],
        ],
        COL_TEAM_SCORES: [
            ["T", "N2"],
            [],
            ["T"],
            [],
            ["None"],
            [],
        ],
        COL_OPPONENT_SCORES: [
            ["E"],
            ["T"],
            [],
            ["R"],
            [],
            ["None"],
        ],
        COL_NET_POINTS: [4, -3, 3, -2, 0, 0],
        COL_ADJUSTED_NET_POINTS: [4, -3, 3, -2, 0, 0],
    }, index=pd.Index(
        ["vid.mkv:1", "vid.mkv:2", "vid.mkv:3", "vid.mkv:4", "vid.mkv:5", "vid.mkv:6"],
        name=COL_ORIGIN,
    ))


@pytest.fixture
def empty_df() -> pd.DataFrame:
    """An empty DataFrame with the correct columns."""
    return pd.DataFrame({
        COL_START_TIME: pd.Series([], dtype=int),
        COL_END_TIME: pd.Series([], dtype=int),
        COL_ATTACKING: pd.Series([], dtype=bool),
        COL_TIE_UP: pd.Series([], dtype=str),
        COL_TEAM_MOVES: pd.Series([], dtype=object),
        COL_OPPONENT_MOVES: pd.Series([], dtype=object),
        COL_TEAM_SCORES: pd.Series([], dtype=object),
        COL_OPPONENT_SCORES: pd.Series([], dtype=object),
        COL_NET_POINTS: pd.Series([], dtype=int),
        COL_ADJUSTED_NET_POINTS: pd.Series([], dtype=int),
    }, index=pd.Index([], name=COL_ORIGIN))


@pytest.fixture
def all_attack_df() -> pd.DataFrame:
    """DataFrame where every row is an attacking sequence."""
    return pd.DataFrame({
        COL_START_TIME: [0, 10],
        COL_END_TIME: [10, 20],
        COL_ATTACKING: [True, True],
        COL_TIE_UP: ["collar tie", "front headlock"],
        COL_TEAM_MOVES: [["double"], ["single"]],
        COL_OPPONENT_MOVES: [[], []],
        COL_TEAM_SCORES: [["T"], ["N2"]],
        COL_OPPONENT_SCORES: [[], []],
        COL_NET_POINTS: [3, 2],
        COL_ADJUSTED_NET_POINTS: [3, 2],
    }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name=COL_ORIGIN))


@pytest.fixture
def all_defend_df() -> pd.DataFrame:
    """DataFrame where every row is a defending sequence."""
    return pd.DataFrame({
        COL_START_TIME: [0, 10],
        COL_END_TIME: [10, 20],
        COL_ATTACKING: [False, False],
        COL_TIE_UP: ["standing", "regular ride"],
        COL_TEAM_MOVES: [[], []],
        COL_OPPONENT_MOVES: [["sweep single"], ["sprawl"]],
        COL_TEAM_SCORES: [[], []],
        COL_OPPONENT_SCORES: [["T"], ["E"]],
        COL_NET_POINTS: [-3, -1],
        COL_ADJUSTED_NET_POINTS: [-3, -1],
    }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name=COL_ORIGIN))


@pytest.fixture
def simple_df() -> pd.DataFrame:
    """Minimal 2-row DataFrame for basic filter tests."""
    return pd.DataFrame({
        COL_START_TIME: [0, 10],
        COL_END_TIME: [10, 20],
        COL_ATTACKING: [True, False],
        COL_TIE_UP: ["collar tie", "standing"],
        COL_TEAM_MOVES: [["double"], ["sprawl"]],
        COL_OPPONENT_MOVES: [["sprawl"], ["sweep single"]],
        COL_TEAM_SCORES: [["T"], []],
        COL_OPPONENT_SCORES: [[], ["E"]],
        COL_NET_POINTS: [3, -1],
        COL_ADJUSTED_NET_POINTS: [3, -1],
    }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name=COL_ORIGIN))
