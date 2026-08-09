"""Config loading and writing for the web app (JSON configs).

The web layer wraps the JSON config helpers in scripts.helpers (LoadAppConfig,
SaveAppConfig, LoadRoster, SaveRoster) and adds pydantic request models for the
API. This is the backend for the config editor and the tag-film/combine-clips
selectors (moves, ties, wrestlers, rulesets).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from scripts import helpers
from scripts.helpers import cfg_dir


class OutcomeUpdate(BaseModel):
    """Request body for a single outcome code within a ruleset."""

    points: int
    description: str = ""
    counts_as_pin: bool = False


class RulesetUpdate(BaseModel):
    """Request body for a ruleset's metadata and outcome map."""

    description: str = ""
    pin_points: int = 0
    outcomes: dict[str, OutcomeUpdate] = {}


class AppConfigUpdate(BaseModel):
    """Request body for saving the app config (moves, ties, rulesets)."""

    active_ruleset: str
    moves: list[str] = []
    ties: list[str] = []
    rulesets: dict[str, RulesetUpdate] = {}


class RosterUpdate(BaseModel):
    """Request body for saving the wrestler roster."""

    wrestlers: list[str] = []
    teams: dict[str, list[str]] = {}


def load_app_config() -> helpers.AppConfig:
    """Load the app config from cfg/config.json, falling back to defaults."""
    return helpers.LoadAppConfig(cfg_dir / "config.json")


def save_app_config(update: AppConfigUpdate) -> helpers.AppConfig:
    """Validate and persist an AppConfigUpdate as cfg/config.json.

    Args:
        update: The parsed request body.

    Returns:
        The saved AppConfig.

    Raises:
        ValueError: If ``active_ruleset`` is not among the given rulesets.
    """
    if update.active_ruleset not in update.rulesets:
        raise ValueError(
            f"active_ruleset {update.active_ruleset!r} is not in rulesets"
        )
    moves: list[str] = [m.strip() for m in update.moves if m.strip()]
    ties: list[str] = [t.strip() for t in update.ties if t.strip()]
    rulesets: dict[str, helpers.Ruleset] = {}
    for name, ruleset_update in update.rulesets.items():
        name_str: str = name.strip()
        if not name_str:
            continue
        outcomes: dict[str, helpers.OutcomeSpec] = {}
        for code, outcome_update in ruleset_update.outcomes.items():
            code_str: str = code.strip()
            if not code_str:
                continue
            outcomes[code_str] = helpers.OutcomeSpec(
                points=np.int16(outcome_update.points),
                description=outcome_update.description.strip(),
                counts_as_pin=outcome_update.counts_as_pin,
            )
        rulesets[name_str] = helpers.Ruleset(
            name=name_str,
            description=ruleset_update.description.strip(),
            pin_points=ruleset_update.pin_points,
            outcomes=outcomes,
        )
    cfg: helpers.AppConfig = helpers.AppConfig(
        active_ruleset=update.active_ruleset.strip(),
        moves=moves,
        ties=ties,
        rulesets=rulesets,
    )
    helpers.SaveAppConfig(cfg, cfg_dir / "config.json")
    return cfg


def app_config_to_dict(cfg: helpers.AppConfig) -> dict[str, object]:
    """Serialize an AppConfig for JSON responses."""
    return helpers.AppConfigToDict(cfg)


def load_roster() -> helpers.Roster:
    """Load the wrestler roster from cfg/Wrestlers.json (or .example)."""
    return helpers.LoadRoster(cfg_dir / "Wrestlers.json")


def save_roster(update: RosterUpdate) -> helpers.Roster:
    """Persist a RosterUpdate as cfg/Wrestlers.json (normalized).

    Args:
        update: The parsed request body.

    Returns:
        The saved (normalized) Roster — team members not on the wrestler list
        are dropped, matching what was written to disk.
    """
    wrestlers: list[str] = [w.strip() for w in update.wrestlers if w.strip()]
    teams: dict[str, list[str]] = {}
    for team, members in update.teams.items():
        team_name: str = team.strip()
        if not team_name:
            continue
        teams[team_name] = [m.strip() for m in members if m.strip()]
    roster: helpers.Roster = helpers.Roster(wrestlers=wrestlers, teams=teams)
    helpers.SaveRoster(roster, cfg_dir / "Wrestlers.json")
    return helpers.LoadRoster(cfg_dir / "Wrestlers.json")


def load_wrestler_names() -> list[str]:
    """Return the configured wrestler names (without team membership)."""
    return helpers.LoadWrestlerNames(cfg_dir / "Wrestlers.json")
