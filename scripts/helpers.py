from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, overload

import numpy as np
import pandas as pd


# ---- Constants ----

EMPTY_SENTINEL: str = "EMPTY"

COL_ORIGIN: str = "Origin"
COL_START_TIME: str = "Start Time"
COL_END_TIME: str = "End Time"
COL_ATTACKING: str = "Attacking"
COL_TIE_UP: str = "Tie Up"
COL_TEAM_MOVES: str = "Team Moves"
COL_OPPONENT_MOVES: str = "Opponent Moves"
COL_TEAM_SCORES: str = "Team Scores"
COL_OPPONENT_SCORES: str = "Opponent Scores"
COL_NET_POINTS: str = "Net Points"
COL_ADJUSTED_NET_POINTS: str = "Adjusted Net Points"
COL_MATCH_RESULT: str = "W/L"

# Title storage conventions for tagged videos. Single mode stores the bare
# wrestler name (optionally suffixed with " (W)" / " (L)"); dual mode joins the
# two names with TITLE_DELIMITER and puts " (W)" on the winner's name.
TITLE_DELIMITER: str = " / "
WIN_MARKER: str = " (W)"
LOSS_MARKER: str = " (L)"

# ---- Global paths ----

taged_dir: Path = Path("vids") / "taged"
untaged_dir: Path = Path("vids") / "untaged"
tmp_dir: Path = Path("tmp")
csv_dir: Path = Path("stats") / "wrestler_data"
eval_dir: Path = Path("stats") / "reports"
cfg_dir: Path = Path("cfg")
clips_dir: Path = Path("vids") / "clips"
log_file: Path = Path("MatStat.log")

logging.basicConfig(filename=log_file, level=logging.DEBUG,
                    format="%(asctime)s:%(levelname)s:%(message)s")

# ---- JSON configuration (cfg/config.json) ----

# Fallback scoring map used when cfg/config.json is missing or corrupt. Values
# match the historical folkstyle defaults so net-point math stays stable.
DEFAULT_FOLKSTYLE_OUTCOMES: dict[str, dict[str, object]] = {
    "None": {"points": 0, "description": "Nothing"},
    "T": {"points": 3, "description": "Takedown"},
    "E": {"points": 1, "description": "Escape (earned only)"},
    "R": {"points": 2, "description": "Reversal"},
    "N2": {"points": 2, "description": "Two nearfall"},
    "N3": {"points": 3, "description": "Three nearfall"},
    "N4": {"points": 4, "description": "Four nearfall"},
    "N5": {"points": 5, "description": "Five nearfall"},
    # Pins are tabulated separately so they are not factored into net points
    "PIN": {"points": 0, "description": "Pin", "counts_as_pin": True},
    "P1": {"points": 1, "description": "Penalty and one point"},
    "P2": {"points": 2, "description": "Penalty and two points"},
    "S": {"points": 0, "description": "Stalling"},
}


@dataclass
class OutcomeSpec:
    """A single scorebook outcome: code -> point value within a ruleset."""

    points: np.int16
    description: str = ""
    counts_as_pin: bool = False


@dataclass
class Ruleset:
    """A wrestling ruleset and its outcome scoring map."""

    name: str
    description: str = ""
    pin_points: int = 0
    outcomes: dict[str, OutcomeSpec] = field(default_factory=dict)


@dataclass
class AppConfig:
    """The app config: shared move/tie lists plus per-ruleset scoring maps."""

    active_ruleset: str
    moves: list[str] = field(default_factory=list)
    ties: list[str] = field(default_factory=list)
    rulesets: dict[str, Ruleset] = field(default_factory=dict)


@dataclass
class Roster:
    """Wrestler names and team membership (a wrestler may be in many teams)."""

    wrestlers: list[str] = field(default_factory=list)
    teams: dict[str, list[str]] = field(default_factory=dict)


def _DefaultAppConfig() -> AppConfig:
    """Build the fallback config used when config.json is missing or corrupt."""
    folkstyle: Ruleset = Ruleset(
        name="Folkstyle",
        description="USA Wrestling folkstyle (college / high school)",
        pin_points=13,
        outcomes={
            code: OutcomeSpec(
                points=np.int16(spec["points"]),
                description=str(spec.get("description", "")),
                counts_as_pin=bool(spec.get("counts_as_pin", False)),
            )
            for code, spec in DEFAULT_FOLKSTYLE_OUTCOMES.items()
        },
    )
    return AppConfig(
        active_ruleset="Folkstyle", moves=[], ties=[], rulesets={"Folkstyle": folkstyle}
    )


def _ParsePinPoints(raw: object) -> int:
    """Parse a ruleset's pin bonus, tolerating missing or invalid values.

    Args:
        raw: The raw ruleset dict from the JSON config.

    Returns:
        The pin bonus in points, or 0 when absent/unparseable (migration-safe).
    """
    if not isinstance(raw, dict):
        return 0
    try:
        return int(raw.get("pin_points", 0))
    except (TypeError, ValueError):
        return 0


def _ParseAppConfig(data: object) -> AppConfig:
    """Parse a loaded JSON value into an AppConfig, tolerating malformed sections."""
    fallback: AppConfig = _DefaultAppConfig()
    if not isinstance(data, dict):
        return fallback

    moves: list[str] = [str(m).strip() for m in data.get("moves", []) if str(m).strip()]
    ties: list[str] = [str(t).strip() for t in data.get("ties", []) if str(t).strip()]

    rulesets: dict[str, Ruleset] = {}
    raw_rulesets: object = data.get("rulesets", {})
    if isinstance(raw_rulesets, dict):
        for name, raw in raw_rulesets.items():
            name_str: str = str(name).strip()
            if not name_str or not isinstance(raw, dict):
                continue
            outcomes: dict[str, OutcomeSpec] = {}
            raw_outcomes: object = raw.get("outcomes", {})
            if isinstance(raw_outcomes, dict):
                for code, spec in raw_outcomes.items():
                    code_str: str = str(code).strip()
                    if not code_str or not isinstance(spec, dict):
                        continue
                    try:
                        points: np.int16 = np.int16(spec["points"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    outcomes[code_str] = OutcomeSpec(
                        points=points,
                        description=str(spec.get("description", "")),
                        counts_as_pin=bool(spec.get("counts_as_pin", False)),
                    )
            rulesets[name_str] = Ruleset(
                name=name_str,
                description=str(raw.get("description", "")),
                pin_points=_ParsePinPoints(raw),
                outcomes=outcomes,
            )

    if not rulesets:
        rulesets = fallback.rulesets
    active: str = str(data.get("active_ruleset", "")).strip()
    if active not in rulesets:
        active = next(iter(rulesets), fallback.active_ruleset)
    return AppConfig(active_ruleset=active, moves=moves, ties=ties, rulesets=rulesets)


def AppConfigToDict(cfg: AppConfig) -> dict[str, object]:
    """Serialize an AppConfig to a JSON-compatible dict."""
    return {
        "active_ruleset": cfg.active_ruleset,
        "moves": cfg.moves,
        "ties": cfg.ties,
        "rulesets": {
            name: {
                "description": ruleset.description,
                "pin_points": int(ruleset.pin_points),
                "outcomes": {
                    code: {
                        "points": int(spec.points),
                        "description": spec.description,
                        "counts_as_pin": spec.counts_as_pin,
                    }
                    for code, spec in ruleset.outcomes.items()
                },
            }
            for name, ruleset in cfg.rulesets.items()
        },
    }


def _WriteJsonWithBackup(config_path: Path, data: dict[str, object]) -> None:
    """Write a JSON dict atomically, keeping a ``.bak`` copy of the previous file."""
    if config_path.is_file():
        try:
            shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + ".bak"))
        except OSError as e:
            logging.warning(f"Could not create backup for {config_path}: {e}")
    tmp_path: Path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n")
    tmp_path.replace(config_path)


def LoadAppConfig(config_path: Path | None = None) -> AppConfig:
    """Load the app config (moves, ties, rulesets) from a JSON file.

    Falls back to the built-in folkstyle defaults when the file is missing or
    unparseable, so scoring logic always has a map to use.

    Args:
        config_path: Path to the JSON config. Defaults to cfg/config.json.

    Returns:
        A populated AppConfig.
    """
    if config_path is None:
        config_path = cfg_dir / "config.json"
    try:
        data: object = json.loads(config_path.read_text())
        return _ParseAppConfig(data)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(f"Could not load app config {config_path}: {e}; using defaults")
        return _DefaultAppConfig()


def SaveAppConfig(cfg: AppConfig, config_path: Path | None = None) -> None:
    """Persist an AppConfig as JSON, backing up the previous file first.

    Args:
        cfg: The config to write.
        config_path: Target path; defaults to cfg/config.json.
    """
    if config_path is None:
        config_path = cfg_dir / "config.json"
    _WriteJsonWithBackup(config_path, AppConfigToDict(cfg))


def GetActiveRuleset(config_path: Path | None = None) -> Ruleset:
    """Return the currently active ruleset."""
    cfg: AppConfig = LoadAppConfig(config_path)
    return cfg.rulesets.get(cfg.active_ruleset, next(iter(cfg.rulesets.values())))


def GetOutcomeMap(config_path: Path | None = None) -> dict[str, OutcomeSpec]:
    """Return the active ruleset's outcome map (code -> OutcomeSpec)."""
    return dict(GetActiveRuleset(config_path).outcomes)


def GetMovesList(config_path: Path | None = None) -> list[str]:
    """Return the shared move list from the app config."""
    return list(LoadAppConfig(config_path).moves)


def GetTiesList(config_path: Path | None = None) -> list[str]:
    """Return the shared tie/position list from the app config."""
    return list(LoadAppConfig(config_path).ties)


_ACTIVE_PIN_CODES: frozenset[str] = frozenset()
score_to_pts: dict[str, np.int16] = {}
active_pin_points: int = 0


def ReloadScoringMap() -> None:
    """Refresh the module-level scoring map from the active ruleset.

    Called at import time and before tabulating net points so CSV loads reflect
    the currently selected ruleset.
    """
    global score_to_pts, _ACTIVE_PIN_CODES, active_pin_points
    active: Ruleset = GetActiveRuleset()
    score_to_pts = {code: spec.points for code, spec in active.outcomes.items()}
    _ACTIVE_PIN_CODES = frozenset(
        code for code, spec in active.outcomes.items() if spec.counts_as_pin
    )
    active_pin_points = int(active.pin_points)


ReloadScoringMap()


def _ParseRoster(data: object) -> Roster:
    """Parse a loaded JSON value into a Roster, deduping names and members."""
    roster: Roster = Roster()
    if not isinstance(data, dict):
        return roster
    seen: set[str] = set()
    for name in data.get("wrestlers", []):
        cleaned: str = str(name).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            roster.wrestlers.append(cleaned)
    raw_teams: object = data.get("teams", {})
    if isinstance(raw_teams, dict):
        for team, members in raw_teams.items():
            team_name: str = str(team).strip()
            if not team_name:
                continue
            member_list: list[str] = []
            member_seen: set[str] = set()
            if isinstance(members, list):
                for member in members:
                    cleaned = str(member).strip()
                    if cleaned and cleaned not in member_seen:
                        member_seen.add(cleaned)
                        member_list.append(cleaned)
            roster.teams[team_name] = member_list
    return roster


def RosterToDict(roster: Roster) -> dict[str, object]:
    """Serialize a Roster to a JSON-compatible dict."""
    return {"wrestlers": list(roster.wrestlers), "teams": dict(roster.teams)}


def LoadRoster(config_path: Path | None = None) -> Roster:
    """Load the wrestler roster (names + team membership) from a JSON file.

    Falls back to a ``.example`` sibling when the real file is absent, so a
    fresh clone (where the real file is gitignored) still has a roster.

    Args:
        config_path: Path to the roster JSON. Defaults to cfg/Wrestlers.json.

    Returns:
        A Roster (empty when no file exists anywhere).
    """
    if config_path is None:
        config_path = cfg_dir / "Wrestlers.json"
    candidates: list[Path] = [
        config_path,
        config_path.with_suffix(config_path.suffix + ".example"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return _ParseRoster(json.loads(candidate.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load roster {candidate}: {e}")
    return Roster()


def SaveRoster(roster: Roster, config_path: Path | None = None) -> None:
    """Persist a Roster as JSON, dropping team members not on the wrestler list.

    Args:
        roster: The roster to write.
        config_path: Target path; defaults to cfg/Wrestlers.json.
    """
    normalized: Roster = Roster(
        wrestlers=[w for w in dict.fromkeys(roster.wrestlers) if w.strip()],
        teams={},
    )
    known: set[str] = set(normalized.wrestlers)
    for team, members in roster.teams.items():
        team_name: str = team.strip()
        if not team_name:
            continue
        kept: list[str] = []
        for member in dict.fromkeys(members):
            member_name: str = member.strip()
            if member_name in known:
                kept.append(member_name)
            else:
                logging.warning(
                    f"Roster team '{team_name}' references unknown wrestler "
                    f"'{member_name}'; dropping"
                )
        normalized.teams[team_name] = kept
    if config_path is None:
        config_path = cfg_dir / "Wrestlers.json"
    _WriteJsonWithBackup(config_path, RosterToDict(normalized))


def LoadWrestlerNames(config_path: Path | None = None) -> list[str]:
    """Return the list of wrestler names (without team membership)."""
    return list(LoadRoster(config_path).wrestlers)

# Create directories that are not tracked by git
csv_dir.mkdir(parents=True, exist_ok=True)
eval_dir.mkdir(parents=True, exist_ok=True)
tmp_dir.mkdir(parents=True, exist_ok=True)
logging.info(f"Ensured directories {csv_dir}, {eval_dir}, {tmp_dir} exist.")


@dataclass(order=True)
class ChapterSequence:
    """A representation of the data encoded in a single chapter title.

    The titles are in the following format:
    {Attacking or Defending},{Starting Tie/Position},{Your Wrestler's Moves},{Opponent's Moves},{Your Wrestler's Scorebook Data},{Opponent's Scorebook Data}
    Fields that can contain multiple entries ({Your Wrestler's Moves},{Opponent's Moves},{Your Wrestler's Scorebook Data},{Opponent's Scorebook Data}) are delimited by ':'

    Keep the default repr() method for debugging.
    """
    # Times are in seconds
    start_time: int
    end_time: int

    attack_defend: bool  # True == attacking and False == defending
    tie_up: str
    team_moves: list[str]
    op_moves: list[str]
    team_scores: list[str]
    op_scores: list[str]

    # Factory methods

    @classmethod
    def MakeEmptyChap(cls: type[ChapterSequence], start: int, end: int) -> ChapterSequence:
        """Create a filler chapter to pad time between sequences.

        FFmpeg can corrupt timing data if there are gaps in the timeline,
        so these empty chapters are used to fill them.

        Args:
            start: The start time of the empty chapter in seconds.
            end: The end time of the empty chapter in seconds.

        Returns:
            A new ChapterSequence object representing an empty (filler) chapter.
        """
        logging.debug(
            f"Creating empty chapter with start time of {start} and end time of {end}")
        return ChapterSequence(start_time=start,
                               end_time=end,
                               attack_defend=True,
                               tie_up=EMPTY_SENTINEL,
                               team_moves=[],
                               op_moves=[],
                               team_scores=[],
                               op_scores=[])

    @classmethod
    def FromCSVRow(cls: type[ChapterSequence], csv_str: str) -> ChapterSequence:
        """Create a ChapterSequence object from a CSV row string.

        Args:
            csv_str: A CSV formatted string representing a single chapter.

        Returns:
            A ChapterSequence object parsed from the CSV string.
        """
        logging.debug(f"Chapter from csv row: {csv_str}")
        try:
            args: list[str] = csv_str.replace("\"", "").split(",")
            return ChapterSequence(start_time=int(float(args[1])),
                                   end_time=int(float(args[2])),
                                   attack_defend=args[3] == "A",
                                   tie_up=args[4],
                                   team_moves=args[5].split(":") if args[5] else [],
                                   op_moves=args[6].split(":") if args[6] else [],
                                   team_scores=args[7].split(":") if args[7] else [],
                                   op_scores=args[8].split(":") if args[8] else [])
        except (IndexError, ValueError) as e:
            logging.warning(f"Could not parse CSV row: {csv_str!r} — {e}")
            return ChapterSequence.MakeEmptyChap(0, 0)

    # Representation methods

    def MakeTitle(self) -> str:
        """Create the chapter title string as formatted for ffmpeg metadata.

        Returns:
            A string representing the chapter's title, with colon-delimited fields.
        """
        team_moves_str: str = ":".join(self.team_moves)
        op_moves_str: str = ":".join(self.op_moves)
        team_scores_str: str = ":".join(self.team_scores)
        op_scores_str: str = ":".join(self.op_scores)
        return (
            f"{'A' if self.attack_defend else 'D'},"
            f"{self.tie_up},"
            f"{team_moves_str},"
            f"{op_moves_str},"
            f"{team_scores_str},"
            f"{op_scores_str}"
        )

    def PrettyChapter(self) -> str:
        """Return a human-readable, formatted string representation of the chapter.

        Returns:
            A multi-line string with details of the chapter sequence.
        """
        logging.debug(f"Creating user facing representation of: {self}")
        team_moves_str: str = ", ".join(self.team_moves)
        op_moves_str: str = ", ".join(self.op_moves)
        team_scores_str: str = ", ".join(self.team_scores)
        op_scores_str: str = ", ".join(self.op_scores)
        return (
            "Sequence Info\n"
            f"Start Time\t\t\t: {self.start_time // 60}:{self.start_time % 60:02}\n"
            f"End Time\t\t\t: {self.end_time // 60}:{self.end_time % 60:02}\n"
            f"Was your wrestler attacking?\t: {self.attack_defend}\n"
            f"Starting position or tie\t\t: {self.tie_up}\n"
            f"Your wrestler's attacks\t\t: {team_moves_str}\n"
            f"The opponent's attacks\t\t: {op_moves_str}\n"
            f"Your wrestler's scoring\t\t: {team_scores_str}\n"
            f"The opponent's scoring\t\t: {op_scores_str}")

    def ToMetadata(self) -> str:
        """Generate the ffmpeg metadata block for this chapter.

        Returns:
            A string formatted as an ffmpeg chapter metadata block.
        """
        logging.debug(f"Creating chapter metadata from: {self}")
        return ("[CHAPTER]\n"
                "TIMEBASE=1/1\n"
                f"START={self.start_time}\n"
                f"END={self.end_time}\n"
                f"title={self.MakeTitle()}\n")


def GetVidDuration(path_to_vid: Path) -> int:
    """Return the video duration in seconds, rounded down.

    Args:
        path_to_vid: The path to the video file.

    Returns:
        The duration of the video in seconds. Returns 0 on failure.
    """
    ffprobe_cmd: str = f"ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 {shlex.quote(str(path_to_vid))}"
    logging.debug(f"Executing ffprobe command for duration: {ffprobe_cmd}")
    try:
        duration_str: str = subprocess.run(ffprobe_cmd,
                                           shell=True,
                                           capture_output=True,
                                           text=True,
                                           check=True
                                           ).stdout.strip()
        duration: int = int(float(duration_str))
        logging.info(f"Video duration for '{path_to_vid}': {duration} seconds")
        return duration
    except (subprocess.CalledProcessError, ValueError) as e:
        logging.error(f"Failed to get duration for '{path_to_vid}': {e}")
        return 0


def NameProbe(path_to_vid: Path) -> str:
    """Return the title field of the video file.

    With the formatting enforced by the film tagging process, this title
    will be the name of the wrestler.

    Args:
        path_to_vid: The path to the video file.

    Returns:
        The title string embedded in the video file, or empty string on failure.
    """
    ffprobe_cmd: str = f"ffprobe -v error -select_streams v:0 -show_entries format_tags=title -of default=nw=1:nk=1 {shlex.quote(str(path_to_vid))}"
    logging.debug(f"Executing ffprobe command for name probe: {ffprobe_cmd}")
    try:
        name: str = subprocess.run(ffprobe_cmd,
                                   shell=True,
                                   capture_output=True,
                                   text=True,
                                   check=True
                                   ).stdout.strip()
        logging.info(f"Probed name for '{path_to_vid}': {name}")
        return name
    except subprocess.CalledProcessError as e:
        logging.warning(f"NameProbe failed for '{path_to_vid}': {e}")
        return ""


def GetVideoCodecs(path_to_vid: Path) -> dict[str, str]:
    """Probe video and audio codec names from a video file.

    Args:
        path_to_vid: Path to the video file.

    Returns:
        A dict with keys 'video' and 'audio' mapping to codec names
        (e.g. {'video': 'h264', 'audio': 'aac'}). Returns empty dict on failure.
    """
    result: dict[str, str] = {}
    try:
        vcodec_cmd: str = (
            "ffprobe -v error -select_streams v:0 -show_entries "
            "stream=codec_name -of default=nw=1:nk=1 "
            f"{shlex.quote(str(path_to_vid))}"
        )
        vcodec: str = subprocess.run(
            vcodec_cmd, shell=True, capture_output=True, text=True, check=True
        ).stdout.strip()
        if vcodec:
            result['video'] = vcodec

        acodec_cmd: str = (
            "ffprobe -v error -select_streams a:0 -show_entries "
            "stream=codec_name -of default=nw=1:nk=1 "
            f"{shlex.quote(str(path_to_vid))}"
        )
        acodec: str = subprocess.run(
            acodec_cmd, shell=True, capture_output=True, text=True, check=True
        ).stdout.strip()
        if acodec:
            result['audio'] = acodec
    except (subprocess.CalledProcessError, OSError) as e:
        logging.warning(f"GetVideoCodecs failed for '{path_to_vid}': {e}")

    return result


def LoadAllWrestlerData(data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load all wrestler CSV files into a dict of DataFrames.

    Skips UNKNOWN.csv. Returns {wrestler_name: df} sorted alphabetically.

    Args:
        data_dir: Directory containing the CSV files. Defaults to csv_dir.

    Returns:
        A dict mapping wrestler names to their DataFrames.
    """
    if data_dir is None:
        data_dir = csv_dir
    result: dict[str, pd.DataFrame] = {}
    for csv_path in sorted(data_dir.glob("*.csv")):
        if csv_path.name == "UNKNOWN.csv":
            continue
        name: str = csv_path.stem
        try:
            result[name] = MakeFormattedDataFrame(csv_path)
        except Exception as e:
            logging.warning(f"Could not load data for '{name}': {e}")
    return result


def MakeNameAndCSV(path_to_vid: Path) -> tuple[str, str]:
    """Extract wrestler name and chapter data into a CSV string.

    This function uses ffprobe to get the video's title (wrestler name) and
    all chapter metadata, formatting the chapter data into a custom CSV string.

    Args:
        path_to_vid: The path to the video file to process.

    Returns:
        A tuple containing:
        - The wrestler's name (str).
        - A multi-line CSV string of the chapter data, suitable for DataFrame loading.
    """
    # This command outputs the title field of the metadata as the last line
    ffprobe_command: str = ("ffprobe -v error -show_chapters -show_entries format_tags=title "
                            f"-of csv {shlex.quote(str(path_to_vid))}")
    logging.debug(
        f"Executing ffprobe command for MakeNameAndCSV: {ffprobe_command}")

    try:
        lines: list[str] = subprocess.run(
            ffprobe_command,
            shell=True,
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip().splitlines()
    except subprocess.CalledProcessError as e:
        logging.error(f"MakeNameAndCSV ffprobe failed for '{path_to_vid}': {e}")
        return ("", "")

    if not lines:
        logging.warning(f"MakeNameAndCSV: no output from ffprobe for '{path_to_vid}'")
        return ("", "")

    name_out: str = lines.pop().removeprefix("format,")
    logging.debug(f"Extracted wrestler name: {name_out}")

    csv_rows: list[str] = []
    for line in lines:
        if EMPTY_SENTINEL in line:  # Filter out empty Chapters
            logging.debug(f"Skipping empty chapter line: {line}")
            continue

        # The line format is: chapter,id,time_base,start,start_time,end,end_time,"title"
        try:
            line = line.removeprefix("chapter,")
            parts: list[str] = line.split('"')
            time_meta: list[str] = parts[0].split(',')
            title: str = parts[1]

            chap_id: str = time_meta[0]
            start_time: int = int(float(time_meta[3]))
            end_time: int = int(float(time_meta[5]))

            csv_rows.append(
                f"{path_to_vid.name}:{chap_id},{start_time},{end_time},{title}")
        except IndexError:
            logging.warning(
                f"Could not parse the following line properly: {line}")
            continue

    logging.info(
        f"Generated CSV data for '{path_to_vid.name}' with {len(csv_rows)} rows.")
    return (name_out, "\n".join(csv_rows))


def MakeFormattedDataFrame(csv_path: Path) -> pd.DataFrame:
    """Initialize a pandas DataFrame from a wrestler's CSV file.

    Reads a CSV file, converts colon-delimited columns into lists, and sets
    appropriate data types. Compiled CSVs may carry stored Net Points and
    Adjusted Net Points columns; both are recomputed from the active ruleset
    on load regardless of what is stored.

    Args:
        csv_path: The path to the CSV file to load.

    Returns:
        A pandas DataFrame with the parsed and structured data.
    """
    logging.info(f"Loading CSV from '{csv_path}' into DataFrame.")
    column_names: list[str] = [
        COL_ORIGIN,
        COL_START_TIME,
        COL_END_TIME,
        COL_ATTACKING,
        COL_TIE_UP,
        COL_TEAM_MOVES,
        COL_OPPONENT_MOVES,
        COL_TEAM_SCORES,
        COL_OPPONENT_SCORES
    ]
    first_lines: list[str] = [
        line for line in csv_path.read_text().splitlines() if line.strip()
    ]
    n_cols: int = first_lines[0].count(",") + 1 if first_lines else 9
    if n_cols >= 11:
        column_names = column_names + [COL_NET_POINTS, COL_ADJUSTED_NET_POINTS]
    if n_cols >= 12:
        column_names = column_names + [COL_MATCH_RESULT]
    df: pd.DataFrame = pd.read_csv(
        csv_path,
        header=None,
        names=column_names,
        index_col=COL_ORIGIN,
        dtype={
            COL_ORIGIN: str,
            COL_START_TIME: "int16",
            COL_END_TIME: "int16",
            COL_ATTACKING: str,
            COL_TIE_UP: str,
            COL_TEAM_MOVES: str,
            COL_OPPONENT_MOVES: str,
            COL_TEAM_SCORES: str,
            COL_OPPONENT_SCORES: str,
        },
    )
    list_columns: list[str] = [COL_TEAM_MOVES, COL_OPPONENT_MOVES,
                               COL_TEAM_SCORES, COL_OPPONENT_SCORES]
    for col in list_columns:
        df[col] = df[col].apply(
            lambda value: str(value).split(":") if pd.notna(value) and value != "" else [])
    df[COL_ATTACKING] = df[COL_ATTACKING] == "A"
    if COL_MATCH_RESULT in df.columns:
        df[COL_MATCH_RESULT] = df[COL_MATCH_RESULT].fillna("").astype(str)
    logging.info(
        f"Successfully loaded DataFrame from '{csv_path}' with shape: {df.shape}")
    ReloadScoringMap()
    TabulateNetPoints(df)
    TabulateAdjustedNetPoints(df)
    return df


def BuildTagTitle(
    wrestler: str,
    opponent: str | None = None,
    match_result: str = "",
) -> str:
    """Build the format ``title`` tag stored on a tagged video.

    Single mode (no opponent): the wrestler's name, with `` (W)`` or `` (L)``
    appended when the match result is known. Dual mode: both names joined by
    ``TITLE_DELIMITER`` with `` (W)`` on the winner's name (draws carry no
    marker). ``match_result`` describes the tagged wrestler's result, so a loss
    in dual mode marks the opponent as the winner.

    Args:
        wrestler: The tagged wrestler's name.
        opponent: The opponent's name for dual-wrestler mode, else None.
        match_result: The tagged wrestler's result: "", "W", or "L".

    Returns:
        The title string to store as the video's format title tag.

    Raises:
        ValueError: If ``match_result`` is not one of "", "W", or "L".
    """
    if match_result not in ("", "W", "L"):
        raise ValueError(f"Invalid match result: {match_result!r}")
    wrestler_clean: str = wrestler.strip()
    opponent_clean: str = opponent.strip() if opponent else ""
    if opponent_clean:
        if match_result == "W":
            return f"{wrestler_clean}{WIN_MARKER}{TITLE_DELIMITER}{opponent_clean}"
        if match_result == "L":
            return f"{wrestler_clean}{TITLE_DELIMITER}{opponent_clean}{WIN_MARKER}"
        return f"{wrestler_clean}{TITLE_DELIMITER}{opponent_clean}"
    if match_result == "W":
        return f"{wrestler_clean}{WIN_MARKER}"
    if match_result == "L":
        return f"{wrestler_clean}{LOSS_MARKER}"
    return wrestler_clean


def ParseTaggedName(title: str) -> tuple[str, str | None, str]:
    """Parse a tagged video's format title into wrestler, opponent, and result.

    Args:
        title: The format title read back from a tagged video.

    Returns:
        A ``(wrestler, opponent, result)`` tuple. ``opponent`` is None for
        single-wrestler titles; ``result`` is "", "W", or "L" describing the
        tagged wrestler's result (derived from which name carries the marker,
        so in dual mode a ``(W)`` on the opponent's name yields "L").
    """
    parts: list[str] = title.split(TITLE_DELIMITER, maxsplit=1)
    wrestler: str = parts[0]
    result: str = ""
    if wrestler.endswith(WIN_MARKER):
        result = "W"
        wrestler = wrestler[: -len(WIN_MARKER)]
    elif wrestler.endswith(LOSS_MARKER):
        result = "L"
        wrestler = wrestler[: -len(LOSS_MARKER)]
    if len(parts) == 1:
        return (wrestler.strip(), None, result)
    opponent: str = parts[1]
    if opponent.endswith(WIN_MARKER):
        opponent = opponent[: -len(WIN_MARKER)]
        if result == "":
            result = "L"
    return (wrestler.strip(), opponent.strip(), result)


def BuildTieEntry(your_tie: str, opp_tie: str | None = None) -> str:
    """Build the tie-up value stored in a sequence's chapter title.

    Dual mode pairs the wrestlers' tie-ups with a colon; single mode stores the
    bare tie-up name.

    Args:
        your_tie: The tagged wrestler's starting tie/position.
        opp_tie: The opponent's tie/position for dual mode, else None.

    Returns:
        The tie-up value (``"yours:theirs"`` in dual mode).
    """
    your_clean: str = your_tie.strip()
    if opp_tie and opp_tie.strip():
        return f"{your_clean}:{opp_tie.strip()}"
    return your_clean


def SwapPerspectiveCSV(csv_data: str) -> str:
    """Rewrite a compiled wrestler CSV from the opponent's perspective.

    Reverses the attacking flag, swaps the team/opponent move and score
    columns, flips the colon-paired tie-up, negates the stored net/adjusted
    points, and inverts the match-result column so the same rows describe the
    opponent. Rows that do not match the 12-field compiled schema pass through
    unchanged.

    Args:
        csv_data: The multi-line compiled CSV string for a wrestler.

    Returns:
        The same CSV re-expressed from the opponent's perspective.
    """
    swapped: list[str] = []
    for line in csv_data.splitlines():
        parts: list[str] = line.split(",")
        if len(parts) != 12:
            swapped.append(line)
            continue
        (origin, start, end, attack, tie, team_moves, op_moves,
         team_scores, op_scores, net, adjusted, result) = parts
        tie_pair: list[str] = tie.split(":")
        new_tie: str = ":".join(reversed(tie_pair)) if len(tie_pair) > 1 else tie
        new_net: str = str(-int(net)) if net else ""
        new_adjusted: str = str(-int(adjusted)) if adjusted else ""
        new_result: str = "L" if result == "W" else ("W" if result == "L" else result)
        swapped.append(
            ",".join([
                origin, start, end,
                "D" if attack == "A" else "A",
                new_tie, op_moves, team_moves, op_scores, team_scores,
                new_net, new_adjusted, new_result,
            ])
        )
    return "\n".join(swapped)


def CalculateNetPoints(row: pd.Series) -> np.int16:
    """Calculates the net points from a wrestler's sequence.
    This function is only intended to be used for the `.apply()` method for a `DataFrame`

    Args:
        row: The sequence, represented by a pd.Series, being scored.

    Returns:
        The net points scored (team scores minus opponent scores).
    """
    logging.debug(f"Summing the net points of the row: {row}")
    sum_val: np.int16 = np.int16(0)
    if isinstance(row[COL_TEAM_SCORES], list):
        for score in row[COL_TEAM_SCORES]:
            try:
                sum_val += score_to_pts[score]
            except KeyError:
                logging.warning(
                    f"Malformed data in 'Team Scores' encountered while summing row: {score}")

    if isinstance(row[COL_OPPONENT_SCORES], list):
        for score in row[COL_OPPONENT_SCORES]:
            try:
                sum_val -= score_to_pts[score]
            except KeyError:
                logging.warning(
                    f"Malformed data in 'Opponent Scores' encountered while summing row: {score}")

    return sum_val


def TabulateNetPoints(df_in: pd.DataFrame) -> None:
    """Adds a column with the net points from the sequence, in place.

    Args:
        df_in: The DataFrame being changed

    Returns:
        None
    """
    df_in[COL_NET_POINTS] = df_in.apply(CalculateNetPoints, axis=1)


def AdjustedNetPoints(row: pd.Series) -> np.int16:
    """Net points plus the active ruleset's pin bonus for a sequence.

    Each pin in the sequence adds ``active_pin_points`` when the wrestler
    scored it (COL_TEAM_SCORES) and subtracts it when the opponent scored it
    (COL_OPPONENT_SCORES). Pin codes come from the active ruleset's
    ``counts_as_pin`` outcomes.

    Args:
        row: The sequence, represented by a pd.Series, being scored.

    Returns:
        The adjusted net points (net points plus the pin bonus).
    """
    net: np.int16 = CalculateNetPoints(row)
    team_pinned: bool = isinstance(row[COL_TEAM_SCORES], list) and any(
        score in _ACTIVE_PIN_CODES for score in row[COL_TEAM_SCORES]
    )
    opp_pinned: bool = isinstance(row[COL_OPPONENT_SCORES], list) and any(
        score in _ACTIVE_PIN_CODES for score in row[COL_OPPONENT_SCORES]
    )
    bonus: int = (1 if team_pinned else 0) - (1 if opp_pinned else 0)
    return np.int16(net + active_pin_points * bonus)


def TabulateAdjustedNetPoints(df_in: pd.DataFrame) -> None:
    """Adds a column with the adjusted net points from the sequence, in place.

    Args:
        df_in: The DataFrame being changed.

    Returns:
        None
    """
    df_in[COL_ADJUSTED_NET_POINTS] = df_in.apply(AdjustedNetPoints, axis=1)


def _MoveFilter(move: str, column: str) -> Callable[[pd.Series], pd.Series | None]:
    """Return a function that filters rows where `move` is present in `column`.

    Args:
        move: The move to search for.
        column: The DataFrame column to search in.

    Returns:
        A function suitable for use with `DataFrame.apply(axis=1)`.
    """
    return lambda row: row if move in row[column] else None


def DidMove(move: str) -> Callable[[pd.Series], pd.Series | None]:
    """Convenience wrapper around _MoveFilter for team moves."""
    return _MoveFilter(move, COL_TEAM_MOVES)


def DefendedMove(move: str) -> Callable[[pd.Series], pd.Series | None]:
    """Convenience wrapper around _MoveFilter for opponent moves."""
    return _MoveFilter(move, COL_OPPONENT_MOVES)


def MoveCounts(df_in: pd.DataFrame, column: str) -> dict[str, int]:
    """Returns a dictionary from move names to how often they appear in `column`.
    Unused moves are not included as keys.

    Args:
        df_in: The DataFrame the moves are being counted from.
        column: The column to count from (e.g. COL_TEAM_MOVES or COL_OPPONENT_MOVES).

    Returns:
        The dictionary of moves and frequencies.
    """
    dict_out: dict[str, int] = {}
    for move_list in df_in[column]:
        for move in move_list:
            dict_out.setdefault(move, 0)
            dict_out[move] += 1
    return dict_out


def MoveUsageCounts(df_in: pd.DataFrame) -> dict[str, int]:
    """Convenience wrapper around MoveCounts for team moves."""
    return MoveCounts(df_in, COL_TEAM_MOVES)


def MoveDefenseCounts(df_in: pd.DataFrame) -> dict[str, int]:
    """Convenience wrapper around MoveCounts for opponent moves."""
    return MoveCounts(df_in, COL_OPPONENT_MOVES)


def PinCount(df_in: pd.DataFrame, column: str) -> int:
    """Count the number of sequences containing a pin in ``column``.

    A sequence counts at most once, and only when it contains a score code
    from the active ruleset's ``counts_as_pin`` outcomes (e.g. "PIN" in
    Folkstyle). This matches the "Number of Pins" column in the move
    DataFrames, which is per-sequence (a pin ends the match).

    Args:
        df_in: The DataFrame to search.
        column: The column to search (e.g. COL_TEAM_SCORES or COL_OPPONENT_SCORES).

    Returns:
        The number of sequences that contain a pin.
    """
    pins_count: int = 0
    for sequence in df_in[column]:
        if isinstance(sequence, list):
            for score in sequence:
                if score in _ACTIVE_PIN_CODES:
                    pins_count += 1
                    break
    return pins_count


def _MoveAttribution(
    df_in: pd.DataFrame, move_column: str
) -> dict[str, dict[str, float | int]]:
    """Accumulate per-move occurrence counts and attributed net points.

    Each sequence's net and adjusted net points are distributed across the
    moves it lists: a move appearing k times in a sequence with n non-"nothing"
    moves receives k / n of that sequence's points. The "nothing" sentinel
    move is excluded entirely, so it never appears in move-level tables.

    Args:
        df_in: The input DataFrame.
        move_column: The column containing moves to analyse.

    Returns:
        A dict mapping each move to {"count", "net", "adjusted"}.
    """
    out: dict[str, dict[str, float | int]] = {}
    for _, row in df_in.iterrows():
        moves: list[str] = [move for move in row[move_column] if move != "nothing"]
        if not moves:
            continue
        weight: float = 1.0 / len(moves)
        net: float = float(row[COL_NET_POINTS])
        adjusted: float = float(row[COL_ADJUSTED_NET_POINTS])
        for move in moves:
            entry: dict[str, float | int] = out.setdefault(
                move, {"count": 0, "net": 0.0, "adjusted": 0.0}
            )
            entry["count"] = int(entry["count"]) + 1
            entry["net"] = float(entry["net"]) + net * weight
            entry["adjusted"] = float(entry["adjusted"]) + adjusted * weight
    return out


def _GenerateMoveDF(df_in: pd.DataFrame, move_column: str) -> pd.DataFrame:
    """Shared implementation for GenerateOffenseDF and GenerateDefenseDF.

    Points are attributed per move occurrence (see _MoveAttribution), so the
    net/adjusted sums and their averages share a common occurrence
    denominator.

    Args:
        df_in: The input DataFrame.
        move_column: The column containing moves to analyse.

    Returns:
        A DataFrame with move-level statistics.
    """
    header: list[str] = [
        "Count (occ.)",
        "Net Points",
        "Adjusted Net Pts",
        "Average Net Points",
        "Average Adjusted Net Points",
        "Number of Pins",
        "Times Pinned"
    ]
    attribution: dict[str, dict[str, float | int]] = _MoveAttribution(df_in, move_column)
    move_list: list[tuple[str, dict[str, float | int]]] = sorted(
        attribution.items(), key=lambda item: int(item[1]["count"]), reverse=True
    )
    df_dict: dict[str, list[int | float]] = {}

    for (move, agg) in move_list:
        move_frame: pd.DataFrame = df_in.apply(
            _MoveFilter(move, move_column), axis=1, result_type='broadcast'
        ).query(f"`{COL_START_TIME}` >= 0")
        count: int = int(agg["count"])
        net_pt_sum: float = float(agg["net"])
        adjusted_sum: float = float(agg["adjusted"])
        df_dict[move] = [
            count,
            net_pt_sum,
            adjusted_sum,
            net_pt_sum / count,
            adjusted_sum / count,
            PinCount(move_frame, COL_TEAM_SCORES),
            PinCount(move_frame, COL_OPPONENT_SCORES),
        ]
    df_out: pd.DataFrame = pd.DataFrame.from_dict(df_dict, orient='index', columns=header)
    df_out.index.name = "Move"
    return df_out


def GenerateOffenseDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a new DataFrame with offensive move stats compiled from the input.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with offensive move stats
    """
    return _GenerateMoveDF(df_in, COL_TEAM_MOVES)


def GenerateDefenseDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a new DataFrame with defensive move stats compiled from the input.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with defensive move stats
    """
    return _GenerateMoveDF(df_in, COL_OPPONENT_MOVES)


def _InitiationStats(df_in: pd.DataFrame) -> dict[str, float | int]:
    """Compute the initiation metrics for a subset of sequence rows.

    Args:
        df_in: The DataFrame (or subset) that stats are being extracted from

    Returns:
        A dict mapping each initiation metric name to its value
    """
    attack_df: pd.DataFrame = df_in[df_in[COL_ATTACKING]]
    defend_df: pd.DataFrame = df_in[~df_in[COL_ATTACKING]]
    attack_count: int = len(attack_df)
    total_count: int = len(df_in)
    attack_ratio: float = attack_count / total_count if total_count > 0 else 0.0
    net_attack: int = int(attack_df[COL_ADJUSTED_NET_POINTS].sum()) if attack_count > 0 else 0
    net_defend: int = int(defend_df[COL_ADJUSTED_NET_POINTS].sum()) if len(defend_df) > 0 else 0
    avg_attack: float = float(attack_df[COL_ADJUSTED_NET_POINTS].mean()) if attack_count > 0 else 0.0
    avg_defend: float = float(defend_df[COL_ADJUSTED_NET_POINTS].mean()) if len(defend_df) > 0 else 0.0
    raw_net_attack: int = int(attack_df[COL_NET_POINTS].sum()) if attack_count > 0 else 0
    raw_net_defend: int = int(defend_df[COL_NET_POINTS].sum()) if len(defend_df) > 0 else 0
    raw_avg_attack: float = float(attack_df[COL_NET_POINTS].mean()) if attack_count > 0 else 0.0
    raw_avg_defend: float = float(defend_df[COL_NET_POINTS].mean()) if len(defend_df) > 0 else 0.0
    return {
        "Sequences": total_count,
        "Attack Count": attack_count,
        "Defense Count": len(defend_df),
        "Attacks / Sequences": attack_ratio,
        "Net Points Attacking": raw_net_attack,
        "Net Points Defending": raw_net_defend,
        "Average Net Points Attacking": raw_avg_attack,
        "Average Net Points Defending": raw_avg_defend,
        "Adjusted Net Points Attacking": net_attack,
        "Adjusted Net Points Defending": net_defend,
        "Average Adjusted Net Points Attacking": avg_attack,
        "Average Adjusted Net Points Defending": avg_defend,
    }


def GenerateInitiationDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a DataFrame with stats based on initiation, ie. who starts an attack.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with the relevant stats
    """
    stats: dict[str, float | int] = _InitiationStats(df_in)
    df_out: pd.DataFrame = pd.DataFrame.from_dict(
        {name: [value] for name, value in stats.items()}, orient="columns"
    )
    df_out.index.name = "Metric"
    return df_out


def GenerateInitiationDFBySegment(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates an initiation DataFrame split by match result segment.

    Rows are bucketed by COL_MATCH_RESULT ("W" / "L"); rows without a result
    ("" or a legacy CSV without the column) contribute only to the All and
    Unrecorded rows.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        A 4-row DataFrame indexed by ["All", "Wins", "Losses", "Unrecorded"]
        with the same columns as GenerateInitiationDF
    """
    if COL_MATCH_RESULT in df_in.columns:
        result_col: pd.Series = df_in[COL_MATCH_RESULT]
        wins_df: pd.DataFrame = df_in[result_col == "W"]
        losses_df: pd.DataFrame = df_in[result_col == "L"]
        unrecorded_df: pd.DataFrame = df_in[~result_col.isin(["W", "L"])]
    else:
        wins_df = df_in.iloc[0:0]
        losses_df = df_in.iloc[0:0]
        unrecorded_df = df_in
    segments: dict[str, pd.DataFrame] = {
        "All": df_in,
        "Wins": wins_df,
        "Losses": losses_df,
        "Unrecorded": unrecorded_df,
    }
    df_out: pd.DataFrame = pd.DataFrame.from_dict(
        {segment: _InitiationStats(subset) for segment, subset in segments.items()},
        orient="index",
    )
    df_out.index.name = "Segment"
    return df_out

def GenerateMoveMatrix(
    df_in: pd.DataFrame,
    move_column: str,
    min_occurrences: int = 3,
    group_key: list[Any] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build a move-count matrix from a DataFrame of sequence rows.

    Each row of the returned matrix mirrors ``df_in``'s index (the Origin
    string, e.g. "video.mkv:3"); columns are the kept moves and entries are
    int16 counts of how many times that move appears in that row's move list
    (duplicates within one sequence count). The literal move "nothing" is
    dropped before counting, and moves appearing in fewer than
    ``min_occurrences`` distinct rows are dropped (the rare-move PCA
    mitigation). When ``group_key`` is given, filtering counts distinct groups
    instead of distinct rows — the "matches" PCA layout passes (wrestler,
    video) keys so a move must appear in enough matches to survive.

    Args:
        df_in: The sequence DataFrame; ``df_in[move_column]`` holds lists of
            move names (from MakeFormattedDataFrame).
        move_column: The column holding the move lists (COL_TEAM_MOVES or
            COL_OPPONENT_MOVES).
        min_occurrences: Minimum number of distinct rows (or groups) a move
            must appear in to be kept as a matrix column.
        group_key: Optional group labels aligned by row position (index i of
            ``df_in`` maps to group_key[i]); when provided the rare-move filter
            counts distinct group values per move instead of distinct rows.

    Returns:
        A (matrix, kept_moves) tuple: the int16 move-count matrix (index = the
        Origin index, columns = sorted kept moves) and the sorted kept-move list.
    """
    # Group by unique row position (not index label) so duplicate Origin
    # strings — e.g. the same "video.mkv:chap" appearing in a wrestler's CSV
    # and the opponent's perspective-swapped CSV in dual mode — do not merge.
    unique_index: pd.RangeIndex = pd.RangeIndex(len(df_in))
    tmp: pd.DataFrame = df_in.reset_index(drop=True)
    exploded: pd.Series = tmp[move_column].explode().dropna()
    exploded = exploded[exploded != "nothing"]
    presence: pd.DataFrame = (
        pd.get_dummies(exploded).groupby(level=0).max().reindex(index=unique_index, fill_value=0)
    )
    if group_key is None:
        counts: pd.Series = presence.sum(axis=0)
    else:
        grouped_index: pd.Index = pd.Index(group_key)[presence.index.to_numpy()]
        counts = presence.groupby(grouped_index).max().sum(axis=0)
    kept_moves: list[str] = sorted(
        move for move, count in counts.items() if count >= min_occurrences
    )
    matrix: pd.DataFrame = (
        pd.get_dummies(exploded)
        .groupby(level=0)
        .sum()
        .reindex(index=unique_index, fill_value=0)
        .reindex(columns=kept_moves, fill_value=0)
        .astype(np.int16)
    )
    matrix = matrix.set_axis(df_in.index)
    return (matrix, kept_moves)


@overload
def FirstPrincipalComponent(
    matrix: pd.DataFrame,
    standardize: bool,
    row_normalize: bool = ...,
    return_loadings: Literal[False] = ...,
) -> tuple[np.ndarray, float]: ...


@overload
def FirstPrincipalComponent(
    matrix: pd.DataFrame,
    standardize: bool,
    row_normalize: bool = ...,
    return_loadings: Literal[True] = ...,
) -> tuple[np.ndarray, float, np.ndarray]: ...


def FirstPrincipalComponent(
    matrix: pd.DataFrame,
    standardize: bool,
    row_normalize: bool = False,
    return_loadings: bool = False,
) -> tuple[np.ndarray, float] | tuple[np.ndarray, float, np.ndarray]:
    """Compute the first principal component scores and explained variance.

    Columns are mean-centered before the SVD. When ``standardize`` is True each
    column is additionally divided by its standard deviation (zero-variance
    columns are left as-is by dividing by 1.0). Standardization is only used for
    the "matches" layout: the "sequences" layout runs covariance PCA (no
    z-scoring) because column z-scoring on sparse near-binary rows over-amplifies
    rare moves. When ``row_normalize`` is True each row is divided by its own
    sum (zero-sum rows are left as-is) so rows are compared on move mix rather
    than tagging volume. The component's sign is fixed so the largest-magnitude
    loading is positive, making scores reproducible across library versions.

    Args:
        matrix: The move-count matrix (rows = sequences or matches).
        standardize: Whether to z-score columns before the SVD.
        row_normalize: Whether to divide each row by its sum before centering.
        return_loadings: Whether to also return the component's loadings
            (the first right singular vector).

    Returns:
        A (scores, explained_variance_ratio) tuple: scores are the projections
        onto the first principal component (U[:, 0] * s[0], float64) and the
        ratio is s[0]**2 / sum(s**2). If ``return_loadings`` is True the
        loadings vector is returned as a third element. If the total variance is
        zero, scores are all zeros and the ratio is 0.0.
    """
    x: np.ndarray = matrix.to_numpy(dtype=float)
    if x.shape[0] == 0 or x.shape[1] == 0:
        zeros: np.ndarray = np.zeros(x.shape[0], dtype=np.float64)
        if return_loadings:
            return (zeros, 0.0, np.zeros(x.shape[1], dtype=np.float64))
        return (zeros, 0.0)
    if row_normalize:
        row_sums: np.ndarray = x.sum(axis=1)
        row_sums[row_sums == 0.0] = 1.0
        x = x / row_sums[:, None]
    x = x - x.mean(axis=0)
    if standardize:
        col_std: np.ndarray = x.std(axis=0)
        col_std[col_std == 0.0] = 1.0
        x = x / col_std
    u: np.ndarray
    s: np.ndarray
    vt: np.ndarray
    u, s, vt = np.linalg.svd(x, full_matrices=False)
    total_variance: float = float(np.sum(s**2))
    if total_variance == 0.0:
        zeros = np.zeros(x.shape[0], dtype=np.float64)
        if return_loadings:
            return (zeros, 0.0, np.zeros(x.shape[1], dtype=np.float64))
        return (zeros, 0.0)
    scores: np.ndarray = u[:, 0] * s[0]
    loadings: np.ndarray = vt[0]
    if loadings[np.argmax(np.abs(loadings))] < 0:
        scores = -scores
        loadings = -loadings
    ratio: float = float(s[0] ** 2 / total_variance)
    if return_loadings:
        return (scores, ratio, loadings)
    return (scores, ratio)

