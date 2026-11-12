# Changelog

All notable changes to MatStat are recorded here. The version lives in
[`VERSION`](VERSION); releases are tagged `vMAJOR.MINOR.PATCH`, and packaging
changes bump the version in the same commit.

## [Unreleased]

### Changed

- Log Match and Review & Fix pair every sequence detail on its own row
  (tie/opponent tie, moves, scores) with equal-height lists, so a hidden
  opponent half no longer shifts the middle columns. Opponent columns are now
  labelled **Opponent Moves** and **Opponent Scores**.

### Fixed

- Pattern Map draws a square plotting window and pins both axes to the same
  scale, so the two principal components share one pixels-per-unit ratio at any
  zoom instead of stretching one axis.

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
