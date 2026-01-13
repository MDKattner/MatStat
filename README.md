<img
    style="display: block;
           margin-left: auto;
           margin-right: auto;
           width: 30%;"
    src="./logo.png"
    alt="MatStat Logo">
</img>

A tool for statistical analysis of folkstyle wrestling film.

MatStat provides command-line tools to tag wrestling matches, extract sequence data, and generate statistical reports. It embeds chapter metadata into video files to create a data source for analyzing wrestler performance, move effectiveness, and match dynamics.

## Key Features

-   `fzf`-based interface for tagging sequences with ties/positions, moves, and scoring.
-   Embeds chapter data directly into new Matroska (`.mkv`) video files.
-   Compiles data from multiple tagged videos into wrestler-specific CSV files.
-   Generates reports on offense, defense, and attack initiation.
-   Exports statistics into a multi-sheet Excel workbook.
-   Configuration is done by editing text files for wrestlers, moves, and scoring outcomes.

## Data Pipeline

The data pipelines are as follows:

1.  **Tagging (`Tag Film` script):** A user selects a raw video from `vids/untaged/`. The script guides the user through creating chapters for each action sequence. A new, tagged `.mkv` video is created in `vids/taged/`, and the original is hidden.
2.  **Compilation (`Compile Stats` script):** This script processes all tagged videos in `vids/taged/`. It extracts the chapter data and aggregates it, writing one `.csv` file per wrestler into `stats/wrestler_data/`.
3.  **Data Use:**
   - **Analysis (`Team Evaluation` script):** This script reads the per-wrestler CSV files, loads them into pandas DataFrames, and calculates a variety of statistics. These stats are then added to wrestler-specific sheets of the , `stats/reports/Team_Stats.xlsx`, Excel file.
   - **Viewing (`Combine Clips` script):** *TO BE IMPLEMENTED* This script creates a new video file in the `vids/clips/` which concatenates all chapters meeting user specifications into a single video.

## Directory Structure

-   `cfg/`: Contains configuration files for wrestlers, moves, ties, etc. Edit these to customize the tagging options.
-   `vids/`:
    -   `untaged/`: Location for raw video files to be tagged.
    -   `taged/`: Output location for `.mkv` video files with embedded chapter metadata.
    -   `clips`: Output location for videos concatenated chapters
-   `stats/`:
    -   `wrestler_data/`: Contains intermediate `.csv` files for each wrestler.
    -   `reports/`: Contains the final `Team_Stats.xlsx` report.
-   `scripts/`: Contains the application logic scripts.
-   `MatStat.log`: A log file for debugging and tracking application activity.

## Installation and Setup

### Dependencies

-   `pipenv`
-   `ffmpeg`
    -   `ffprobe` (included with most ffmpeg packages)

### Setup

#### ffmpeg

Install [ffmpeg and ffprobe](https://ffmpeg.org/download.html).

#### pipenv

Install [pipenv](https://pipenv.pypa.io/en/latest/installation.html). The project uses `pipenv` to manage Python dependencies.

```bash
# Clone the repo
git clone https://github.com/MDKattner/MatStat.git

# cd into the directory
cd MatStat

# Install all required Python packages
pipenv install
```

## Usage

All scripts are run through the `Run.sh` entry point, which provides an interactive menu.

```bash
./Run.sh
```

Select the desired script from the menu. The preview pane shows a description of what each script does.

## Scripts Overview

-   **`Tag Film`**: Interactively tags a video by guiding the user through creating `ChapterSequence` objects for each action sequence.
-   **`Compile Stats`**: Processes all tagged videos and generates an aggregated `.csv` file for each wrestler.
-   **`Team Evaluation`**: Reads the compiled CSVs, calculates offensive, defensive, and initiation statistics, and generates a multi-sheet `Team_Stats.xlsx` report.
-   `helpers.py`: Contains shared data classes (`ChapterSequence`) and functions used by the other scripts for video processing, data formatting, and statistical calculation. This also contains functions useful for analyzing the csv files in a Jupyter Notebook.
    -   This also contains many useful functions if one wants to analyze the csv files in a Jupyter Notebook
-   **`Combine Clips`**: (Not Implemented) Placeholder for a script to combine video clips.

## Configuration

Customize the tagging options by editing the text files in the `cfg/` directory:

-   `Wrestlers.config`: Add wrestler names with each wrestler on a new line (in `.gitignore` to avoid commiting PID).
-   `Ties.config`: Add tie-ups or positions (e.g., "overhook", "leg ride", "in base").
-   `Moves.config`: Add move names.
-   `Outcomes.config`: Add scoring notations (e.g., "T" for Takedown, "N3" for Nearfall-3pts).
