#! /usr/bin/env python

import subprocess
import numpy as np
from dataclasses import dataclass

def SelectFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --no-multi --preview='cat ./cfg/{file_name}'",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout

 
def SelectMultiFromConfig(file_name : str, desc : str) -> str:
    return subprocess.run(
        # Double escape characters are needed to avoid throwing a SyntaxWarning
        f"sed 's/#.*//' ./cfg/{file_name} | grep -G '\\S' | sed 's/[[:space:]]\\+$//' | "
            f"fzf --header='{desc}' --header-border=bold --header-label-pos=top --multi --preview='cat ./cfg/{file_name}'",
        shell=True,
        capture_output=True,
        text=True,
        check=True
    ).stdout

def GetTime(msg : str) -> int:
    [mins, secs] = input(msg).split(sep=":")
    return int(mins)*60 + int(secs)

@dataclass(order=True)
class Chapter:
    """
    Representation of the data encoded in chapter titles.
    """
    # Times are in seconds
    start_time : int
    end_time : int

    attack_defend : bool # True == attacking and False == defending
    tie_up : str
    team_moves : [str]
    op_moves : [str]
    team_scores : [str]
    op_scores : [str]


