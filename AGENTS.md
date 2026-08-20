# MatStat AI Agent Guide

## Core Concepts
- **Frontend**: Sole frontend is a FastAPI web app (`scripts/web/`). No CLI or desktop GUI exists.
- **Environment**: NixOS (`nix-shell`) or `pipenv` for non-NixOS.
- **No Build/Lint/Format System**: Style is enforced manually.

## Development Commands
```bash
# Launch (NixOS)
MATSTAT_AUTH=off uvicorn scripts.web.app:app --reload

# Run Tests
pytest
```

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
- **Frontend Components**: Shared UI lives in `scripts/web/static/js/components.js`. Use `queryPanel` for unified filter UIs (Search + Combine Clips). Use `el()` for all DOM construction.
- **Tab Structure**: Tabs mount into `#tab-*` roots via `mount*(root)`. Each tab owns a state object, loads config via `loadOptions()`/`loadConfigs()`, and listens to `"configs-updated"` window events.

## Testing
- **Setup**: `pytest`.
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
- `COL_NET_POINTS`, `COL_ADJUSTED_NET_POINTS` (int16, recomputed on load)
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
- `POST /api/search` — `SearchQuery` body, returns `{ count, matches }`. Supports all QueryBuilder fields.
- `POST /api/search/export` — Same body, returns CSV blob.
- `POST /api/clips/find` — `FindRequest` body. Legacy path (filter_type + filter_item) or QueryBuilder path (filter_item empty).
- `POST /api/combine-clips` — Single reel job. Uses `filter_type` + `filter_item`.
- `POST /api/combine-clips/batch` — Multi-wrestler reel job.
- `GET /api/config` — Full config (moves, ties, wrestlers, teams, rulesets).

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
