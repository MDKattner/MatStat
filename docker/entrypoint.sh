#!/bin/sh
# Seed the data root from the packaged defaults, then start MatStat.
#
# Mirrors the NixOS module's ExecStartPre: missing directories are created and
# packaged config files are copied only when absent, so restarts and upgrades
# never overwrite user config, media, reels, or reports.
set -eu

package_dir="${MATSTAT_PACKAGE_DIR:-/app/result}"
data_root="${MATSTAT_ROOT:-/data}"

install -d \
  "$data_root/cfg" \
  "$data_root/vids/untaged" \
  "$data_root/vids/taged" \
  "$data_root/vids/clips" \
  "$data_root/stats/wrestler_data" \
  "$data_root/stats/reports" \
  "$data_root/tmp"

for src in "$package_dir"/share/matstat/cfg/*; do
  [ -e "$src" ] || continue
  dest="$data_root/cfg/$(basename "$src")"
  if [ ! -e "$dest" ]; then
    cp "$src" "$dest"
  fi
done

export MATSTAT_ROOT="$data_root"
exec "$package_dir/bin/matstat" "$@"
