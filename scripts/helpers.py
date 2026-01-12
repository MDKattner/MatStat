#! /usr/bin/env python

import logging
import subprocess
from dataclasses import dataclass
import os
from pathlib import Path
import pandas as pd
import numpy as np
import pyarrow as pa
import io

# Global variables
taged_dir: Path = Path("vids") / "taged"
untaged_dir: Path = Path("vids") / "untaged"
tmp_dir: Path = Path("tmp")
csv_dir: Path = Path("stats") / "wrestler_data"
eval_dir: Path = Path("stats") / "reports"
cfg_dir: Path = Path("cfg")
log_file: Path = Path("MatStat.log")

logging.basicConfig(filename=log_file, level=logging.DEBUG,
                    format="%(asctime)s:%(levelname)s:%(message)s")

# Map from scorebook encodings to point values
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
try:
    logging.info(f"Creating directories {csv_dir}")
    os.makedirs(csv_dir)
except FileExistsError as e:
    logging.info(f"{csv_dir} already exists")
try:
    logging.info(f"Creating directories {eval_dir}")
    os.mkdir(eval_dir)
except FileExistsError as e:
    logging.info(f"{eval_dir} already exists")
try:
    logging.info(f"Creating directories {tmp_dir}")
    os.mkdir(tmp_dir)
except FileExistsError as e:
    logging.info(f"{tmp_dir} already exists")


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
    def MakeEmptyChap(cls, start: int, end: int):
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
                               tie_up="EMPTY",
                               team_moves=[],
                               op_moves=[],
                               team_scores=[],
                               op_scores=[])

    @classmethod
    def FromCSVRow(cls, csv_str: str):
        """Create a ChapterSequence object from a CSV row string.

        Args:
            csv_str: A CSV formatted string representing a single chapter.

        Returns:
            A ChapterSequence object parsed from the CSV string.
        """
        logging.debug(
            f"Chapter from csv row: {csv_str}")
        args: list[str] = csv_str.replace("\"", "").split(",")
        return ChapterSequence(start_time=int(float(args[1])),
                               end_time=int(float(args[2])),
                               attack_defend=args[3] == "A",
                               tie_up=args[4],
                               team_moves=args[5].split(":"),
                               op_moves=args[6].split(":"),
                               team_scores=args[7].split(":"),
                               op_scores=args[8].split(":"))

    # Representation methods

    def MakeTitle(self) -> str:
        """Create the chapter title string as formatted for ffmpeg metadata.

        Returns:
            A string representing the chapter's title, with colon-delimited
            fields escaped for ffmpeg.
        """
        return (
            f"{'A' if self.attack_defend else 'D'},"
            f"{self.tie_up},"
            # A '\' character must me in the string to escape ':' for ffmpeg
            f"{':'.join(self.team_moves)},"
            f"{':'.join(self.op_moves)},"
            f"{':'.join(self.team_scores)},"
            f"{':'.join(self.op_scores)}"
        )

    def PretyChapter(self) -> str:
        """Return a human-readable, formatted string representation of the chapter.

        Returns:
            A multi-line string with details of the chapter sequence.
        """
        logging.debug(f"Creating user facing representation of: {self}")
        return (
            "Sequence Info\n"
            f"Start Time\t\t\t: {self.start_time // 60}:{self.start_time % 60:02}\n"
            f"End Time\t\t\t: {self.end_time // 60}:{self.end_time % 60:02}\n"
            f"Was your wrestler attacking?\t: {self.attack_defend}\n"
            f"Starting position or tie\t: {self.tie_up}\n"
            f"Your wrestler's attacks\t\t: {", ".join(self.team_moves)}\n"
            f"The opponent's attacks\t\t: {", ".join(self.op_moves)}\n"
            f"Your wrestler's scoring\t\t: {", ".join(self.team_scores)}\n"
            f"The opponent's scoring\t\t: {", ".join(self.op_scores)}")

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


def SelectFromConfig(file_name: str, desc: str) -> str:
    """Present a list from a config file to the user via fzf for single selection.

    Args:
        file_name: The name of the configuration file (e.g., "Wrestlers.config").
        desc: A description to display as the header in the fzf menu.

    Returns:
        The single item selected by the user.
    """
    fzf_cmd = (
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
        f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --no-multi --preview='cat ./cfg/{file_name}' --preview-window=80%"
    )
    logging.debug(f"Executing fzf command: {fzf_cmd}")
    result = subprocess.run(
        fzf_cmd,
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()
    logging.info(f"Selected from config '{file_name}': {result}")
    return result


def SelectMultiFromConfig(file_name: str, desc: str) -> str:
    """Present a list from a config file to the user via fzf for multiple selection.

    Args:
        file_name: The name of the configuration file (e.g., "Moves.config").
        desc: A description to display as the header in the fzf menu.

    Returns:
        A newline-separated string of items selected by the user.
    """
    fzf_cmd = (
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
        f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --multi --preview='cat ./cfg/{file_name}' --preview-window=80%"
    )
    logging.debug(f"Executing fzf command: {fzf_cmd}")
    result = subprocess.run(
        fzf_cmd,
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()
    logging.info(
        f"Selected multiple from config '{file_name}': {result.splitlines()}")
    return result


def GetTimeFromUsr(msg: str) -> int:
    """Get a time from the user, formatted as mins:secs, and handle invalid input.

    This function will repeatedly prompt the user until a valid time format
    is entered.

    Args:
        msg: The message to display to the user as a prompt.

    Returns:
        The time in seconds.
    """
    while True:
        logging.info(
            f"Asking user for a time with the following message: {msg}")
        user_input = input(msg)
        logging.debug(f"User entered for time: '{user_input}'")

        try:
            mins, secs = map(int, user_input.split(':'))
            total_seconds = mins * 60 + secs
            logging.info(
                f"Successfully parsed time: {mins}m {secs}s ({total_seconds}s).")
            return total_seconds  # exit point
        except ValueError:
            logging.warning(f"Invalid time format entered: '{user_input}'")
            print(
                f"\nInvalid format: '{user_input}'. Please use the format 'mins:secs' (e.g., '1:32').\n")


def GetVidDuration(path_to_vid: str) -> int:
    """Return the video duration in seconds, rounded down.

    Args:
        path_to_vid: The path to the video file.

    Returns:
        The duration of the video in seconds.
    """
    ffprobe_cmd = f"ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 '{path_to_vid}'"
    logging.debug(f"Executing ffprobe command for duration: {ffprobe_cmd}")
    duration_str = subprocess.run(ffprobe_cmd,
                                  shell=True,
                                  capture_output=True,
                                  text=True,
                                  check=True
                                  ).stdout.strip()
    duration = int(float(duration_str))
    logging.info(f"Video duration for '{path_to_vid}': {duration} seconds")
    return duration


def NameProbe(path_to_vid: Path) -> str:
    """Return the title field of the video file.

    With the formatting enforced by the film tagging process, this title
    will be the name of the wrestler.

    Args:
        path_to_vid: The path to the video file.

    Returns:
        The title string embedded in the video file.
    """
    ffprobe_cmd = f"ffprobe -v error -select_streams v:0 -show_entries format_tags=title -of default=nw=1:nk=1 '{path_to_vid}'"
    logging.debug(f"Executing ffprobe command for name probe: {ffprobe_cmd}")
    name = subprocess.run(ffprobe_cmd,
                          shell=True,
                          capture_output=True,
                          text=True,
                          check=True
                          ).stdout.strip()
    logging.info(f"Probed name for '{path_to_vid}': {name}")
    return name


def FilterVideos(wrestler_name: str) -> list[Path]:
    """Return a list of paths to tagged videos matching a wrestler's name.

    Searches the `vids/taged` directory for videos that have `wrestler_name`
    in their title metadata field.

    Args:
        wrestler_name: The name of the wrestler to filter by.

    Returns:
        A list of Path objects for video files matching the wrestler's name.
    """
    logging.info(f"Filtering videos for wrestler: {wrestler_name}")
    vids: list[str] = os.listdir(taged_dir)
    filtered = [
        taged_dir / vid for vid in vids if NameProbe(taged_dir / vid) == wrestler_name]
    logging.info(f"Found {len(filtered)} videos for '{wrestler_name}'")
    return filtered


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
                            f"-of default=nw=1:nk=1 -print_format csv=print_section=0 '{path_to_vid}'")
    logging.debug(
        f"Executing ffprobe command for MakeNameAndCSV: {ffprobe_command}")

    lines: list[str] = subprocess.run(
        ffprobe_command,
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip().splitlines()

    name_out: str = lines.pop()
    logging.debug(f"Extracted wrestler name: {name_out}")

    csv_rows: list[str] = []
    for line in lines:
        if "EMPTY" in line:  # Filter out empty Chapters
            logging.debug(f"Skipping empty chapter line: {line}")
            continue

        # The line format is: id,time_base,start,start_time,end,end_time,title,"the,title,string"
        try:
            parts: str = line.split('"')
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


def MakeFormatedDataFrame(csv_path: Path) -> pd.DataFrame:
    """Initialize a pandas DataFrame from a wrestler's CSV file.

    This function reads a CSV file, converts colon-delimited columns into lists, and sets
    appropriate data types.

    Args:
        csv_path: The path to the CSV file to load.

    Returns:
        A pandas DataFrame with the parsed and structured data.
    """
    logging.info(f"Loading CSV from '{csv_path}' into DataFrame.")
    column_names = [
        "Origin",
        "Start Time",
        "End Time",
        "Attacking",
        "Tie Up",
        "Team Moves",
        "Opponent Moves",
        "Team Scores",
        "Opponent Scores"
    ]
    df = pd.read_csv(
        csv_path,
        header=None,
        names=column_names,
        index_col="Origin",
        dtype={
            "Origin": str,
            "Start Time": "int16",
            "End Time": "int16",
            "Attacking": str,
            "Tie Up": str,
            "Team Moves": str,
            "Opponent Moves": str,
            "Team Scores": str,
            "Opponent Scores": str,
        },
    )

    list_columns: list[str] = ["Team Moves", "Opponent Moves",
                               "Team Scores", "Opponent Scores"]

    for col in list_columns:
        df[col] = df[col].str.split(':')

    # Convert the attack/defend to bool
    df["Attacking"] = df["Attacking"] == "A"
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
    sum: np.int16 = np.int16(0)
    if isinstance(row["Team Scores"], list):
        for score in row["Team Scores"]:
            try:
                sum += score_to_pts[score]
            except KeyError as e:
                logging.warning(
                    f"Malformed data in 'Team Scores' encountered while summing row: {score}")

    if isinstance(row["Opponent Scores"], list):
        for score in row["Opponent Scores"]:
            try:
                sum -= score_to_pts[score]
            except KeyError as e:
                logging.warning(
                    f"Malformed data in 'Opponent Scores' encountered while summing row: {score}")

    return sum


def TabulateNetPoints(df_in: pd.DataFrame) -> None:
    """Adds a column with the net points from the sequence, in place.

    Args:
        df_in: The DataFrame being changed

    Returns:
        None
    """
    df_in["Net Points"] = df_in.apply(
        CalculateNetPoints, axis=1)


def DidMove(move: str):
    """Only intended to be used with the `apply` method with `axis=1` for DataFrames.
    This function returns a function to be used in that method.

    Args:
        move: The move being searched for in the `Team Moves` field

    Returns:
        A function that takes a `pd.Series` and returns it if the move is present
    """
    return (lambda row: row if move in row["Team Moves"] else None)


def DefendedMove(move: str):
    """Only intended to be used with the `apply` method with `axis=1` for DataFrames.
    This function returns a function to be used in that method.

    Args:
        move: The move being searched for in the `Opponent Moves` field

    Returns:
        A function that takes a `pd.Series` and returns it if the move is present
    """
    return (lambda row: row if move in row["Opponent Moves"] else None)


def MoveUsageCounts(df_in: pd.DataFrame) -> dict[str, int]:
    """Returns a dictionary from move names to how often they are used by the wrestler.
    Unused moves are not included as keys.

    Args:
        df_in: The DataFrame the moves are being counted from

    Returns:
        The dictionary of moves and frequencies
    """
    dict_out: dict = dict()
    for move_list in df_in["Team Moves"]:
        for move in move_list:
            dict_out.setdefault(move, 0)
            dict_out[move] += 1
    return dict_out


def MoveDefenseCounts(df_in: pd.DataFrame) -> dict[str, int]:
    """Returns a dictionary from move names to how often they are used against the wrestler.
    Unused moves are not included as keys.

    Args:
        df_in: The DataFrame the moves are being counted from

    Returns:
        The dictionary of moves and frequencies
    """
    dict_out: dict = dict()
    for move_list in df_in["Opponent Moves"]:
        for move in move_list:
            dict_out.setdefault(move, 0)
            dict_out[move] += 1
    return dict_out


def PinCount(df_in: pd.DataFrame) -> int:
    """Counts how many pins, by the wrestler, are in a DataFrame.

    Args:
        df_in: The DataFrame that the stats are being extracted from
    Returns:
        The amount of pins in the DataFrame
    """
    pins_count: int = 0
    for sequence in df_in["Team Scores"]:
        if isinstance(sequence, list) and "PIN" in sequence:
            pins_count += 1
    return pins_count


def PinedCount(df_in: pd.DataFrame) -> int:
    """Counts how many times the wrestler was pined in a DataFrame.

    Args:
        df_in: The DataFrame that the stats are being extracted from
    Returns:
        The amount of pins in the DataFrame
    """
    pined_count: int = 0
    for sequence in df_in["Opponent Scores"]:
        if isinstance(sequence, list) and "PIN" in sequence:
            pined_count += 1
    return pined_count


def GenerateOffenseDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a new DataFrame with offensive move stats compiled from the input.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with offensive move stats
    """
    header: list[str] = [
        "Count",
        "Net Pts",
        "Average Net Points",
        "Number of Pins",
        "Times Pined"
    ]
    move_list: list[tuple[str, int]] = list(MoveUsageCounts(df_in).items())
    move_list.sort(key=lambda x: x[1], reverse=True)
    df_dict: dict = dict()

    for (move, count) in move_list:
        move_frame: pd.DataFrame = df_in.apply(
            DidMove(move), axis=1, result_type='broadcast'
        ).query("`Start Time` > 0")
        net_pt_sum: int = move_frame['Net Points'].sum()
        df_dict[move] = [
            count,
            net_pt_sum,
            net_pt_sum / count,
            PinCount(move_frame),
            PinedCount(move_frame),
        ]
    return pd.DataFrame.from_dict(df_dict, orient='index', columns=header)


def GenerateDefenseDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a new DataFrame with defensive move stats compiled from the input.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with defensive move stats
    """
    header: list[str] = [
        "Count",
        "Net Pts",
        "Average Net Points",
        "Number of Pins",
        "Times Pined"
    ]
    move_list: list[tuple[str, int]] = list(MoveDefenseCounts(df_in).items())
    move_list.sort(key=lambda x: x[1], reverse=True)
    df_dict: dict = dict()

    for (move, count) in move_list:
        move_frame: pd.DataFrame = df_in.apply(
            DefendedMove(move), axis=1, result_type='broadcast'
        ).query("`Start Time` > 0")
        net_pt_sum: int = move_frame['Net Points'].sum()
        df_dict[move] = [
            count,
            net_pt_sum,
            net_pt_sum / count,
            PinCount(move_frame),
            PinedCount(move_frame),
        ]
    return pd.DataFrame.from_dict(df_dict, orient='index', columns=header)


def GenerateInitiationDF(df_in: pd.DataFrame) -> pd.DataFrame:
    """Creates a DataFrame with stats based on initiation, ie. who starts an attack.

    Args:
        df_in: The DataFrame that stats are being extracted from

    Returns:
        The DataFrame with the relevant stats
    """
    attack_df: pd.DataFrame = df_in[df_in["Attacking"]]
    defend_df: pd.DataFrame = df_in[~df_in["Attacking"]]
    df_dict: dict[str, list[float | int]] = {
        "Attack Count": [len(attack_df)],
        "Defense Count": [len(defend_df)],
        "Attacks / Sequences": [len(attack_df) / len(df_in)],
        "Net Points Attacking": [attack_df["Net Points"].sum()],
        "Net Points Defending": [defend_df["Net Points"].sum()],
        "Average Net Points Attacking": [attack_df["Net Points"].mean()],
        "Average Net Points Defending": [defend_df["Net Points"].mean()],
    }
    return pd.DataFrame.from_dict(df_dict, orient='columns')
