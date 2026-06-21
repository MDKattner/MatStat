#! /usr/bin/env bash

# Not using set -e: fzf can return non-zero on cancel, which should not abort the script.
set -uo pipefail

if ! command -v pipenv &>/dev/null ; then
    echo "This utility depends on pipenv, please install it in the environment this script is being run on" >&2
    exit 1
fi

if ! command -v ffprobe &>/dev/null ; then
    echo "This utility depends on ffprobe, please install it in the environment this script is being run on" >&2
    exit 1
fi

if ! command -v ffmpeg &>/dev/null ; then
    echo "This utility depends on ffmpeg, please install it in the environment this script is being run on" >&2
    exit 1
fi

if ! command -v fzf &>/dev/null ; then
    echo "This utility depends on fzf, please install it." >&2
    exit 1
fi

function ScriptSelect
{
    # All scripts ending in '.py' are excluded from the selection menu.
    # The preview window shows the script's comment header (lines starting with #, but not #!).
    # pyfiglet is optional; if missing the banner is silently skipped.
    pipenv run ls ./scripts/ | grep -v '\.py$' | fzf \
        --header="Select What You Want to Do" \
        --header-border=bold --header-label-pos=top --no-multi \
        --preview='pyfiglet -w$FZF_PREVIEW_COLUMNS -fansi_regular -jcenter MatStat 2>/dev/null; grep -G ^#[^!] ./scripts/{}' \
        --preview-window=70% \
        || echo ""
}

selected=$(ScriptSelect)
while [ "$selected" ]; do
    pipenv run python "./scripts/$selected"
    read -p "Done with \`$selected\`. Press Enter to continue or Ctrl-c to exit... \
        "
    selected=$(ScriptSelect) || true
done
