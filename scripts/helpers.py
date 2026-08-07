from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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

# ---- Scoring map ----

score_to_pts: dict[str, np.int16] = {
    "None": np.int16(0),

    "T": np.int16(3),
    "E": np.int16(1),
    "R": np.int16(2),
    "N2": np.int16(2),
    "N3": np.int16(3),
    "N4": np.int16(4),
    "N5": np.int16(5),
    # Pins are tabulated separately so they are not factored into net points
    "PIN": np.int16(0),

    "P1": np.int16(1),
    "P2": np.int16(2),

    "S": np.int16(0)
}

# Create directories that are not tracked by git
csv_dir.mkdir(parents=True, exist_ok=True)
eval_dir.mkdir(parents=True, exist_ok=True)
tmp_dir.mkdir(parents=True, exist_ok=True)
logging.info(f"Ensured directories {csv_dir}, {eval_dir}, {tmp_dir} exist.")


def LoadConfigItems(config_path: Path) -> list[str]:
    """Load non-blank, non-comment lines from a config file.

    Strips inline comments (text after '#') and leading/trailing whitespace.
    Shared by the Qt selectors (scripts/qt_app/widgets.py) and the web app
    (scripts/web/configs.py).

    Args:
        config_path: Path to the config file.

    Returns:
        A list of parsed item strings.
    """
    try:
        items: list[str] = []
        with open(config_path) as f:
            for raw in f:
                stripped: str = raw.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                items.append(stripped.split("#")[0].strip())
        return items
    except FileNotFoundError:
        logging.error(f"Config file not found: {config_path}")
        return []
    except Exception as e:
        logging.error(f"Error loading config {config_path}: {e}")
        return []


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

        Ffmpeg can corrupt timing data if there are gaps in the timeline,
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
                                   team_moves=args[5].split(":"),
                                   op_moves=args[6].split(":"),
                                   team_scores=args[7].split(":"),
                                   op_scores=args[8].split(":"))
        except (IndexError, ValueError) as e:
            logging.warning(f"Could not parse CSV row: {csv_str!r} — {e}")
            return ChapterSequence.MakeEmptyChap(0, 0)

    # Representation methods

    def MakeTitle(self) -> str:
        """Create the chapter title string as formatted for ffmpeg metadata.

        Returns:
            A string representing the chapter's title, with colon-delimited
            fields escaped for ffmpeg.
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
    ffprobe_cmd: str = f"ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 '{path_to_vid}'"
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
    ffprobe_cmd: str = f"ffprobe -v error -select_streams v:0 -show_entries format_tags=title -of default=nw=1:nk=1 '{path_to_vid}'"
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
            f"'{path_to_vid}'"
        )
        vcodec: str = subprocess.run(
            vcodec_cmd, shell=True, capture_output=True, text=True, check=True
        ).stdout.strip()
        if vcodec:
            result['video'] = vcodec

        acodec_cmd: str = (
            "ffprobe -v error -select_streams a:0 -show_entries "
            "stream=codec_name -of default=nw=1:nk=1 "
            f"'{path_to_vid}'"
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
                            f"-of csv '{path_to_vid}'")
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

    This function reads a CSV file, converts colon-delimited columns into lists, and sets
    appropriate data types.

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
        df[col] = df[col].str.split(':')

    # Convert the attack/defend to bool
    df[COL_ATTACKING] = df[COL_ATTACKING] == "A"
    logging.info(
        f"Successfully loaded DataFrame from '{csv_path}' with shape: {df.shape}")

    TabulateNetPoints(df)
    return df


def CalculateNetPoints(row: pd.Series) -> np.int16:
    """Calculates the net points from a wrestler's sequence.
    This function is only intended to be used for the `.apply()` method for a `DataFrame`

    Args:
        row: The sequence, represented by a pd.Series, being scored.

    Returns:
        The sum of the points scored.
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
    """Counts how many times "PIN" appears in `column` entries.

    Args:
        df_in: The DataFrame to search.
        column: The column to search (e.g. COL_TEAM_SCORES or COL_OPPONENT_SCORES).

    Returns:
        The number of pins found.
    """
    pins_count: int = 0
    for sequence in df_in[column]:
        if isinstance(sequence, list) and "PIN" in sequence:
            pins_count += 1
    return pins_count


def _GenerateMoveDF(df_in: pd.DataFrame, move_column: str) -> pd.DataFrame:
    """Shared implementation for GenerateOffenseDF and GenerateDefenseDF.

    Args:
        df_in: The input DataFrame.
        move_column: The column containing moves to analyse.

    Returns:
        A DataFrame with move-level statistics.
    """
    header: list[str] = [
        "Count",
        "Net Pts",
        "Average Net Points",
        "Number of Pins",
        "Times Pinned"
    ]
    move_list: list[tuple[str, int]] = list(MoveCounts(df_in, move_column).items())
    move_list.sort(key=lambda x: x[1], reverse=True)
    df_dict: dict[str, list[int | float]] = {}

    for (move, count) in move_list:
        move_frame: pd.DataFrame = df_in.apply(
            _MoveFilter(move, move_column), axis=1, result_type='broadcast'
        ).query(f"`{COL_START_TIME}` >= 0")
        net_pt_sum: int = int(move_frame[COL_NET_POINTS].sum())
        df_dict[move] = [
            count,
            net_pt_sum,
            net_pt_sum / count,
            PinCount(move_frame, COL_TEAM_SCORES),
            PinCount(move_frame, COL_OPPONENT_SCORES),
        ]
    return pd.DataFrame.from_dict(df_dict, orient='index', columns=header)


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


def GenerateInitiationDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a DataFrame with stats based on initiation, ie. who starts an attack.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with the relevant stats
    """
    attack_df: pd.DataFrame = df_in[df_in[COL_ATTACKING]]
    defend_df: pd.DataFrame = df_in[~df_in[COL_ATTACKING]]
    attack_count: int = len(attack_df)
    total_count: int = len(df_in)
    attack_ratio: float = attack_count / total_count if total_count > 0 else 0.0
    net_attack: int = int(attack_df[COL_NET_POINTS].sum()) if attack_count > 0 else 0
    net_defend: int = int(defend_df[COL_NET_POINTS].sum()) if len(defend_df) > 0 else 0
    avg_attack: float = float(attack_df[COL_NET_POINTS].mean()) if attack_count > 0 else 0.0
    avg_defend: float = float(defend_df[COL_NET_POINTS].mean()) if len(defend_df) > 0 else 0.0
    df_dict: dict[str, list[float | int]] = {
        "Attack Count": [attack_count],
        "Defense Count": [len(defend_df)],
        "Attacks / Sequences": [attack_ratio],
        "Net Points Attacking": [net_attack],
        "Net Points Defending": [net_defend],
        "Average Net Points Attacking": [avg_attack],
        "Average Net Points Defending": [avg_defend],
    }
    return pd.DataFrame.from_dict(df_dict, orient='columns')

def GenerateMoveMatrix() -> pd.DataFrame:
    """

    """

