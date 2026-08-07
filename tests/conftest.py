import sys
from pathlib import Path

import pytest
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from scripts.helpers import (
    COL_ORIGIN, COL_START_TIME, COL_END_TIME, COL_ATTACKING, COL_TIE_UP,
    COL_TEAM_MOVES, COL_OPPONENT_MOVES, COL_TEAM_SCORES, COL_OPPONENT_SCORES,
    COL_NET_POINTS,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with all video/data dirs redirected into a temp dir.

    Redirects the preview dirs, the upload dir, and the tag job's data dirs
    so routes and background jobs operate on the same tmp_path (auto-restored
    by monkeypatch after each test).
    """
    from scripts.web import app as web_app
    from scripts.web import tag as tag_module

    preview_dirs: dict[str, Path] = {
        "untaged": tmp_path / "untaged",
        "taged": tmp_path / "taged",
        "clips": tmp_path / "clips",
    }
    for directory in preview_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(web_app, "_PREVIEW_DIRS", preview_dirs)
    monkeypatch.setattr(web_app, "_UPLOAD_DIR", preview_dirs["untaged"])
    monkeypatch.setattr(tag_module, "untaged_dir", preview_dirs["untaged"])
    monkeypatch.setattr(tag_module, "taged_dir", preview_dirs["taged"])
    monkeypatch.setattr(tag_module, "tmp_dir", tmp_path / "tmp")

    with TestClient(web_app.app) as test_client:
        yield test_client



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
    }, index=pd.Index(["v.mkv:1", "v.mkv:2"], name=COL_ORIGIN))
