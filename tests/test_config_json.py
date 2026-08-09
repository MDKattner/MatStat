"""Tests for the JSON config layer in scripts.helpers (config.json + Wrestlers.json)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent.parent))

import scripts.helpers as helpers
from scripts.helpers import (
    AppConfig,
    AppConfigToDict,
    COL_TEAM_SCORES,
    LoadAppConfig,
    LoadRoster,
    LoadWrestlerNames,
    OutcomeSpec,
    PinCount,
    ReloadScoringMap,
    Roster,
    Ruleset,
    SaveAppConfig,
    SaveRoster,
)


def _snapshot_scoring_globals(monkeypatch) -> None:
    """Register the scoring globals so ReloadScoringMap() mutations restore after the test."""
    monkeypatch.setattr(helpers, "score_to_pts", dict(helpers.score_to_pts))
    monkeypatch.setattr(helpers, "_ACTIVE_PIN_CODES", set(helpers._ACTIVE_PIN_CODES))
    monkeypatch.setattr(helpers, "active_pin_points", helpers.active_pin_points)


def _minimal_cfg(active: str = "Alt") -> AppConfig:
    """A small two-ruleset config for load/save tests."""
    return AppConfig(
        active_ruleset=active,
        moves=["high crotch", "double"],
        ties=["collar tie"],
        rulesets={
            "Folkstyle": Ruleset(
                name="Folkstyle",
                pin_points=13,
                outcomes={
                    "T": OutcomeSpec(points=np.int16(3), description="Takedown"),
                    "PIN": OutcomeSpec(points=np.int16(0), counts_as_pin=True),
                },
            ),
            "Alt": Ruleset(
                name="Alt",
                description="Alternate",
                pin_points=5,
                outcomes={
                    "X": OutcomeSpec(points=np.int16(5)),
                    "FALL": OutcomeSpec(points=np.int16(0), counts_as_pin=True),
                },
            ),
        },
    )


class TestLoadAppConfig:
    """Tests for LoadAppConfig — reading config.json."""

    def test_parses_full_config(self, tmp_path) -> None:
        cfg: AppConfig = _minimal_cfg(active="Alt")
        path: Path = tmp_path / "config.json"
        SaveAppConfig(cfg, path)

        loaded: AppConfig = LoadAppConfig(path)
        assert loaded.active_ruleset == "Alt"
        assert loaded.moves == ["high crotch", "double"]
        assert loaded.ties == ["collar tie"]
        assert set(loaded.rulesets) == {"Folkstyle", "Alt"}
        assert loaded.rulesets["Alt"].outcomes["X"].points == np.int16(5)
        assert loaded.rulesets["Folkstyle"].outcomes["PIN"].counts_as_pin is True
        assert loaded.rulesets["Alt"].pin_points == 5

    def test_missing_file_falls_back_to_defaults(self, tmp_path) -> None:
        cfg: AppConfig = LoadAppConfig(tmp_path / "nope.json")
        assert cfg.active_ruleset == "Folkstyle"
        assert "T" in cfg.rulesets["Folkstyle"].outcomes

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path) -> None:
        path: Path = tmp_path / "config.json"
        path.write_text("{ not valid json")
        cfg: AppConfig = LoadAppConfig(path)
        assert cfg.active_ruleset == "Folkstyle"
        assert cfg.moves == []

    def test_invalid_active_ruleset_falls_back_to_first(self, tmp_path) -> None:
        path: Path = tmp_path / "config.json"
        path.write_text('{"active_ruleset": "Missing", "rulesets": {"A": {"outcomes": {}}}}')
        cfg: AppConfig = LoadAppConfig(path)
        assert cfg.active_ruleset == "A"

    def test_missing_pin_points_defaults_to_zero(self, tmp_path) -> None:
        path: Path = tmp_path / "config.json"
        path.write_text(
            '{"active_ruleset": "A", "rulesets": {"A": {"outcomes": {}}}}'
        )
        cfg: AppConfig = LoadAppConfig(path)
        assert cfg.rulesets["A"].pin_points == 0

    def test_repo_default_config_loads_folkstyle(self) -> None:
        cfg: AppConfig = LoadAppConfig()
        assert cfg.active_ruleset == "Folkstyle"
        assert cfg.moves  # seeded from the former Moves.config
        assert cfg.ties  # seeded from the former Ties.config
        assert cfg.rulesets["Folkstyle"].outcomes["T"].points == np.int16(3)
        assert cfg.rulesets["Folkstyle"].pin_points == 13


class TestSaveAppConfig:
    """Tests for SaveAppConfig — writing config.json with a backup."""

    def test_round_trip_and_backup(self, tmp_path) -> None:
        path: Path = tmp_path / "config.json"
        path.write_text('{"active_ruleset": "Folkstyle", "moves": [], "ties": [], "rulesets": {}}')

        SaveAppConfig(_minimal_cfg(active="Alt"), path)

        assert path.with_suffix(".json.bak").exists()
        loaded: AppConfig = LoadAppConfig(path)
        assert loaded.active_ruleset == "Alt"
        assert loaded.rulesets["Alt"].outcomes["FALL"].counts_as_pin is True

    def test_serialization_round_trips(self) -> None:
        cfg: AppConfig = _minimal_cfg(active="Alt")
        import json

        data: dict[str, object] = AppConfigToDict(cfg)
        json.dumps(data)  # must not raise (numpy values serialized as ints)
        reparsed: AppConfig = _parse_via_disk(data)
        assert reparsed.active_ruleset == "Alt"
        assert reparsed.rulesets["Alt"].outcomes["X"].points == np.int16(5)


def _parse_via_disk(data: dict[str, object]) -> AppConfig:
    """Re-parse a serialized dict through the JSON loader for a faithful round-trip."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path: Path = Path(tmp) / "config.json"
        path.write_text(json.dumps(data))
        return LoadAppConfig(path)


class TestActiveRulesetAccessors:
    """Tests for GetActiveRuleset / GetOutcomeMap / GetMovesList / GetTiesList."""

    def test_outcome_map_uses_active_ruleset(self, tmp_path) -> None:
        path: Path = tmp_path / "config.json"
        SaveAppConfig(_minimal_cfg(active="Alt"), path)

        active: Ruleset = helpers.GetActiveRuleset(path)
        assert active.name == "Alt"
        outcome_map: dict[str, OutcomeSpec] = helpers.GetOutcomeMap(path)
        assert set(outcome_map) == {"X", "FALL"}
        assert helpers.GetMovesList(path) == ["high crotch", "double"]
        assert helpers.GetTiesList(path) == ["collar tie"]


class TestReloadScoringMap:
    """Tests for ReloadScoringMap — refreshing the module scoring map."""

    def test_reflects_active_ruleset(self, tmp_path, monkeypatch) -> None:
        _snapshot_scoring_globals(monkeypatch)
        monkeypatch.setattr(helpers, "cfg_dir", tmp_path)
        SaveAppConfig(_minimal_cfg(active="Alt"), tmp_path / "config.json")

        ReloadScoringMap()

        assert helpers.score_to_pts == {"X": np.int16(5), "FALL": np.int16(0)}
        assert "FALL" in helpers._ACTIVE_PIN_CODES

    def test_pin_count_uses_config_pin_codes(self, tmp_path, monkeypatch) -> None:
        _snapshot_scoring_globals(monkeypatch)
        monkeypatch.setattr(helpers, "cfg_dir", tmp_path)
        SaveAppConfig(_minimal_cfg(active="Alt"), tmp_path / "config.json")
        ReloadScoringMap()

        df: pd.DataFrame = pd.DataFrame({COL_TEAM_SCORES: [["FALL"], ["X"], ["T"]]})
        assert PinCount(df, COL_TEAM_SCORES) == 1

    def test_active_pin_points_from_ruleset(self, tmp_path, monkeypatch) -> None:
        _snapshot_scoring_globals(monkeypatch)
        monkeypatch.setattr(helpers, "cfg_dir", tmp_path)
        SaveAppConfig(_minimal_cfg(active="Alt"), tmp_path / "config.json")
        ReloadScoringMap()
        assert helpers.active_pin_points == 5


class TestLoadRoster:
    """Tests for LoadRoster — reading Wrestlers.json with .example fallback."""

    def test_parses_roster(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        path.write_text(
            '{"wrestlers": ["Alice", "Bob"], "teams": {"Varsity": ["Alice"], "JV": ["Alice", "Bob"]}}'
        )
        roster: Roster = LoadRoster(path)
        assert roster.wrestlers == ["Alice", "Bob"]
        assert roster.teams["JV"] == ["Alice", "Bob"]

    def test_dedupes_wrestlers(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        path.write_text('{"wrestlers": ["Alice", "Alice", "Bob"], "teams": {}}')
        assert LoadRoster(path).wrestlers == ["Alice", "Bob"]

    def test_example_fallback(self, tmp_path) -> None:
        (tmp_path / "Wrestlers.json.example").write_text(
            '{"wrestlers": ["UNKNOWN"], "teams": {}}'
        )
        roster: Roster = LoadRoster(tmp_path / "Wrestlers.json")
        assert roster.wrestlers == ["UNKNOWN"]

    def test_missing_returns_empty(self, tmp_path) -> None:
        assert LoadRoster(tmp_path / "Wrestlers.json").wrestlers == []

    def test_corrupt_falls_back_to_example(self, tmp_path) -> None:
        (tmp_path / "Wrestlers.json").write_text("{ bad")
        (tmp_path / "Wrestlers.json.example").write_text(
            '{"wrestlers": ["UNKNOWN"], "teams": {}}'
        )
        assert LoadRoster(tmp_path / "Wrestlers.json").wrestlers == ["UNKNOWN"]


class TestSaveRoster:
    """Tests for SaveRoster — writing Wrestlers.json with normalization."""

    def test_round_trip(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        roster: Roster = Roster(
            wrestlers=["Alice", "Bob"],
            teams={"Varsity": ["Alice"], "JV": ["Bob", "Alice"]},
        )
        SaveRoster(roster, path)

        loaded: Roster = LoadRoster(path)
        assert loaded.wrestlers == ["Alice", "Bob"]
        assert loaded.teams == {"Varsity": ["Alice"], "JV": ["Bob", "Alice"]}

    def test_drops_unknown_members(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        SaveRoster(
            Roster(
                wrestlers=["Alice"],
                teams={"Varsity": ["Alice", "Ghost"]},
            ),
            path,
        )
        assert LoadRoster(path).teams == {"Varsity": ["Alice"]}

    def test_writes_backup(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        path.write_text('{"wrestlers": ["Old"], "teams": {}}')
        SaveRoster(Roster(wrestlers=["New"], teams={}), path)
        assert path.with_suffix(".json.bak").exists()


class TestLoadWrestlerNames:
    """Tests for LoadWrestlerNames — names only."""

    def test_returns_names(self, tmp_path) -> None:
        path: Path = tmp_path / "Wrestlers.json"
        path.write_text(
            '{"wrestlers": ["Alice", "Bob"], "teams": {"Varsity": ["Alice"]}}'
        )
        assert LoadWrestlerNames(path) == ["Alice", "Bob"]

    def test_empty_when_nothing_exists(self, tmp_path) -> None:
        assert LoadWrestlerNames(tmp_path / "Wrestlers.json") == []
