#! /usr/bin/env bash

set -euo pipefail # Putting the script in 'strict mode'

if ! command -v pipenv &>/dev/null ; then
    echo "This utillity depends on pipenv, please install it in the enviornment this script is being run on" >&2
    exit 1
fi

if ! command -v ffprobe &>/dev/null ; then
    echo "This utillity depends on ffprobe, please install it in the enviornment this script is being run on" >&2
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
    pipenv run ls ./scripts/ | grep -Gv "[\_\.]py" | fzf --header="Select What You Want to Do" --header-border=bold --header-label-pos=top --no-multi --preview='pyfiglet -w$FZF_PREVIEW_COLUMNS -fansi_regular -jcenter MatStat && grep -G ^#[^!] ./scripts/{}' --preview-window=70%
}

selected=$(ScriptSelect)
while [ "$selected" ]; do
    pipenv run python "./scripts/$selected"
    read -p "Done with \`$selected\`. Press Enter to continue or Ctrl-c to exit... \
        "
    selected=$(ScriptSelect)
done
