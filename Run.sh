#! /usr/bin/env bash


if ! command -v python &>/dev/null ; then
    echo "This utillity depends on python, please install it in the enviornment this script is being run on" >&2
    exit 1
fi

if ! command -v fzf &>/dev/null ; then
    echo "This utillity depends on fzf, please install it in the enviornment this script is being run on" >&2
    exit 1
fi

if ! command -v ffmpeg &>/dev/null ; then
    echo "This utillity depends on ffmpeg, please install it in the enviornment this script is being run on" >&2
    exit 1
fi

function ScriptSelect
{
    # All scripts that end in '.py' are excluded from the selection menu
    # The preview window only shows lines that begin with '#' excluding those that begin with '#!'
    ls ./scripts/ | grep -Gv "[\_\.]py" | fzf --header="Select What You Want to Do" --header-border=bold --header-label-pos=top --no-multi --preview="grep -G '^#[^!]' ./scripts/{}" --preview-window=80%
}

python "./scripts/$(ScriptSelect)"





