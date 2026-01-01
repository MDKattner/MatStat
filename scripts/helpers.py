#! /usr/bin/env python

import subprocess
from dataclasses import dataclass

@dataclass(order=True)
class Chapter:
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

    @classmethod
    def MakeEmptyChap(cls, start : int, end : int):
        """
        Use the Chapter objects created from this method to pad between sequences.
        Ffmpeg will ruin the timing data passed to it if this is not done.
        """
        return Chapter(start_time=start, 
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
        return Chapter(start_time = int(float(args[4])), 
                       end_time = int(float(args[6])),
                       attack_defend = args[7] == "A",
                       tie_up = args[8],
                       team_moves = args[9].split(":"), # No '\' used because ffmpeg removes it
                       op_moves = args[10].split(":"),
                       team_scores = args[11].split(":"),
                       op_scores = args[12].split(":"))




    def ToMetadata(self) -> str:
        return ( "[CHAPTER]\n"
            "TIMEBASE=1/1\n"
            f"START={self.start_time}\n"
            f"END={self.end_time}\n"
            f"title={'A' if self.attack_defend  else 'D'},"
            f"{self.tie_up},"
            f"{'\\:'.join(self.team_moves)}," # A '\' character must me in the string to escape ':' for ffmpeg
            f"{'\\:'.join(self.op_moves)},"
            f"{'\\:'.join(self.team_scores)},"
            f"{'\\:'.join(self.op_scores)}\n"
            "[/CHAPTER]\n")



def SelectFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --no-multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout


def SelectMultiFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --multi --preview='cat ./cfg/{file_name}' --preview-window=80%",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout

def GetTime(msg : str) -> int:
    [mins, secs] = input(msg).split(sep=":")
    return int(mins)*60 + int(secs)


def ClearFiller(text: str) -> str:
    """
    Removes all lines from a string that contain the substring "NONE".
    This is used to remove chapters used as fillers for the metadata.
    """
    return "\n".join([line for line in text.splitlines() if "NONE" not in line])

