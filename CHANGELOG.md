# Changelog

All notable changes to MatStat are recorded here. The version lives in
[`VERSION`](VERSION); releases are tagged `vMAJOR.MINOR.PATCH`, and packaging
changes bump the version in the same commit.

## [0.2.1] - 2026-11-14

### Changed

- Background jobs now run on daemon worker threads instead of a
  `ThreadPoolExecutor`, so an abandoned job can never block interpreter exit
  after uvicorn stops.
- `ffprobe`, `mkvmerge`, and `mkvpropedit` calls are routed through the new
  `scripts/processes.py` registry (`run_tracked`), which starts each tool in its
  own session and registers it for shutdown.

### Added

- `MATSTAT_SHUTDOWN_GRACE` (seconds, default `10`) sets how long shutdown waits
  for running jobs to cancel. Jobs still running at the deadline are logged by
  id and kind and abandoned.
- `kill -USR1 <pid>` dumps every thread's stack to stderr for diagnosing a
  stalled job or shutdown.

### Fixed

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

[Unreleased]: https://github.com/MDKattner/MatStat/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MDKattner/MatStat/releases/tag/v0.1.0
