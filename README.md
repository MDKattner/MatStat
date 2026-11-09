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

-   Single interface: **web app** (`uvicorn scripts.web.app:app --reload`).
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
-   `flake.nix`: Nix flake defining the development shell (all dependencies pre-configured).
-   `flake.lock`: Pinned flake inputs (locks the `nixpkgs` revision used by the dev shell).
-   `Pipfile`: pipenv dependencies (alternative to the Nix flake).
-   `MatStat.log`: A log file for debugging and tracking application activity.

## Installation and Setup

### Dependencies

-   [Nix](https://nixos.org/download/) with flakes enabled (recommended — the flake supplies Python, ffmpeg, pytest, and Playwright/Chromium), or `pipenv` for a non-Nix setup
-   `ffmpeg`
    -   `ffprobe` (included with most ffmpeg packages)

### Setup

#### NixOS / Nix (primary development environment)

The project uses a Nix flake (`flake.nix`) whose `devShells.default` provides all dependencies (Python, ffmpeg, pytest, Playwright/Chromium). On NixOS — or any system with Nix and flakes enabled — enter the development shell with `nix develop`:

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
uvicorn scripts.web.app:app --reload      # Launch web app (http://127.0.0.1:8000)
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
The pipenv path needs a one-time install:
```bash
pipenv install --dev                      # provides playwright + axe-playwright-python
pipenv run playwright install chromium    # one-time browser download
pipenv run pytest -m browser
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
> See [Authentication & Deployment](#authentication--deployment) below.

#### Non-NixOS (pipenv)

Install [ffmpeg and ffprobe](https://ffmpeg.org/download.html). Install [pipenv](https://pipenv.pypa.io/en/latest/installation.html).

```bash
git clone https://github.com/MDKattner/MatStat.git
cd MatStat
pipenv install
```

To run:
```bash
pipenv run uvicorn scripts.web.app:app --reload      # Launch web app (http://127.0.0.1:8000)
pipenv run pytest                                    # Run fast tests (browser suite excluded)
pipenv run pytest -m browser                         # Playwright + axe UI suite
```

Install Chromium once before the browser suite: `pipenv run playwright install chromium`.

## Usage
**Web app:** Start the FastAPI server and open http://127.0.0.1:8000 in a browser:
```bash
uvicorn scripts.web.app:app --reload
```
The tabs above are available in the browser with the same workflows, plus PCA and Auditing tabs. Browsers can't play `.mkv` directly, so sources are streamed as progressive HLS previews that start playing within seconds while the rest of the video encodes in the background. The on-screen log panel is removed from the web UI by default; pass the logging flag to show it:
```bash
MATSTAT_VISIBLE_LOGGING=1 uvicorn scripts.web.app:app
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

## Authentication & Deployment

MatStat protects wrestler PII and video with password authentication. It is **enabled by default**: start the server with `MATSTAT_PASSWORD` set, and every page and API (including `/ws`, static assets, previews, and downloads) requires the login page. The server **fails fast** if a password is expected but not configured, so an unprotected instance is never silently served:

```bash
MATSTAT_PASSWORD="StrongPassword" uvicorn scripts.web.app:app
```

Behavior knobs (all optional):

-   `MATSTAT_AUTH=off` — disable authentication entirely (trusted LAN/local only).
-   `MATSTAT_SESSION_HOURS=12` — how long a login stays valid (default 12 hours). Only **one session is active at a time**; a second login while one is active is rejected until the first is logged out or expires.
-   `MATSTAT_SECURE_COOKIE=1` — set the `Secure` attribute on the session cookie (required over HTTPS).
-   `MATSTAT_ROOT=/path` — data root for `cfg/`, `vids/`, and `stats/` (defaults to the repository root; browser tests use it to isolate their temp tree).

Because the app is password-only and single-session, it is meant to sit **behind an HTTPS front**. Two straightforward options:

-   **Caddy reverse proxy** (recommended, automatic TLS): put `https://your.domain { reverse_proxy 127.0.0.1:8000 }` in the Caddyfile, then run `MATSTAT_PASSWORD=... MATSTAT_SECURE_COOKIE=1 uvicorn scripts.web.app:app --host 127.0.0.1`.
-   **uvicorn TLS directly**: `MATSTAT_PASSWORD=... MATSTAT_SECURE_COOKIE=1 uvicorn scripts.web.app:app --ssl-keyfile key.pem --ssl-certfile cert.pem`.

> Use the local dev commands above with `MATSTAT_AUTH=off` unless you actually want to exercise the login flow.

## Module Overview

-   **`helpers.py`**: Contains shared data classes (`ChapterSequence`) and functions used by the web app for video processing, data formatting, and statistical calculation. Also contains many useful functions for analyzing the csv files in a Jupyter Notebook.
-   **`scripts/web/`**: The FastAPI web app (app.py, ffmpeg.py, jobs.py, ws.py, transcode.py, and the tab modules tag/stats/team_eval/clips/search/pca/trend/audit).

## Configuration

Customize the tagging options via JSON files in the `cfg/` directory (or the structured editors in the web Config Editor):

-   `config.json` (committed): Holds `active_ruleset`, `moves`, `ties`, and per-ruleset `rulesets` (each with `outcomes: {code: {points, description, counts_as_pin}}`). Seeded with Folkstyle (real values) plus Freestyle and Greco-Roman (UWW scoring templates — review before relying on them). Move/ties/outcome lists are shared across rulesets; only the scoring outcomes differ per ruleset.
-   `Wrestlers.json` (gitignored — contains PII): Holds `wrestlers: [names]` and `teams: {team: [names]}`. A wrestler may belong to multiple teams. The committed `Wrestlers.json.example` placeholder (`UNKNOWN` wrestler) is used as a fresh-clone fallback, so **never commit real wrestler names** to the repo.
-   The **active ruleset** determines `score_to_pts` and pin counts used for net-point statistics; it is read when tagged CSVs are compiled, so switch it via the header gear button (web) and re-run Tabulate Sequences to regenerate `stats/wrestler_data/`.
-   All editors (the web Config Editor gear button) support Add, Edit, and Delete with automatic `.bak` backup creation on save.
