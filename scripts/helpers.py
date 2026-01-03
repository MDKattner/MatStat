#! /usr/bin/env python

import subprocess
from dataclasses import dataclass
import os

@dataclass(order=True)
class ChapterSequence:
    """
    Representation of the data encoded in chapter titles.
    -----------------------------------------------------
    The titles are in the following format:
    {Attacking or Defending},{Starting Tie/Position},{Your Wrestler's Moves},{Opponent's Moves},{Your Wrestler's Scorebook Data},{Opponent's Scorebook Data}

    Keep the default repr() method for debugging
    """
    # Times are in seconds
    start_time : int
    end_time : int

    attack_defend : bool # True == attacking and False == defending
    tie_up : str
    team_moves : list[str]
    op_moves : list[str]
    team_scores : list[str]
    op_scores : list[str]

    def MakeTitle(self) -> str:
        return (f"{'A' if self.attack_defend  else 'D'},"
            f"{self.tie_up},"
            f"{'\\:'.join(self.team_moves)}," # A '\' character must me in the string to escape ':' for ffmpeg
            f"{'\\:'.join(self.op_moves)},"
            f"{'\\:'.join(self.team_scores)},"
            f"{'\\:'.join(self.op_scores)}")


    @classmethod
    def MakeEmptyChap(cls, start : int, end : int):
        """
        Use the Chapter objects created from this method to pad between sequences.
        Ffmpeg will ruin the timing data passed to it if this is not done.
        """
        return ChapterSequence(start_time=start, 
                               end_time=end, 
                               attack_defend=True, 
                               tie_up="NONE", 
                               team_moves=[],
                               op_moves=[], 
                               team_scores=[],
                               op_scores=[])

    @classmethod 
    def FromCSVRow(cls, csv_str : str):
        """
        Takes a string that results from the command 'ffprobe -v error -show_chapters -print_format csv'
        They are of the following form:
        chapter,1,1/1000,12000,12.000000,61000,61.000000,"D,outside control,double:sweep single,sprawl:spladle,R,N2:R"
        """
        args : list[str] = csv_str.replace("\"", "").split(",")
        return ChapterSequence(start_time = int(float(args[4])), 
                               end_time = int(float(args[6])),
                               attack_defend = args[7] == "A",
                               tie_up = args[8],
                               team_moves = args[9].split(":"), # No '\' used because ffmpeg removes it
                               op_moves = args[10].split(":"),
                               team_scores = args[11].split(":"),
                               op_scores = args[12].split(":"))


    def PretyChapter(self) -> str:
        return ("Sequence Info\n"
            f"Start Time\t\t\t: {self.start_time // 60:02}:{self.start_time % 60:02}\n"
            f"End Time\t\t\t: {self.end_time // 60:02}:{self.end_time % 60:02}\n"
            f"Was your wrestler attacking?\t: {self.attack_defend}\n"
            f"Starting position or tie\t: {self.tie_up}\n"
            f"Your wrestler's attacks\t\t: {self.team_moves}\n"
            f"The opponent's attacks\t\t: {self.op_moves}\n"
            f"Your wrestler's scoring\t\t: {self.team_scores}\n"
            f"The opponent's scoring\t\t: {self.op_scores}")




    def ToMetadata(self) -> str:
        return ( "[CHAPTER]\n"
            "TIMEBASE=1/1\n"
            f"START={self.start_time}\n"
            f"END={self.end_time}\n"
            f"title={self.MakeTitle()}\n")



def SelectFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --no-multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip() # fzf returns an additional newline that is striped here


def SelectMultiFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()

def GetTime(msg : str) -> int:
    """
    Gets a time from the user
    """
    [mins, secs] = input(msg).split(sep=":")
    return int(mins)*60 + int(secs)

def GetVidDuration(path : str) -> int:
    """
    Returns the video duration in seconds rounded down.
    """
    return int(float(
        subprocess.run(f"ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 '{path}'",
                       shell=True,
                       capture_output=True,
                       text=True,
                       check=True
                       ).stdout.strip()
    ))

def ClearFiller(text: str) -> str:
    """
    Removes all lines from a string that contain the substring "NONE".
    This is used to remove chapters used as fillers for the metadata.
    """
    return "\n".join([line for line in text.splitlines() if "NONE" not in line])

def NameProbe(path_to_vid : str) -> str:
    """
    Returns the title field of the video file path passed to it.
    """
    return subprocess.run(
        f"ffprobe -v error -select_streams v:0 -show_entries format_tags=title -of default=nw=1:nk=1 {path_to_vid}",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()


def FilterVideos(wrestler_name : str) -> list[str]:
    """
    Returns a list of strings of strings, that represent file names of videos, that have `wrestler_name` in the title field.
    The function searches vids/taged for these videos.
    """
    vids : list[str] = os.listdir("./vids/taged/")
    return [vid for vid in vids if NameProbe(f"./vids/taged/{vid}") == wrestler_name]
