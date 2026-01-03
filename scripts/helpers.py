#! /usr/bin/env python

import logging
import subprocess
from dataclasses import dataclass
import os
from pathlib import Path
import pandas as pd

# Global variables
taged_dir: Path = Path("vids") / "taged"
untaged_dir: Path = Path("vids") / "untaged"
tmp_dir: Path = Path("tmp")
csv_dir: Path = Path("stats") / "wrestler_data"
eval_dir: Path = Path("stats") / "reports"
cfg_dir: Path = Path("cfg")

logging.basicConfig()


@dataclass(order=True)
class ChapterSequence:
    """
    Representation of the data encoded in chapter titles.
    -----------------------------------------------------
    The titles are in the following format:
    {Attacking or Defending},{Starting Tie/Position},{Your Wrestler's Moves},{Opponent's Moves},{Your Wrestler's Scorebook Data},{Opponent's Scorebook Data}
    Fields that can contain multiple entries ({Your Wrestler's Moves},{Opponent's Moves},{Your Wrestler's Scorebook Data},{Opponent's Scorebook Data}) are delimited by ':'

    Keep the default repr() method for debugging
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
        """
        Use the Chapter objects created from this method to pad between sequences.
        Ffmpeg will ruin the timing data passed to it if this is not done.
        """
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
        """
        Takes a string that results from the command MakeNameAndCSV()
        Then returns a ChapterSequence
        """
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
        return (
            "Sequence Info\n"
            f"Start Time\t\t\t: {self.start_time // 60:02}:{self.start_time % 60:02}\n"
            f"End Time\t\t\t: {self.end_time // 60:02}:{self.end_time % 60:02}\n"
            f"Was your wrestler attacking?\t\t: {self.attack_defend}\n"
            f"Starting position or tie\t: {self.tie_up}\n"
            f"Your wrestler's attacks\t\t: {self.team_moves}\n"
            f"The opponent's attacks\t\t: {self.op_moves}\n"
            f"Your wrestler's scoring\t\t: {self.team_scores}\n"
            f"The opponent's scoring\t\t: {self.op_scores}")

    def ToMetadata(self) -> str:
        return ("[CHAPTER]\n"
                "TIMEBASE=1/1\n"
                f"START={self.start_time}\n"
                f"END={self.end_time}\n"
                f"title={self.MakeTitle()}\n")


def SelectFromConfig(file_name: str, desc: str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
        f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --no-multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()  # fzf returns an additional newline that is striped here


def SelectMultiFromConfig(file_name: str, desc: str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
        f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()


def GetTimeFromUsr(msg: str) -> int:
    """
    Gets a time from the user, formatted as mins:secs
    """
    [mins, secs] = input(msg).split(sep=":")
    return int(mins)*60 + int(secs)


def GetVidDuration(path_to_vid: str) -> int:
    """
    Returns the video duration in seconds rounded down.
    """
    return int(float(
        subprocess.run(f"ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 '{path_to_vid}'",
                       shell=True,
                       capture_output=True,
                       text=True,
                       check=True
                       ).stdout.strip()
    ))


def NameProbe(path_to_vid: str | Path) -> str:
    """
    Returns the title field of the video file path passed to it.
    With the formatting enforced by the film tagging process this will be the name of the wrestler.
    """
    return subprocess.run(
        f"ffprobe -v error -select_streams v:0 -show_entries format_tags=title -of default=nw=1:nk=1 '{path_to_vid}'",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()


def FilterVideos(wrestler_name: str) -> list[str]:
    """
    Returns a list of strings of strings, that represent file names of videos, that have `wrestler_name` in the title field.
    The function searches vids/taged for these videos.
    """
    vids: list[str] = os.listdir(taged_dir)
    return [vid for vid in vids if NameProbe(taged_dir / vid) == wrestler_name]


def MakeNameAndCSV(path_to_vid: Path) -> tuple[str, str]:
    """
    Uses ffprobe to convert the chapter metadata into csv data and the name of the wrestler.
    This is done as a tuple of the form (name, csv).
    """
    ffprobe_command: list[str] = [
        "ffprobe",
        "-v", "error",
        "-show_chapters",
        "-print_format", "csv=print_section=0",  # Compact CSV, one line per chapter
        # Makes `format,<wrestler>` the last line of cmd output
        "-show_entries", "format_tags=title",
        str(path_to_vid)
    ]

    lines: list[str] = subprocess.run(
        ffprobe_command,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip().splitlines()

    name_out: str = lines.pop()

    csv_rows: list[str] = []
    for line in lines:
        if "EMPTY" in line:  # Filter out empty Chapters
            continue

        # The line format is: id,time_base,start,start_time,end,end_time,title,"the,title,string"
        try:
            parts: str = line.split('"')
            time_meta: list[str] = parts[0].split(',')
            title: str = parts[1]

            # time_meta indices: 2=start, 4=end (integer timestamps).
            # The project uses a TIMEBASE of 1/1, so these are equivalent to seconds.
            chap_id: str = time_meta[0]
            start_time: str = time_meta[3]
            end_time: str = time_meta[5]

            csv_rows.append(
                f"{path_to_vid.name}:{chap_id},{start_time},{end_time},{title}")
        except IndexError:
            # Skip malformed lines from ffprobe quietly.
            continue

    return (name_out, "\n".join(csv_rows))


def MakeFormatedDataFrame(csv_path: Path) -> pd.DataFrame:
    """
    Initializes a pandas DataFrame from a CSV string where some columns
    are colon-delimited and should be converted to lists.

    This function is optimized for performance on large datasets.

    Args:
        csv_string: A multi-line string containing the CSV data.

    Returns:
        A pandas DataFrame with the parsed and structured data.
    """
    # Define the column names based on the format from makeNameAndCSV
    column_names = [
        'sequence_id',
        'start_time',
        'end_time',
        'attack_defend',
        'tie_up',
        'team_moves',
        'op_moves',
        'team_scores',
        'op_scores'
    ]

    # Read the data using pandas' highly optimized CSV reader
    df = pd.read_csv(
        csv_path,
        header=None,         # The data has no header row
        names=column_names,  # Assign the column names
        dtype={
            'sequence_id': str,
            'start_time': 'int16',
            'end_time': 'int16',
            'attack_defend': str,
            'tie_up': str,
            'team_moves': str,
            'op_moves': str,
            'team_scores': str,
            'op_scores': str,
        }
    )

    # Columns that contain colon-separated values to be converted to lists
    list_columns = ['team_moves', 'op_moves', 'team_scores', 'op_scores']

    for col in list_columns:
        df[col] = df[col].str.split(':')

    # Convert the attack/defend column to a more useful boolean type
    df['attack_defend'] = df['attack_defend'] == 'A'

    return df
