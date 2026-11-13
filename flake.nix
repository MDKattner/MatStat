{
  description = "MatStat — statistical analysis and film generation for folkstyle wrestling";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forEachSupportedSystem =
        f:
        nixpkgs.lib.genAttrs supportedSystems (
          system:
          f {
            pkgs = import nixpkgs { inherit system; };
          }
        );

      # Single dependency list shared by the package, the test check, and the
      # development shell so they cannot drift apart.
      runtimePackages =
        ps: with ps; [
          pandas
          numpy
          openpyxl
          fastapi
          uvicorn
          python-multipart
          plotly
          websockets
        ];

      runtimePython = pkgs: pkgs.python3.withPackages runtimePackages;
      testPython =
        pkgs:
        pkgs.python3.withPackages (
          ps:
          runtimePackages ps
          ++ [
            ps.pytest
            ps.httpx
          ]
        );
      devPython =
        pkgs:
        pkgs.python3.withPackages (
          ps:
          runtimePackages ps
          ++ [
            ps.pytest
            ps.httpx
            ps.playwright
          ]
        );

      mkMatStat =
        pkgs:
        import ./nix/package.nix {
          inherit self pkgs;
          python = runtimePython pkgs;
        };
    in
    {
      packages = forEachSupportedSystem (
        { pkgs }: {
          default = mkMatStat pkgs;
          matstat = mkMatStat pkgs;
        }
      );

      apps = forEachSupportedSystem (
        { pkgs }: {
          default = {
            type = "app";
            program = "${mkMatStat pkgs}/bin/matstat";
            meta = {
              description = "MatStat wrestling analysis web app";
            };
          };
        }
      );

      checks = forEachSupportedSystem (
        { pkgs }:
        {
          package = mkMatStat pkgs;
          tests = import ./nix/tests.nix {
            inherit self pkgs;
            python = testPython pkgs;
          };
        }
        // nixpkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          # Enforce the Nix packaging compatibility rules from AGENTS.md: evaluate
          # the module like an upgrading operator and pin the public contract.
          compat = import ./nix/compat-check.nix {
            inherit self nixpkgs;
            system = pkgs.stdenv.hostPlatform.system;
          };
        }
      );

      nixosModules.default = import ./nix/module.nix { inherit self; };

      devShells = forEachSupportedSystem (
        { pkgs }: {
          default = pkgs.mkShell {
            buildInputs = [
              # ── Python runtime + dev/test packages ──
              (devPython pkgs)

              # ── System tools ──
              pkgs.ffmpeg
              pkgs.mkvtoolnix
              pkgs.playwright-driver.browsers

              # ── Advisory lint / format ──
              pkgs.ruff
              pkgs.nixfmt
            ];

            # Point Playwright at the Nix-provided Chromium instead of ~/.cache.
            PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";

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
                  name, match_date, csv_data = MakeNameAndCSV(path)
                  wrestler, opponent, result = ParseTaggedName(name)
                  print(f"== {path} ==")
                  print(f"  title    : {name!r}")
                  print(f"  date     : {match_date!r}")
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

              # Show the shape of compiled wrestler data in the SQLite store.
              matstat-csv () {
                python3 - "$@" <<'PY'
              import sys

              from scripts import storage

              names = sys.argv[1:] or storage.list_wrestlers()
              if not names:
                  print("!! no compiled wrestlers (run Tabulate Sequences first)")
                  sys.exit(0)
              for name in names:
                  try:
                      df = storage.load_wrestler_frame(name)
                  except FileNotFoundError:
                      print(f"!! no compiled data for {name!r}")
                      continue
                  origins = df.index.astype(str).str.split(":").str[0]
                  print(f"== {name} ==")
                  print(f"  shape: {df.shape}  ({len(df.index)} rows, {len(df.columns)} cols)")
                  print(f"  unique origins (videos): {origins.nunique()}")
                  print(f"  columns: {list(df.columns)}")
                  print(f"  index name: {df.index.name!r}  duplicates: {df.index.duplicated().sum()}")
                  print(df.dtypes.to_string())
                  print(df.head(6).to_string())
                  print()
              PY
              }

              # Derived stats overview for compiled wrestler data in the SQLite store.
              matstat-stats () {
                python3 - "$@" <<'PY'
              import sys
              from collections import Counter

              from scripts import storage
              from scripts.helpers import (
                  COL_OPPONENT_MOVES,
                  COL_OPPONENT_SCORES,
                  COL_TEAM_MOVES,
                  COL_TEAM_SCORES,
                  GenerateInitiationDFBySegment,
                  PinCount,
              )

              names = sys.argv[1:] or storage.list_wrestlers()
              if not names:
                  print("!! no compiled wrestlers (run Tabulate Sequences first)")
                  sys.exit(0)
              for name in names:
                  try:
                      df = storage.load_wrestler_frame(name)
                  except FileNotFoundError:
                      print(f"!! no compiled data for {name!r}")
                      continue
                  print(f"== {name} ==")
                  print("  offense (top 8):", dict(Counter(m for ml in df[COL_TEAM_MOVES] for m in ml).most_common(8)))
                  print("  defense (top 8):", dict(Counter(m for ml in df[COL_OPPONENT_MOVES] for m in ml).most_common(8)))
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
              echo "  MATSTAT_AUTH=off uvicorn scripts.web.app:app --timeout-graceful-shutdown 10   Web app (auth off, http://127.0.0.1:8000)"
              echo "  pytest                                        Tests"
              echo ""
              echo "  matstat-meta <video.mkv>...     Inspect tagged-video metadata (title + chapters)"
              echo "  matstat-csv  [wrestler...]     Compiled rows as loaded from the database"
              echo "  matstat-stats [wrestler...]    Derived move/initiation stats overview"
              echo ""
            '';
          };
        }
      );
    };
}
