# MatStat AI Agent Guide

## Core Concepts
- **Frontend**: Sole frontend is a FastAPI web app (`scripts/web/`). No CLI or desktop GUI exists.
- **Environment**: Nix flake (`nix develop` for development, `nix build .#default` / `nixosModules.default` for deployment) on NixOS or any system with flakes enabled.
- **No Build/Lint/Format System**: Style is enforced manually.

## Development Commands
```bash
# Enter dev shell (flake provides Python, ffmpeg, mkvtoolnix, pytest, Playwright/Chromium)
nix develop

# Launch (from inside the dev shell)
MATSTAT_AUTH=off uvicorn scripts.web.app:app --reload

# Run Tests
pytest                 # fast suite; browser tests excluded
pytest -m browser      # Playwright + axe UI suite

# Build / verify the deployable package
nix build .#default
nix flake check
```

The dev shell's `shellHook` also defines inspection helpers:
```bash
matstat-meta  vids/taged/*.mkv            # Tagged-video title + chapter metadata
matstat-csv   stats/wrestler_data/*.csv   # Compiled CSV shape as loaded by helpers
matstat-stats stats/wrestler_data/*.csv   # Move/initiation stats overview
```

## Deployment
- **Supported paths**: `nixosModules.default` for servers (hardened systemd, data seeding, optional Caddy TLS), or `nix build .#default` plus your own service manager on non-NixOS hosts. There is no OCI image.
- **Docs to keep in sync**: the wiki is canonical for users and operators — `wiki/Deployment.md` (install, env vars, proxy), `wiki/Operations.md` (probes, jobs, backups), `wiki/Troubleshooting.md`, `wiki/API Reference.md`, and `wiki/Developer Guide.md` — plus the option descriptions in `nix/module.nix`. `README.md` is a quickstart that links to those pages. Update the relevant pages whenever you change flake outputs, launcher environment variables, module options, public probes, or the HTTP API.
- **Git-tracked source**: the flake copies only tracked `scripts/` and `cfg/` files. Stage changes before `nix build`/`nix flake check`, and never stage real `cfg/Wrestlers.json` or media.
- **Caddy firewall**: enabling the module's `virtualHost` does not open 80/443; set `services.caddy.openFirewall = true` (or open the ports explicitly).
- **Single process**: jobs, cancellation, sessions, and transcodes live in memory. Do not add uvicorn workers.

## Code Style & Conventions
- **Naming**: `PascalCase` classes/methods, `snake_case` variables, `UPPER_SNAKE_CASE` constants.
- **Types**: MANDATORY type annotations on all functions/variables. Use builtin generics (e.g., `list[str]`).
- **Imports**: Block order: stdlib → 3rd party → local. Always `from __future__ import annotations`.
- **Constants**: Use `COL_*` constants and `EMPTY_SENTINEL` (never hardcode).

## Architecture & Patterns
- **Async Tasks**: Use `JobManager.submit(kind, fn)`. Jobs run in thread pool.
- **FFmpeg**: Use `run_ffmpeg(cmd, ...)` from `scripts.web.ffmpeg` (blocking, handles cancellation/logging).
- **Video Preview**: HLS progressive streaming (`scripts.web.transcode`).
- **Configs**: JSON in `cfg/` (never commit real wrestler PII). Loaders in `helpers.py` (e.g., `LoadAppConfig`).
- **DataFrame Querying**: Use `QueryBuilder` from `scripts/helpers.py` for all DataFrame filtering. Chainable API with 10 filter methods + `apply()` for sort/pagination. Never hand-roll ad-hoc filter logic.
- **Edge Middleware**: `scripts/web/app.py` owns the security-header, CSRF-origin, upload-size, and auth-gate middleware. The security-header middleware is defined last so it wraps every short-circuit response. Public probes and the login script live in `_PUBLIC_PATHS`; upload limits are module globals (`_UPLOAD_MAX_BYTES`, etc.) and are monkeypatched in tests.
- **Frontend Components**: Shared UI lives in `scripts/web/static/js/components.js`. Use `queryPanel` for unified filter UIs (Search + Combine Clips). Use `el()` for all DOM construction.
- **Tab Structure**: Tabs mount into `#<tab>-root` elements via `mount*(root)`. Each tab owns a state object, loads config via `loadOptions()`/`loadConfigs()`, and listens to `"configs-updated"` window events.

## Testing
- **Setup**: `pytest`. The default run excludes `browser`-marked tests; run `pytest -m browser` for the Playwright suite (Chromium is supplied by the Nix flake).
- **Browser harness**: `tests/test_browser_smoke.py` launches uvicorn with `MATSTAT_ROOT` pointed at a temp data tree, so tests never touch real `vids/`/`stats/`/`cfg/`. The `axe` checks skip when `axe-playwright-python` is not installed.
- **Import Hack**: Every test file MUST include:
  ```python
  import sys
  from pathlib import Path
  sys.path.append(str(Path(__file__).parent.parent))
  ```
- **Fixtures**: Use `conftest.py` fixtures (especially `client` for API tests). Use `monkeypatch` (not `pytest-mock`) to redirect `tmp_path`.
- **FakeContext**: Shared `FakeContext` class in `conftest.py` stands in for `JobContext` in clip/job tests.

## DataFrame Columns
All `COL_*` constants are defined in `scripts/helpers.py`. Key columns:
- `COL_ORIGIN` (index), `COL_START_TIME`, `COL_END_TIME`, `COL_ATTACKING` (bool)
- `COL_TIE_UP` (str, dual-wrestler format: `"yours:theirs"`), `COL_TEAM_MOVES`, `COL_OPPONENT_MOVES` (list[str])
- `COL_NET_POINTS`, `COL_ADJUSTED_NET_POINTS` (int16; stored values are trusted on load, recomputed only for legacy 9-column CSVs)
- `COL_MATCH_RESULT` (`"W"` / `"L"` / `""`), `COL_MATCH_DATE` (`"YYYY-MM-DD"` / `""`)
- `COL_MATCH_DURATION` (computed by `QueryBuilder.duration_filter`, not stored in CSV)

## QueryBuilder Reference
```python
from scripts.helpers import QueryBuilder
df = (
    QueryBuilder(frame)
    .attack_filter("Attacking")        # "All" | "Attacking" | "Defending"
    .tie_filter("front headlock")      # wrestler's tie (before colon)
    .opp_tie_filter("underhook")       # opponent's tie (after colon)
    .team_move_filter("half nelson")   # substring by default; exact=True for exact match
    .opp_move_filter("sprawl")
    .match_result_filter("W")
    .date_range_filter("2025-01-01", "2025-12-31")
    .duration_filter(min_seconds=10, max_seconds=60)
    .net_points_filter(min=-5, max=10)
    .adjusted_net_points_filter(min=0, max=20)
    .apply(sort_by="Adjusted Net Points", ascending=False, limit=50, offset=0)
)
```

## API Endpoints (Data)
- `GET /healthz` — public liveness probe (bypasses auth).
- `GET /readyz` — public readiness probe; checks data root, config, and ffmpeg (bypasses auth).
- `POST /api/videos` — Batch upload; enforces `MATSTAT_MAX_UPLOAD_MB`, `MATSTAT_MAX_REQUEST_MB`, `MATSTAT_MAX_UPLOAD_FILES`, extension allowlist, free-space reserve, and an ffprobe video check.
- `POST /api/search` — `QuerySpec` body, returns `{ count, matches }`.
- `POST /api/search/export` — Same body, returns CSV blob.
- `POST /api/clips/find` — `QuerySpec` body; returns `{ wrestlers, count, matches }`.
- `POST /api/combine-clips` — Single reel job from a `QuerySpec` (`wrestlers` must have exactly one name).
- `POST /api/combine-clips/batch` — Multi-wrestler reel job from one `QuerySpec`.
- `DELETE /api/videos/{dir}/{file}` — Delete an `untaged`/`taged` video; `recompile=true` re-tabulates.
- `DELETE /api/clips/{file}` — Delete a generated reel.
- `GET /api/reels` — List generated reels grouped by base name with all versions.
- `GET /api/reports/latest` — Freshness/provenance metadata for the latest report.
- `GET /api/stats/provenance` — Compiled vs active ruleset status (never mutates points).
- `GET /api/config` — Full config (moves, ties, wrestlers, teams, rulesets).

All query routes share `scripts/web/query.py`: `QuerySpec` (extra fields forbidden) and
`apply_query(df, spec, wrestler)`. There is no legacy `filter_type`/`filter_item` shape;
unknown/retired fields return `422`. `origins` selects an exact row set.
`QuerySpec` holds `wrestlers`, `attack_mode`, `tie_up`, `opp_tie`, `team_move`,
`opp_move`, `move_match` (`substring`/`exact`), `match_result`, date/points/duration
ranges, `sort_by`/`ascending`/`limit`/`offset`, and `origins`.

## Frontend Components
- `el(tag, attrs, children)` — DOM element factory (all DOM built programmatically).
- `queryPanel({ onSearch, onExport, onFindClips, onGenerateReel })` — Shared filter panel used by Search and Combine Clips tabs.
- `checkList({ label, items, onChange, teams })` — Multi-select checkboxes with team filter.
- `filterList({ label, items, onChange })` — Single-select filterable list.
- `configCombo({ label, items })` — Autocomplete input backed by datalist.
- `videoPlayer({})` — HLS video player with controls.
- `fetchJson(url, options)` — Fetch wrapper returning `{ ok, status, body }`.
- `pollJob(jobId, { onProgress, onStatus })` — Poll background job until terminal state.
- `showToast(message, kind)` — Transient notification.
