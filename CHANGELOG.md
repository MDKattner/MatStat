# Changelog

All notable changes to MatStat are recorded here. The version lives in
[`VERSION`](VERSION); releases are tagged `vMAJOR.MINOR.PATCH`, and packaging
changes bump the version in the same commit.

## [0.3.0] - 2026-11-18

A user-interface and accessibility update. There are no API, configuration, or
data-format changes; existing deployments upgrade in place.

### Added

- **Twelve per-browser themes**, chosen in **Config Editor → Appearance**. The
  list includes Catppuccin Mocha/Latte, Neofusion, Dracula, Nord, Gruvbox Dark,
  Tokyo Night, One Dark, Solarized Dark, GitHub Dark, and two new High Contrast
  entries. The choice is saved to `localStorage`, applies to the login page on
  the same browser, and is never sent to the server.
- **High Contrast (dark)** and **High Contrast (light)**, modelled on VS Code's
  Default High Contrast themes: black or white canvas, hard borders, and
  saturated accents. The dark palette keeps every text pair at or above 8.6:1
  and the light palette at or above 5.9:1, well past WCAG AA.
- **OS-driven defaults**, including accessibility settings. With no saved theme
  the app follows the desktop: High Contrast when `prefers-contrast: more` is
  set, otherwise Catppuccin Mocha or Latte by `prefers-color-scheme`. Both media
  queries are watched live, so changing the OS setting applies without a reload;
  an explicit theme choice always wins.
- A pre-paint theme bootstrap on the login page, so it no longer flashes the old
  hard-coded dark palette.

### Changed

- Palette tokens moved out of `app.css` into `css/themes.css`; `app.css` now
  owns structure and theme-independent details only (`--radius-*`,
  `--shadow-*`, `--hover`, `--stripe`, `accent-color`, `::selection`).
- Elevation, hover, and striping derive from the active theme, with
  `light-dark()` shadows and `color-mix()` tones, so light themes no longer
  inherit dark-theme shadows.
- Pattern Map and Trends charts are recoloured from the active theme and
  re-render on a theme switch instead of staying in the backend's light
  template.
- Header About and Config Editor icons are inline SVG rather than text glyphs,
  with consistent 32 px targets.
- Toasts animate in, dialogs get a blurred backdrop, and pressed buttons move
  by 1 px for tactile feedback.
- The video panel now shows an explicit **Select a video to preview** overlay,
  a spinner while buffering, and a readable error state instead of an empty
  black frame.
- Segmented controls, active list rows, the connection badge, and disabled
  buttons use the theme's on-colours, so labels stay legible on filled
  backgrounds in every palette.

### Accessibility

- `--on-accent`, `--on-success`, `--on-warning`, and `--on-error` tokens keep
  text readable on filled controls in all themes, including the light ones.
- A consistent `:focus-visible` outline (2 px accent, 2 px offset) on controls,
  plus dedicated focus rings for text, time, and filter inputs.
- Decorative SVG icons are `aria-hidden`; every icon-only button keeps its
  accessible name.
- Tabular numerals in timers, scores, and tables so values do not reflow.
- `prefers-reduced-motion: reduce` disables the new transitions and animations.

### Tests

- `tests/test_theme_contrast.py` parses `themes.css` and enforces the High
  Contrast targets (7:1 body text, 4.5:1 status and on-colour pairs).
- `tests/test_browser_smoke.py` covers theme defaults, the High Contrast
  entries, saved-theme persistence, live `prefers-contrast` changes, and that a
  saved theme overrides the OS setting.

## [0.2.1] - 2026-11-15

### Added

- `MATSTAT_SHUTDOWN_GRACE` (seconds, default `10`) sets how long shutdown waits
  for running jobs to cancel. Jobs still running at the deadline are logged by
  id and kind and abandoned.
- `kill -USR1 <pid>` dumps every thread's stack to stderr for diagnosing a
  stalled job or shutdown.

### Performance

Server:

- Compiled data loads in one SQLite snapshot and one joined query
  (`storage.load_frames`), so Search, Find Clips, reel building, the conflict
  scan, and report generation no longer open a connection per wrestler.
- The Audit listing caches each video's probe result until the file's size or
  mtime changes, instead of running `ffprobe` for every video on every request.
  Entries for deleted videos are pruned on the next listing.
- Pattern Map and Trends figures are cached against the compiled database
  (main file plus WAL) and, for Pattern Map, the roster file. Repeating a
  request or switching back to a previous control setting returns the cached
  figure; any compile or roster edit invalidates it.
- Search result rows and both PCA/trend video splits are built vectorized
  instead of per-row `iterrows()`/`apply()` calls.
- `GenerateMoveMatrix` encodes the exploded move list once instead of twice.
- Text responses (`text/html`, `text/css`, `application/javascript`,
  `application/json`, `text/csv`, …) of 1 KiB–512 KiB are gzipped when the
  client sends `Accept-Encoding: gzip`. HLS playlists/segments, downloads, and
  larger files such as the vendored Plotly build are served unchanged.
- Generated HLS segments are served with
  `Cache-Control: public, max-age=31536000, immutable`, so re-opening a
  preview reuses them instead of re-downloading.

Client:

- Tabs mount lazily on first view instead of all seven at boot, so a page load
  no longer triggers the audit probe sweep or one request per tab.
- `/api/config` is fetched once and shared by every tab; saving the config
  invalidates it.
- Plotly and hls.js load on demand (Plotly from `/static/vendor` with a CDN
  fallback), removing both from the initial page load.
- Background jobs update over the WebSocket, with a 5 s HTTP poll as fallback,
  replacing the 400 ms polling loop (~150 requests/minute per job).
- `checkList`/`filterList` precompute team membership and skip redundant list
  rebuilds; selecting an item only toggles the highlight.

### Changed

- Background jobs now run on daemon worker threads instead of a
  `ThreadPoolExecutor`, so an abandoned job can never block interpreter exit
  after uvicorn stops.
- `ffprobe`, `mkvmerge`, and `mkvpropedit` calls are routed through the new
  `scripts/processes.py` registry (`run_tracked`), which starts each tool in its
  own session and registers it for shutdown.
- Search, Find Clips, export, and reel queries no longer apply a default
  `-20…20` points window. Each points range is filtered only when a bound is
  supplied, so high-scoring sequences (for example a pin bonus) are no longer
  dropped silently. The Search and Combine Clips point inputs start blank with
  `No min` / `No max` placeholders.
- Highlight-reel generation probes each source video once instead of once per
  sequence, and the offense/defense move tables are computed in a single pass
  over the frame instead of re-filtering it once per move.
- Removed the unused CSV-loading helpers `LoadAllWrestlerData`,
  `MakeFormattedDataFrame`, and `SwapPerspectiveCSV`; compiled data is read
  from SQLite via `scripts/storage.py`.

### Behavior notes

Visible differences from the performance work above, none of which change any
tagged data or query result:

- A tab loads its data the first time you open it, so the first visit to a tab
  can show its loading state for a moment; later visits reuse the mounted tab.
- Plotly and hls.js are fetched on first use. If both the vendored file and the
  Plotly CDN are unreachable, the Pattern Map/Trends plot fails with the usual
  load error.
- Job progress is pushed over the WebSocket; with the socket down it refreshes
  every 5 s instead of every 400 ms.
- Compressed responses advertise `Vary: Accept-Encoding`, so proxies cache the
  gzip and identity variants separately.

### Fixed

- Codec detection works with current `ffprobe`. The probe now asks for
  `codec_type` and `codec_name` and parses by token, so uploads, previews, and
  reel stream-copy decisions succeed again on ffprobe 8.x (the old
  `csv=nw=1:nk=1` invocation failed with `Failed to set option 'nw'`).
- Uploading the same file name twice in one request reports the second as
  `conflict` instead of overwriting the first. Uploads, reels, and reports
  reserve their destination atomically, so two jobs cannot choose the same name.
- Deleting a video or reel now cancels an in-flight preview transcode first,
  instead of letting the worker recreate preview files after the delete.
- Failed or cancelled reel jobs remove their temporary segment directory and
  any partial output; failed report jobs remove their placeholder workbook.
- Reel versions sort numerically, so `-10` is newer than `-9` rather than
  sorting lexicographically.
- Tagging publishes its finished file without overwriting an existing tagged
  video. Two tag jobs racing on the same output name (for example `match.mp4`
  and `match.mkv`) now fail the later one with "Tagged video already exists"
  instead of silently replacing the first result and deleting its source.
- Listing videos returns only supported video extensions.
- The video player now keeps its total duration in sync as an HLS preview
  finishes transcoding. Previously the label showed the length loaded so far
  and only corrected itself after the video was opened again.
- The Search wrestler selector now loads the `cfg/Wrestlers.json` roster (plus
  any compiled names missing from it), matching Combine Clips, instead of
  staying empty until the first compile.
- Preview playback uses stream copy for h264 sources without an audio track
  instead of falling back to a full re-encode.
- Reel names replace `\`, `:`, and `,` in addition to spaces and `/`, so
  user-supplied wrestler or move text cannot produce awkward download names.
- Stopping the server now always returns control to the shell. Shutdown cancels
  jobs, kills tracked child processes, waits a bounded grace period, and a
  watchdog forces the process to exit at grace plus five seconds. Previously a
  job stuck in a step without a cancellation checkpoint (a SQLite compile
  commit, `mkvpropedit`, `ffprobe`, or an openpyxl write) held
  `lifespan.shutdown()` open forever, because uvicorn's
  `--timeout-graceful-shutdown` only bounds connection handlers.

## [0.2.0] - 2026-11-13

### Added

- `/readyz` now reports a `database` check alongside data root, config, and
  ffmpeg.

### Changed

- Compiled wrestler sequences now live in a single SQLite database at
  `stats/wrestler_data/matstat.db` instead of per-wrestler CSV files. The
  schema is created on startup; running Tabulate Sequences rebuilds every
  wrestler's rows in one transaction. Existing CSV files are left in place as a
  rollback snapshot but are no longer read.
- Team Evaluation reports list wrestler names under **Source files** and
  describe **Compiled points** as stored values from the compiled database.
- Log Match and Review & Fix pair every sequence detail on its own row
  (tie/opponent tie, moves, scores) with equal-height lists, so a hidden
  opponent half no longer shifts the middle columns. Opponent columns are now
  labelled **Opponent Moves** and **Opponent Scores**.

### Fixed

- Pattern Map draws a square plotting window and pins both axes to the same
  scale, so the two principal components share one pixels-per-unit ratio at any
  zoom instead of stretching one axis.
- Stopping the server no longer hangs when a browser tab holds the `/ws` event
  stream open. The handler now watches for disconnects, so uvicorn can finish
  its graceful shutdown instead of waiting on the task forever.
- Running jobs are cancelled and the worker pool is joined during shutdown.
- The packaged launcher and documented dev commands pass
  `--timeout-graceful-shutdown 10`, bounding any future stall.
- uvicorn shutdown diagnostics now reach `MatStat.log`, so a slow stop is
  visible in the log instead of looking like a silent freeze.

## [0.1.0] - 2026-11-11

### Added

- FastAPI web app for tagging matches, compiling wrestler CSVs, generating
  Excel reports, building move/position reels, searching across wrestlers, PCA
  pattern mapping, and seasonal trends.
- Nix flake package, development shell, and hardened NixOS module with data
  seeding and optional Caddy TLS.
- GitHub Actions CI running `nix flake check` with a cached Nix store.
- `checks.compat`, which evaluates legacy and Caddy/passwordFile deployments and
  fails if module defaults, the service environment, or `ExecStart` change.
- `MATSTAT_LOG_LEVEL` and `MATSTAT_LOG_FILE` for file-log control.

### Changed

- The version is single-sourced in `VERSION`; the package name and FastAPI
  `version` field both read it.
- `MatStat.log` rotates at 5 MiB with three backups instead of growing without
  bound, and defaults to `INFO`.
- Test runs write logs to a temporary directory instead of the repository.
- The README is a quickstart; architecture and configuration details live in the
  wiki.

### Fixed

- `ffprobe` calls use argv lists instead of `shell=True`.
- An invalid `MATSTAT_SESSION_HOURS` falls back to 12 hours instead of crashing
  at import.
- IPv6 binds (`host = "::"`) proxy to `[::1]` through Caddy instead of a broken
  `host:port` string.
- `stateDir` under `/home` or `/root` now fails module evaluation with an
  explanation, matching the `ProtectHome = true` runtime behavior.

### Security

- Authentication is enabled by default; the server refuses to start without a
  password unless `MATSTAT_AUTH=off`.
- Uploads enforce size, file-count, extension, free-space, and ffprobe checks.
- The service runs as a dedicated user with `ProtectSystem = "strict"`,
  `ProtectHome = true`, and a single writable data root.

[Unreleased]: https://github.com/MDKattner/MatStat/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/MDKattner/MatStat/releases/tag/v0.2.1
[0.2.0]: https://github.com/MDKattner/MatStat/releases/tag/v0.2.0
[0.1.0]: https://github.com/MDKattner/MatStat/releases/tag/v0.1.0
