<p align="center">
    <img
        style="display: block;
               margin-left: auto;
               margin-right: auto;
               width: 30%;"
        src="./logo.png"
        alt="MatStat Logo">
    </img>
</p>

A tool for statistical analysis and move and position specific film generation of folkstyle wrestling film.

MatStat provides a FastAPI web app to tag wrestling matches, extract sequence data, generate statistical reports, and create move and position specific film. It embeds chapter metadata into video files to create a data source for analyzing wrestler performance, move effectiveness, and match dynamics.

## Key Features

-   Single interface: **web app** (FastAPI; run from source with `nix develop`, or deploy with the NixOS module / `nix run .#`).
-   **Batch video upload** into the untagged pool (multi-file, with per-file success/conflict/error reporting).
-   Interactive tagging of sequences with ties/positions, moves, and scoring — each move can be assigned a count for how many times it occurred in a sequence.
-   **Dual-wrestler mode:** tag both wrestlers in one pass, each with their own ties, plus a match outcome (Win/Loss) recorded as a `(W)` on the winner's name. Per-wrestler stats are derived for both sides automatically.
-   **Match dates:** record a `YYYY-MM-DD` date per match while tagging (or backfill it in the Auditing tab). The date is embedded in the video and stored as a `Match Date` column in the compiled CSVs, enabling per-match trends over a season.
-   Embeds chapter data directly into new Matroska (`.mkv`) video files.
-   Compiles data from multiple tagged videos into wrestler-specific CSV files.
-   Generates reports on offense, defense, and attack initiation.
-   Exports statistics into a multi-sheet Excel workbook.
-   Generates move and position specific film by combining clips based on wrestler, tie-ups, or moves, with optional fast stream-copy extraction and batch multi-wrestler processing.
-   Preview your generated highlight reel directly in the browser before saving.
-   Cross-wrestler search with filterable queries and video preview.
-   **Trends:** per-match net/adjusted points charted over the season with cumulative and rolling averages (matches with no recorded date are skipped and reported).
-   Interactive PCA analysis: brush-select sequences or matches on an offense-vs-defense component scatter to preview them or compile them into a highlight reel.
-   Configuration is done via JSON config files for wrestlers, moves, and scoring outcomes, editable through a built-in Config Editor (header gear button).

## Data Pipeline

The data pipelines are as follows:

1.  **Tagging (`Tag Film` tab):** A user uploads raw videos into `vids/untaged/` (batch upload supported), then selects a video. The interface guides the user through creating chapters for each action sequence. A new, tagged `.mkv` video is created in `vids/taged/`, and the original is deleted.
2.  **Compilation (`Tabulate & Report` tab):** Tabulate all tagged videos in `vids/taged/` into per-wrestler sequence data. The tabulation step extracts the chapter data and aggregates it, writing one `.csv` file per wrestler into `stats/wrestler_data/`. Each row carries stored Net Points, Adjusted Net Points, a `W/L` column (the match result, if recorded), and a `Match Date` column (the `YYYY-MM-DD` tag date, blank when unrecorded), and dual-wrestler videos also produce a perspective-swapped CSV for the opponent.
3.  **Data Use:**
    - **Analysis (`Tabulate & Report` tab):** Reads the per-wrestler CSV files, loads them into pandas DataFrames, and calculates a variety of statistics (with the Initiation summary broken out by match result into All / Wins / Losses rows). These stats are then added to wrestler-specific sheets of a timestamped `stats/reports/Team_Stats_<YYYYMMDD-HHMMSS>.xlsx` workbook, alongside a **Team Summary** sheet of team-wide rates, a **Match Outcomes** sheet consolidating each wrestler's wins/losses, unrecorded matches, win rate, and pins, and a **Read Me** sheet recording ruleset provenance and definitions. `Team_Stats_latest.xlsx` always points at the most recent complete report.
    - **Viewing (`Combine Clips` tab):** Lets you filter tagged sequences by wrestler, tie-up, or move, then concatenates matching clips into a single video in `vids/clips/`. The web app provides a live preview of the generated reel.
    - **Search (`Search` tab):** Query across all wrestlers' tagged data with filters for tie-up, moves, scores, and adjusted net points (net points plus the pin bonus). Preview results in the video player and export to CSV.
    - **Analysis (`PCA` tab):** Projects every tagged sequence (or per-wrestler/per-video match aggregates) onto its first offense and defense move components, colored by net points. Brush-select points to preview them in the player or compile them into a highlight reel.
    - **Trends (`Trends` tab):** Charts a single wrestler's per-match net (or adjusted) points over the season as a scatter of each dated match, with cumulative and rolling averages overlaid. Only matches with a recorded date are plotted; the number of skipped, undated rows is reported so you know how much of the video history is missing from the trend.

## Dual Wrestler Mode & Match Outcome

The Tag Film and Auditing tabs support tagging both wrestlers in a single pass.

-   **Title format:** the winner's name is suffixed with ` (W)`. Single mode stores the bare name (`Alice`, `Alice (W)`, `Alice (L)`); dual mode joins the names with `" / "` and puts ` (W)` on the winner (`Alice (W) / Bob`, or `Alice / Bob (W)` when the opponent won). Draws carry no marker. The `(L)` suffix is single-mode only.
-   **Dual mode:** pick an opponent in the wrestler card; a second tie selector appears so each sequence records both wrestlers' ties as a `"yours:theirs"` colon pair. Tie-up filters (Combine Clips, Search) match on the tagged wrestler's tie (the element before the colon).
-   **Result:** set Win / Loss / No Result per match. The outcome is embedded in the title and surfaced as the `W/L` column in the compiled CSVs.
-   **Compilation:** each dual-mode video also writes a perspective-swapped CSV (attacking flag reversed, move/score columns swapped, net points negated, `W/L` inverted) into the opponent's bucket, so both wrestlers get their own stats. Opponents not on the roster are reported as skipped.
-   **Auditing backfill:** old videos tagged without an opponent/result can be fixed in the Auditing tab, which reads the embedded title and lets you re-tag with an opponent and result. Re-tagging leaves the compiled CSVs untouched unless the "re-tabulate" checkbox is enabled, so tabulation stays a deliberate step in the Tabulate & Report tab.

## Directory Structure

-   `cfg/`: Contains configuration files for wrestlers, moves, ties, etc. Edit these to customize the tagging options.
-   `vids/`:
    -   `untaged/`: Location for raw video files to be tagged.
    -   `taged/`: Output location for `.mkv` video files with embedded chapter metadata.
    -   `clips/`: Output location for generated highlight reel videos.
-   `stats/`:
    -   `wrestler_data/`: Contains intermediate `.csv` files for each wrestler.
    -   `reports/`: Timestamped `Team_Stats_<YYYYMMDD-HHMMSS>.xlsx` reports plus the `Team_Stats_latest.xlsx` pointer (the last 10 are kept).
-   `scripts/`: Contains the application logic scripts.
    -   `web/`: FastAPI web app (app.py, jobs, ffmpeg runner, tab modules, static/).
    -   `helpers.py`: Shared data classes and utility functions.
-   `flake.nix`: Nix flake defining the development shell, production package, and NixOS module (all dependencies pre-configured).
-   `flake.lock`: Pinned flake inputs (locks the `nixpkgs` revision used by the shell and package).
-   `nix/`: Nix package, test check, and NixOS module definitions.
-   `MatStat.log`: Rotating application log (5 MiB per file, 3 backups by default). Set `MATSTAT_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, ...) to change verbosity.

## Installation and Setup

### Dependencies

-   [Nix](https://nixos.org/download/) with flakes enabled — the flake supplies Python, ffmpeg/mkvtoolnix, pytest, Playwright/Chromium, and the production package.
-   `ffmpeg` / `ffprobe` (provided by the flake; needed at runtime for transcoding and clip generation).

### Setup

#### NixOS / Nix (primary development environment)

The project uses a Nix flake (`flake.nix`) whose `devShells.default` provides all dependencies (Python, ffmpeg, pytest, Playwright/Chromium), and whose `packages.default` builds the deployable app. On NixOS — or any system with Nix and flakes enabled — enter the development shell with `nix develop`:

```bash
git clone https://github.com/MDKattner/MatStat.git
cd MatStat
nix develop
```

> **Flakes not enabled?** Either enable `nix-command` and `flakes` in your `nix.conf`, or invoke the shell with the experimental features flag:
> ```bash
> nix --extra-experimental-features 'nix-command flakes' develop
> ```
> The pinned `flake.lock` ensures everyone gets the same `nixpkgs` revision.

Once inside `nix develop`:
```bash
MATSTAT_AUTH=off uvicorn scripts.web.app:app --reload   # http://127.0.0.1:8000
pytest                                    # Run fast tests (browser suite excluded)
pytest -m browser                         # Playwright + axe UI suite (see below)
```

**Browser tests (optional):** the `browser`-marked tests need Playwright and
Chromium. The Nix dev shell already provides both (the browsers come from
`playwright-driver.browsers` via `PLAYWRIGHT_BROWSERS_PATH`), so inside
`nix develop` just run:
```bash
pytest -m browser
```
The axe accessibility checks skip automatically when
`axe-playwright-python` is unavailable (as in the Nix shell); the Playwright
smoke tests still run.

The dev shell also provides helper macros that run the same `scripts/helpers.py` code paths as the web app:

```bash
matstat-meta  vids/taged/*.mkv            # Inspect tagged-video metadata (title + chapters)
matstat-csv   stats/wrestler_data/*.csv   # Show compiled CSV shape as loaded by helpers
matstat-stats stats/wrestler_data/*.csv   # Derived move/initiation stats overview
```

> **Authentication:** auth is on by default — set `MATSTAT_PASSWORD` to a strong password or the server refuses to start. For a trusted local/LAN setup, pass `MATSTAT_AUTH=off` to run without a login page:
> ```bash
> MATSTAT_AUTH=off uvicorn scripts.web.app:app --reload
> ```
> See [Authentication](#authentication) and [Deployment quickstart](#deployment-quickstart) below.

> **Deploying a server instance?** The development shell above is for local work; jump to [Deployment quickstart](#deployment-quickstart), then use the wiki for the full [deployment](https://github.com/MDKattner/MatStat/wiki/Deployment), [operations](https://github.com/MDKattner/MatStat/wiki/Operations), and [troubleshooting](https://github.com/MDKattner/MatStat/wiki/Troubleshooting) guides.

## Usage
**Web app:** run the packaged app and open http://127.0.0.1:8000 in a browser:
```bash
MATSTAT_AUTH=off nix run .#          # trusted local/LAN use
MATSTAT_PASSWORD=... nix run .#      # keep the login page enabled
```
For iterative development, enter the dev shell and use auto-reload:
```bash
nix develop
MATSTAT_AUTH=off uvicorn scripts.web.app:app --reload
```
`nix run .#` uses the current directory as its data root. To keep a run isolated from the repo, point `MATSTAT_ROOT` at a temp directory:
```bash
MATSTAT_ROOT=$(mktemp -d) MATSTAT_AUTH=off nix run .#
```
The tabs above are available in the browser with the same workflows, plus PCA and Auditing tabs. Browsers can't play `.mkv` directly, so sources are streamed as progressive HLS previews that start playing within seconds while the rest of the video encodes in the background. The on-screen log panel is removed from the web UI by default; pass the logging flag to show it:
```bash
MATSTAT_VISIBLE_LOGGING=1 nix run .#
```

## Data Integrity, Reports, and Reels

-   **Match outcomes count per match, not per sequence.** One video with many sequences is one match in **Match Outcomes**. Win rate is blank when no result was recorded; `Matches`, `Recorded`, and `Unrecorded` make the denominator explicit.
-   **Conflicting results are blocked.** If sequences from the same match disagree on `W`/`L`, tabulation and report generation abort before writing anything and list every conflict. Fix the tagged metadata in **Review & Fix** and retry.
-   **Tabulation and reports are fail-safe.** CSVs and workbooks are written atomically. A failed re-tabulation or report keeps the previous valid files intact; incomplete reports are timestamped but never replace `Team_Stats_latest.xlsx`.
-   **Reports are versioned.** Each run writes `Team_Stats_<YYYYMMDD-HHMMSS>.xlsx`; `Team_Stats_latest.xlsx` tracks the newest complete report, and the last 10 are retained. The **Read Me** sheet records the active ruleset, its hash, pin bonus, source files, match counts, date range, and column definitions.
-   **Ruleset changes never rewrite history.** Stored net/adjusted points are trusted as compiled. If the active ruleset differs from the compiled one, a banner appears (Search, Trends, PCA, Report) telling you to re-tabulate; it does not alter existing values.
-   **Reels are versioned.** Generated clips use timestamped names with a `<base>_latest.mkv` alias. **Combine Clips** lists every version with Play, Download, and Delete actions.
-   **Deletes require confirmation** and purge the cached preview. Re-tabulation after a delete is optional and off by default, so removing a video does not silently change compiled stats.
-   **Metadata-only edits** (fixing a name, date, or result without changing sequences) use `mkvpropedit` when available and otherwise fall back to an ffmpeg stream-copy rewrite. Both preserve existing chapters.
-   **API:** query, export, search, and reel endpoints accept only the shared `QuerySpec` shape (`wrestlers` list plus filters). Legacy `filter_type`/`filter_item` bodies return `422`.

## Authentication

Authentication is **enabled by default**. Set `MATSTAT_PASSWORD` or the server
refuses to start. Every page, API route, WebSocket, preview, download, and
static asset requires a login; only `/healthz`, `/readyz`, `/login`, and the
login/logout endpoints are public.

```bash
MATSTAT_PASSWORD="StrongPassword" nix run .#
MATSTAT_AUTH=off nix run .#   # trusted LAN only; never public
```

Only one session is active at a time. `MATSTAT_SECRET` keeps sessions valid
across password changes, and `MATSTAT_SECURE_COOKIE=1` is required over HTTPS.
The complete authentication and environment-variable reference lives in
[Deployment](https://github.com/MDKattner/MatStat/wiki/Deployment).

## Deployment quickstart

MatStat ships as a Nix flake package (`packages.default`, `apps.default`) for
Linux and macOS, and a NixOS module (`nixosModules.default`) that runs a hardened
systemd service, seeds the data tree, and can provision Caddy with automatic
HTTPS. Linux is the CI-verified platform; the macOS package is declared but not
yet tested in CI. There is no OCI image or PyPI package, and the app always runs
as **one process** — do not add uvicorn workers.

### NixOS module

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    matstat.url = "github:MDKattner/MatStat";
  };

  outputs = { self, nixpkgs, matstat, ... }: {
    nixosConfigurations.host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        matstat.nixosModules.default
        {
          services.matstat = {
            enable = true;
            passwordFile = "/etc/matstat/matstat.env";
            virtualHost = "matstat.example.com"; # optional; enables Caddy + HTTPS
          };
          services.caddy.openFirewall = true;
        }
      ];
    };
  };
}
```

Create the runtime secret file (never put secrets in the Nix store):

```bash
sudo install -d -m 0750 /etc/matstat
sudo install -m 0600 /dev/null /etc/matstat/matstat.env
sudoedit /etc/matstat/matstat.env   # MATSTAT_PASSWORD=... and optional MATSTAT_SECRET=...
```

```bash
sudo nixos-rebuild switch --flake .#host
curl -fsS http://127.0.0.1:8000/readyz
journalctl -u matstat -f
```

The module keeps data in `stateDir` (`/var/lib/matstat` by default) and never
overwrites existing `cfg/` files on upgrade.

### Non-NixOS host

```bash
sudo nix --extra-experimental-features 'nix-command flakes' \
  build .#default -o /var/lib/matstat-pkg
```

Then create the `matstat` service account and data tree, point
`MATSTAT_ROOT`/`MATSTAT_SECURE_COOKIE`/`MATSTAT_FORWARDED_ALLOW_IPS` at it, and
run `/var/lib/matstat-pkg/bin/matstat` under systemd. The full unit file,
reverse-proxy configs, health probes, upgrades, backups, and troubleshooting are
in the wiki:

| Topic | Page |
| --- | --- |
| Install, module options, environment variables, TLS proxy | [Deployment](https://github.com/MDKattner/MatStat/wiki/Deployment) |
| Probes, logs, jobs, sessions, upgrades, backups, disk | [Operations](https://github.com/MDKattner/MatStat/wiki/Operations) |
| Symptom-driven fixes | [Troubleshooting](https://github.com/MDKattner/MatStat/wiki/Troubleshooting) |
| HTTP routes, models, job shapes | [API Reference](https://github.com/MDKattner/MatStat/wiki/API-Reference) |
| User workflows and screenshots | [User Manual](https://github.com/MDKattner/MatStat/wiki/User-Manual) |

The wiki is the canonical documentation for operators and users; this README
stays a quickstart.

## Project layout and configuration

Application code lives in `scripts/` — shared data classes and statistics in
`helpers.py`, and the FastAPI app (routes, jobs, ffmpeg/HLS, tab modules) in
`scripts/web/`.

Tagging options come from `cfg/config.json` (moves, ties, and per-ruleset
scoring) and the gitignored `cfg/Wrestlers.json` (roster and teams). Edit both
in the web **Config Editor** or directly as JSON. The committed
`cfg/Wrestlers.json.example` is the fresh-clone fallback, so never commit real
wrestler names.

Schema details, the data pipeline, and module-by-module architecture are in the
[Developer Guide](https://github.com/MDKattner/MatStat/wiki/Developer-Guide) and
[Configuration Editor](https://github.com/MDKattner/MatStat/wiki/Configuration-Editor)
pages.
