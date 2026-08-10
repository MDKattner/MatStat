{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    # ── Python runtime ──
    python3

    # ── Python packages (PyPI equivalents) ──
    python3Packages.pandas
    python3Packages.numpy
    python3Packages.openpyxl       # Excel writer (.xlsx)
    python3Packages.fastapi        # Web backend (scripts/web)
    python3Packages.uvicorn        # ASGI server for the web app
    python3Packages.python-multipart  # Multipart uploads (web video upload)
    python3Packages.httpx          # FastAPI TestClient
    python3Packages.plotly          # Interactive PCA figures (web)

    # ── Python dev / test ──
    python3Packages.pytest
    python3Packages.pytest-mock
    pipenv

    # ── System tools ──
    ffmpeg                         # Includes ffprobe
  ];

  shellHook = ''
    # ── Dev helper macros ────────────────────────────────────────────────────
    # Run from the repo root. All macros go through scripts/helpers.py so what
    # you see matches exactly what the web app processes.

    # Inspect the embedded metadata of tagged video(s): title + every chapter.
    matstat-meta () {
      python3 - "$@" <<'PY'
import sys
from pathlib import Path

from scripts.helpers import ChapterSequence, MakeNameAndCSV, ParseTaggedName

for arg in sys.argv[1:]:
    path = Path(arg)
    if not path.is_file():
        print(f"!! not found: {path}")
        continue
    name, csv_data = MakeNameAndCSV(path)
    wrestler, opponent, result = ParseTaggedName(name)
    print(f"== {path} ==")
    print(f"  title    : {name!r}")
    print(f"  wrestler : {wrestler!r}  opponent={opponent!r}  result={result!r}")
    rows = [line for line in csv_data.splitlines() if line]
    if not rows:
        print("  (no sequences / no chapters)")
        continue
    print(f"  sequences: {len(rows)}")
    for line in rows:
        chap = ChapterSequence.FromCSVRow(line)
        print(
            f"    [{chap.start_time:>6} - {chap.end_time:>6}] "
            f"{'A' if chap.attack_defend else 'D'} | {chap.tie_up!r} | "
            f"moves={chap.team_moves} opp={chap.op_moves} | "
            f"scores={chap.team_scores} opp={chap.op_scores}"
        )
PY
    }

    # Show the shape of compiled wrestler CSV(s) as processed by helpers.
    matstat-csv () {
      python3 - "$@" <<'PY'
import sys
from pathlib import Path

from scripts.helpers import MakeFormattedDataFrame

for arg in sys.argv[1:]:
    path = Path(arg)
    if not path.is_file():
        print(f"!! not found: {path}")
        continue
    df = MakeFormattedDataFrame(path)
    origins = df.index.astype(str).str.split(":").str[0]
    print(f"== {path} ==")
    print(f"  shape: {df.shape}  ({len(df.index)} rows, {len(df.columns)} cols)")
    print(f"  unique origins (videos): {origins.nunique()}")
    print(f"  columns: {list(df.columns)}")
    print(f"  index name: {df.index.name!r}  duplicates: {df.index.duplicated().sum()}")
    print(df.dtypes.to_string())
    print(df.head(6).to_string())
    print()
PY
    }

    # Derived stats overview for compiled wrestler CSV(s).
    matstat-stats () {
      python3 - "$@" <<'PY'
import sys
from pathlib import Path

from scripts.helpers import (
    COL_OPPONENT_SCORES,
    COL_TEAM_SCORES,
    GenerateInitiationDFBySegment,
    MakeFormattedDataFrame,
    MoveDefenseCounts,
    MoveUsageCounts,
    PinCount,
)

for arg in sys.argv[1:]:
    path = Path(arg)
    if not path.is_file():
        print(f"!! not found: {path}")
        continue
    df = MakeFormattedDataFrame(path)
    print(f"== {path} ==")
    print("  offense (top 8):", dict(list(MoveUsageCounts(df).items())[:8]))
    print("  defense (top 8):", dict(list(MoveDefenseCounts(df).items())[:8]))
    print(f"  pins (team): {PinCount(df, COL_TEAM_SCORES)}  (opp): {PinCount(df, COL_OPPONENT_SCORES)}")
    print(GenerateInitiationDFBySegment(df).to_string())
    print()
PY
    }

    # ── Banner ───────────────────────────────────────────────────────────────
    echo ""
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║         MatStat Dev Environment           ║"
    echo "  ║         Python ${pkgs.python3.version}                    ║"
    echo "  ╚═══════════════════════════════════════════╝"
    echo ""
    echo "  MATSTAT_AUTH=off uvicorn scripts.web.app:app   Web app (auth off, http://127.0.0.1:8000)"
    echo "  pytest                                        Tests"
    echo ""
    echo "  matstat-meta <video.mkv>...     Inspect tagged-video metadata (title + chapters)"
    echo "  matstat-csv  <wrestler.csv>...  Compiled CSV shape as loaded by helpers"
    echo "  matstat-stats <wrestler.csv>... Derived move/initiation stats overview"
    echo ""
  '';
}
