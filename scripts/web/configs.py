"""Config file loading for the web app (cfg/*.config).

The web layer reuses `LoadConfigItems` from scripts.helpers (never forks the
parser) and adds name validation so arbitrary paths can't be read. This is the
backend for the tag-film selectors (wrestlers, ties, moves, outcomes).
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.helpers import LoadConfigItems, cfg_dir

# Only plain config names inside cfg/ are allowed (e.g. "Moves.config").
_SAFE_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]+\.config$")


def resolve_config_path(name: str) -> Path | None:
    """Validate a config name and resolve it to a path inside cfg_dir.

    Args:
        name: A config file name (e.g. "Moves.config").

    Returns:
        The resolved Path, or None if the name is invalid or escapes cfg_dir.
    """
    if _SAFE_NAME_RE.match(name) is None:
        return None
    candidate: Path = (cfg_dir / name).resolve()
    if not str(candidate).startswith(str(cfg_dir.resolve())):
        return None
    return candidate


def load_config_items(name: str) -> list[str]:
    """Load the parsed entries from a named config file.

    Args:
        name: A config file name (e.g. "Moves.config").

    Returns:
        The parsed entries, or an empty list for invalid/missing files.
    """
    config_path: Path | None = resolve_config_path(name)
    if config_path is None or not config_path.is_file():
        return []
    return LoadConfigItems(config_path)
