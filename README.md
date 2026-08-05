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

MatStat provides command-line tools (fzf-based TUI) and a Qt6 graphical interface to tag wrestling matches, extract sequence data, generate statistical reports, and create move and position specific film. It embeds chapter metadata into video files to create a data source for analyzing wrestler performance, move effectiveness, and match dynamics.

> **DEPRECATION NOTICE:** The fzf-based CLI (`./Run.sh` and the `scripts/` entry scripts) is **deprecated** and will be removed in a future release. Use the Qt6 GUI instead: `python scripts/qt_app/main.py`. The CLI is kept functional during the transition and prints a deprecation warning on launch.

## Key Features

-   Two interfaces: **Qt6 GUI** (`python scripts/qt_app/main.py`) and **fzf-based TUI** (`./Run.sh` — *deprecated*).
-   Interactive tagging of sequences with ties/positions, moves, and scoring — each move can be assigned a count for how many times it occurred in a sequence.
-   Embeds chapter data directly into new Matroska (`.mkv`) video files.
-   Compiles data from multiple tagged videos into wrestler-specific CSV files.
-   Generates reports on offense, defense, and attack initiation.
-   Exports statistics into a multi-sheet Excel workbook.
-   Generates move and position specific film by combining clips based on wrestler, tie-ups, or moves, with optional fast stream-copy extraction and batch multi-wrestler processing.
-   Preview your generated highlight reel directly in the GUI before saving.
-   Cross-wrestler search with filterable queries and video preview.
-   Configuration is done by editing text files for wrestlers, moves, and scoring outcomes, editable via a built-in Settings dialog (File > Settings).

## Data Pipeline

The data pipelines are as follows:

1.  **Tagging (`Tag Film` script):** A user selects a raw video from `vids/untaged/`. The script guides the user through creating chapters for each action sequence. A new, tagged `.mkv` video is created in `vids/taged/`, and the original is hidden.
2.  **Compilation (`Compile Stats` script):** This script processes all tagged videos in `vids/taged/`. It extracts the chapter data and aggregates it, writing one `.csv` file per wrestler into `stats/wrestler_data/`.
3.  **Data Use:**
    - **Analysis (`Team Evaluation` script):** This script reads the per-wrestler CSV files, loads them into pandas DataFrames, and calculates a variety of statistics. These stats are then added to wrestler-specific sheets of the `stats/reports/Team_Stats.xlsx` Excel file.
    - **Viewing (`Combine Clips` script):** This script lets you filter tagged sequences by wrestler, tie-up, or move, then concatenates matching clips into a single video in `vids/clips/`. The GUI provides a live preview of the generated reel.
    - **Search (`Search` tab):** Query across all wrestlers' tagged data with filters for tie-up, moves, scores, and net points. Preview results in the video player and export to CSV.

## Directory Structure

-   `cfg/`: Contains configuration files for wrestlers, moves, ties, etc. Edit these to customize the tagging options.
-   `vids/`:
    -   `untaged/`: Location for raw video files to be tagged.
    -   `taged/`: Output location for `.mkv` video files with embedded chapter metadata.
    -   `clips/`: Output location for generated highlight reel videos.
-   `stats/`:
    -   `wrestler_data/`: Contains intermediate `.csv` files for each wrestler.
    -   `reports/`: Contains the final `Team_Stats.xlsx` report.
-   `scripts/`: Contains the application logic scripts.
    -   `qt_app/`: Qt6 GUI application (main.py, main_window.py, widgets, workers).
    -   `helpers.py`: Shared data classes and utility functions.
    -   `Tag Film`, `Compile Stats`, `Team Evaluation`, `Combine Clips`: fzf-based entry scripts.
-   `shell.nix`: Nix development shell (all dependencies pre-configured).
-   `Pipfile`: pipenv dependencies (alternative to nix-shell).
-   `MatStat.log`: A log file for debugging and tracking application activity.

## Installation and Setup

### Dependencies

-   `pipenv`
-   `ffmpeg`
    -   `ffprobe` (included with most ffmpeg packages)

### Setup

#### NixOS (primary development environment)

On NixOS, use `nix-shell` to enter a development shell with all dependencies (Python, PyQt6, ffmpeg, fzf, pytest):

```bash
git clone https://github.com/MDKattner/MatStat.git
cd MatStat
nix-shell
```

Once inside `nix-shell`:
```bash
python scripts/qt_app/main.py   # Launch Qt6 GUI
./Run.sh                        # Headless fzf mode (DEPRECATED — see notice above)
pytest                          # Run tests
```

#### Non-NixOS (pipenv)

Install [ffmpeg and ffprobe](https://ffmpeg.org/download.html). Install [pipenv](https://pipenv.pypa.io/en/latest/installation.html).

```bash
git clone https://github.com/MDKattner/MatStat.git
cd MatStat
pipenv install PyQt6 PyQt6-QtMultimedia
pipenv install
```

To run:
```bash
pipenv run python scripts/qt_app/main.py   # Launch Qt6 GUI
pipenv run ./Run.sh                        # Headless fzf mode (DEPRECATED — see notice above)
pipenv run pytest                          # Run tests
```

## Usage

**Qt6 GUI:** Run the graphical interface:
```bash
python scripts/qt_app/main.py
```
This launches a multi-tab window with all four tools accessible at once. Each tab has its own workflow:
- **Tag Film**: Select a video, tag sequences with time ranges, ties, moves (with per-move count), and scoring. Preview the video while tagging.
- **Compile Stats**: Process all tagged videos to generate per-wrestler CSV files.
- **Team Evaluation**: Select a wrestler and view offensive/defensive statistics, then generate an Excel report.
- **Combine Clips**: Filter tagged sequences by wrestler, tie-up, or move. Check multiple wrestlers for batch processing. Enable "Fast extraction (stream copy)" for up to 5x faster clip generation when source codecs are compatible. Preview matching clips and generate a highlight reel.
- **Search**: Query across all wrestlers' tagged data with filters for tie-up, moves, scores, and net points. Preview results in the video player and export to CSV.

**Headless TUI (fzf):** *(DEPRECATED — use the Qt6 GUI above)* Use the `Run.sh` entry point for SSH/Docker/terminal use:
```bash
./Run.sh
```
Select the desired script from the menu. The preview pane shows a description of what each script does.

## Scripts Overview

> **Note:** The scripts below are the *deprecated* CLI entry points. Their functionality is provided by the GUI tabs in `scripts/qt_app/main.py`.

-   **`Tag Film`** *(deprecated)*: Interactively tags a video by guiding the user through creating `ChapterSequence` objects for each action sequence. Supports assigning a count to each move (how many times it occurred in the sequence).
-   **`Compile Stats`** *(deprecated)*: Processes all tagged videos and generates an aggregated `.csv` file for each wrestler.
-   **`Team Evaluation`** *(deprecated)*: Reads the compiled CSVs, calculates offensive, defensive, and initiation statistics, and generates a multi-sheet `Team_Stats.xlsx` report.
-   **`Combine Clips`** *(deprecated)*: Filters tagged sequences by wrestler, tie-up, or move and concatenates matching clips into a single highlight reel video in `vids/clips/`. The GUI provides a live preview of generated reels.
-   `helpers.py`: Contains shared data classes (`ChapterSequence`) and functions used by the other scripts for video processing, data formatting, and statistical calculation. Also contains many useful functions for analyzing the csv files in a Jupyter Notebook.

## Configuration

Customize the tagging options by editing the text files in the `cfg/` directory:

-   `Wrestlers.config`: Add wrestler names with each wrestler on a new line. DO NOT COMMIT PID IN THIS FILE.
    - To avoid this run ```git update-index --assume-unchanged cfg/Wrestlers.config``` after cloning the repo
-   `Ties.config`: Add tie-ups or positions (e.g., "overhook", "leg ride", "in base").
-   `Moves.config`: Add move names.
-   `Outcomes.config`: Add scoring notations (e.g., "T" for Takedown, "N3" for Nearfall-3pts).
    - Alternatively, use File → Settings (Ctrl+,) in the Qt6 GUI for a structured editor with Add, Edit, and Delete support and automatic backup creation.
