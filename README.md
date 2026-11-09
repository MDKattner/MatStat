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
-   `MatStat.log`: A log file for debugging and tracking application activity.

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
> See [Authentication](#authentication) and [Deployment](#deployment) below.

> **Deploying a server instance?** The development shell above is for local work; jump to [Deployment](#deployment) for the NixOS module, systemd, reverse-proxy, and backup instructions.

## Usage
**Web app:** run the packaged app and open http://127.0.0.1:8000 in a browser:
```bash
MATSTAT_AUTH=off nix run .#          # trusted local/LAN use
MATSTAT_PASSWORD=... nix run .#      # keep the login page enabled
```
For iterative development, enter the dev shell and use auto-reload:
```bash
nix develop
uvicorn scripts.web.app:app --reload
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

MatStat protects wrestler PII and video with password authentication. It is **enabled by default**: start the server with `MATSTAT_PASSWORD` set, and every page and API (including `/ws`, static assets, previews, and downloads) requires the login page. The server **fails fast** if a password is expected but not configured, so an unprotected instance is never silently served:

```bash
MATSTAT_PASSWORD="StrongPassword" nix run .#
```

Authentication knobs:

-   `MATSTAT_AUTH=off` — disable authentication entirely (trusted LAN/local only).
-   `MATSTAT_SESSION_HOURS=12` — how long a login stays valid (default 12 hours). Only **one session is active at a time**; a second login while one is active is rejected until the first is logged out or expires.
-   `MATSTAT_SECRET=...` — optional cookie-signing key. Set it so changing `MATSTAT_PASSWORD` does not invalidate sessions; leave unset to key cookies off the password.
-   `MATSTAT_SECURE_COOKIE=1` — set the `Secure` attribute on the session cookie (required over HTTPS).

## Runtime configuration

The package launcher reads these optional environment variables:

-   `MATSTAT_ROOT=/path` — data root for `cfg/`, `vids/`, and `stats/` (defaults to the current directory). The server `chdir`s here, so every path below is relative to it; browser tests use it to isolate their temp tree.
-   `MATSTAT_HOST=127.0.0.1` / `MATSTAT_PORT=8000` — bind address and port (defaults shown). Keep the host on loopback when a reverse proxy is in front.
-   `MATSTAT_FORWARDED_ALLOW_IPS=127.0.0.1` — proxy addresses trusted to set `X-Forwarded-*`. Keep this to loopback when the proxy is local; it is what makes login rate limiting, logs, and HTTPS detection see the real client.
-   `MATSTAT_MAX_UPLOAD_MB=4096` / `MATSTAT_MAX_REQUEST_MB=8192` — per-file and total upload caps. Oversized requests are rejected with `413`; oversized files fail individually without entering the pool.
-   `MATSTAT_MAX_UPLOAD_FILES=20` — maximum files in one upload request.
-   `MATSTAT_ALLOWED_EXTENSIONS=.mkv,.mp4,.mov,.webm,.m4v` — accepted upload extensions (case-insensitive).
-   `MATSTAT_MIN_FREE_MB=1024` — free-space reserve that uploads may not consume.
-   `MATSTAT_HSTS_MAX_AGE=31536000` — HSTS max-age sent over HTTPS; set `0` to disable.
-   `MATSTAT_VISIBLE_LOGGING=1` — show the in-browser log panel (hidden by default).

Uploads are additionally validated with `ffprobe` before they join the untagged pool, so renaming a non-video file to `.mkv` does not work.

### Health and readiness probes

Two unauthenticated probes are available for reverse proxies and service managers:

-   `GET /healthz` — liveness; always `{"status":"ok"}` while the process serves HTTP.
-   `GET /readyz` — readiness; checks the data root is writable, the config loads, and `ffmpeg` is on `PATH`. Returns `200` when ready and `503` with per-check status otherwise. It never exposes paths or roster data.

The SPA's own `/api/health` endpoint remains behind the authentication gate.

### Browser hardening

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, and a same-origin `Content-Security-Policy` (scripts must be external files; inline styles stay allowed for Plotly). HSTS is added automatically when the request arrives over HTTPS. Cross-origin `POST`/`PUT`/`DELETE` requests are rejected with `403`, complementing the `SameSite=Lax` session cookie.

## Deployment

### Deployment targets

MatStat ships in two forms:

-   **Nix flake package** (`packages.default`, `apps.default`) for Linux and macOS. Use it with `nix run .#` for a quick run, or install the built binary as a service on a non-NixOS host.
-   **NixOS module** (`nixosModules.default`) for servers. It runs MatStat as a hardened systemd service, creates and seeds the data tree, and can provision Caddy with automatic HTTPS.

There is no OCI/Docker image and no PyPI package. A Nix installation with flakes enabled is required for the packaged deployment. `nix run .#` is a quick foreground run that uses the current directory as its data root; for a persistent server, use the module or a dedicated data root.

### Deploy on NixOS with the flake

Add the MatStat flake as an input and enable the module in your host configuration:

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
          # Caddy's own firewall switch is off by default in nixpkgs.
          services.caddy.openFirewall = true;
        }
      ];
    };
  };
}
```

`passwordFile` is a systemd `EnvironmentFile` that must exist at runtime. At minimum it contains the login password; add `MATSTAT_SECRET` to keep sessions valid when the password changes:

```bash
MATSTAT_PASSWORD=StrongPassword
MATSTAT_SECRET=change-me-to-a-long-random-string
```

Do **not** place secrets in the Nix store. Materialize the file with a secrets manager (sops-nix, agenix, etc.) or create it as root:

```bash
sudo install -d -m 0750 /etc/matstat
sudo install -m 0600 /dev/null /etc/matstat/matstat.env
sudoedit /etc/matstat/matstat.env
```

Before deploying:

-   Point the `virtualHost` DNS name (A/AAAA) at the host.
-   Open ports 80 and 443 for Caddy (`services.caddy.openFirewall = true`, or `networking.firewall.allowedTCPPorts = [ 80 443 ]`).
-   Keep `host = "127.0.0.1"` (the default) so the app is reachable only through Caddy. Do not open the app port.

Then deploy and verify:

```bash
sudo nixos-rebuild switch --flake .#host

systemctl status matstat
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS https://matstat.example.com/readyz
journalctl -u matstat -f
```

The module keeps data in `stateDir` (`/var/lib/matstat` by default): `cfg/`, `vids/`, `stats/`, `tmp/`, and `MatStat.log`. On first start it creates the tree and copies the packaged default `cfg/` files only when they are absent, so upgrades never overwrite your rulesets or roster.

Module options:

-   `stateDir` — writable data root (default `/var/lib/matstat`).
-   `user` / `group` — service account that owns the data (default `matstat`).
-   `host` / `port` — bind address (default `127.0.0.1:8000`); keep it local when using Caddy.
-   `virtualHost` — Caddy virtual host; enables Caddy and defaults `secureCookie` to `true`. Caddy also enforces `maxRequestMb` with `request_body`.
-   `openFirewall` — open the app port directly (off by default; prefer the proxy).
-   `forwardedAllowIps` — proxy addresses trusted for `X-Forwarded-*` (default `127.0.0.1,::1`).
-   `maxUploadMb`, `maxRequestMb`, `maxUploadFiles`, `minFreeMb` — upload guards mirrored to the app environment.
-   `auth` — enable password authentication (default `true`). The module refuses to evaluate without `passwordFile`.
-   `passwordFile` — runtime EnvironmentFile with `MATSTAT_PASSWORD` (and optionally `MATSTAT_SECRET`).
-   `sessionHours`, `secureCookie`, `visibleLogging` — corresponding runtime knobs.
-   `extraEnvironment` — extra `MATSTAT_*` variables (for example `{ MATSTAT_HSTS_MAX_AGE = "0"; }`).
-   `package` — override the flake package.

### Deploy on non-NixOS hosts

Build the package once and keep an out-link so the store path stays alive across garbage collection:

```bash
# Root needs flakes enabled (or pass the experimental flags explicitly).
sudo nix --extra-experimental-features 'nix-command flakes' \
  build .#default -o /var/lib/matstat-pkg
```

Create the service account and data tree, then seed the packaged default config without overwriting existing files:

```bash
sudo useradd --system --home /var/lib/matstat --shell /usr/sbin/nologin matstat
sudo install -d -o matstat -g matstat -m 0750 \
  /var/lib/matstat/{cfg,vids/untaged,vids/taged,vids/clips,stats/wrestler_data,stats/reports,tmp}
sudo cp -n /var/lib/matstat-pkg/share/matstat/cfg/* /var/lib/matstat/cfg/
```

Create the environment file (root-owned; systemd reads it before dropping privileges):

```bash
sudo install -d -m 0750 /etc/matstat
sudo install -m 0600 /dev/null /etc/matstat/matstat.env
sudoedit /etc/matstat/matstat.env   # MATSTAT_PASSWORD=... and optional MATSTAT_SECRET=...
```

An example unit at `/etc/systemd/system/matstat.service`:

```ini
[Unit]
Description=MatStat wrestling analysis web app
After=network.target

[Service]
Type=simple
User=matstat
Group=matstat
WorkingDirectory=/var/lib/matstat
EnvironmentFile=/etc/matstat/matstat.env
Environment=MATSTAT_ROOT=/var/lib/matstat
Environment=MATSTAT_HOST=127.0.0.1
Environment=MATSTAT_PORT=8000
Environment=MATSTAT_SECURE_COOKIE=1
Environment=MATSTAT_FORWARDED_ALLOW_IPS=127.0.0.1
ExecStart=/var/lib/matstat-pkg/bin/matstat
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/matstat

[Install]
WantedBy=multi-user.target
```

Set `MATSTAT_SECURE_COOKIE=0` only if you are deliberately testing over plain HTTP; the browser will not send a `Secure` cookie over HTTP, which looks like a login loop. Enable and verify:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now matstat
curl -fsS http://127.0.0.1:8000/readyz
```

The packaged launcher adds `--proxy-headers`, reads `MATSTAT_HOST`, `MATSTAT_PORT`, `MATSTAT_ROOT`, and `MATSTAT_FORWARDED_ALLOW_IPS`, and embeds its Python path. Run it with `MATSTAT_ROOT` set rather than relying on the current directory.

### Reverse proxy and TLS

MatStat is single-tenant and password-only, so expose it **behind HTTPS**. The proxy must preserve the public `Host` header and set `X-Forwarded-Proto`; the app compares `Origin` with `Host` for cross-origin write protection, and uses the forwarded scheme for secure cookies and HSTS. Set `MATSTAT_FORWARDED_ALLOW_IPS` to the proxy's address (never a wildcard unless the proxy is fully trusted), and keep WebSocket upgrade headers enabled for `/ws`.

**Caddy** (automatic TLS; this is what the NixOS module configures):

```caddyfile
matstat.example.com {
    request_body {
        max_size 8GB
    }
    reverse_proxy 127.0.0.1:8000
}
```

**Nginx** (with your certificate paths; the `map` belongs in the `http {}` block):

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl http2;
    server_name matstat.example.com;
    client_max_body_size 8192m;

    ssl_certificate     /etc/ssl/certs/matstat.crt;
    ssl_certificate_key /etc/ssl/private/matstat.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

Align `client_max_body_size` / Caddy's `request_body` with `MATSTAT_MAX_REQUEST_MB`; otherwise the proxy rejects large uploads with `413` before MatStat sees them.

**Direct uvicorn TLS** is also possible on a single host:

```bash
MATSTAT_HOST=0.0.0.0 MATSTAT_SECURE_COOKIE=1 \
MATSTAT_PASSWORD=... ./result/bin/matstat \
  --ssl-keyfile /etc/ssl/private/matstat.key \
  --ssl-certfile /etc/ssl/certs/matstat.crt
```

With direct TLS, HSTS is emitted because the request scheme is `https`; with a reverse proxy it is emitted when the trusted proxy supplies `X-Forwarded-Proto: https`.

> MatStat runs a single process: jobs, cancellation, sessions, and in-flight transcodes are held in memory. Do not add multiple uvicorn workers.

> `MATSTAT_AUTH=off` is only appropriate on a trusted LAN. It disables the login page entirely; never combine it with a public listener.

### Operations: logs, upgrades, and rollback

-   **Logs:** `journalctl -u matstat -f` on systemd hosts, plus `MatStat.log` in the data root. The in-browser log panel is opt-in with `MATSTAT_VISIBLE_LOGGING=1` (module: `visibleLogging = true`).
-   **NixOS upgrades:** update the input (`nix flake update matstat` in the host flake), then `sudo nixos-rebuild switch --flake .#host`. Roll back with `sudo nixos-rebuild switch --rollback` (or select the previous generation at boot).
-   **Non-NixOS upgrades:** pull the new source, run `sudo nix --extra-experimental-features 'nix-command flakes' build .#default -o /var/lib/matstat-pkg`, then `sudo systemctl restart matstat`.
-   **Data survives upgrades:** the module never overwrites existing `cfg/` files; new packaged defaults are copied only when absent. Back up `cfg/` before editing rulesets or the roster.
-   **Health checks:** point your monitoring at `/healthz` for liveness and `/readyz` for readiness; use the `503` response and its per-check body to diagnose startup problems.

### Backup and restore

The deployment's irreplaceable data is `cfg/` (rulesets and the PII roster) and `stats/` (compiled wrestler CSVs and reports). `vids/` is large media; back it up according to your retention policy, and expect to re-transcode previews after a restore since `tmp/` is scratch.

A filesystem-consistent backup is easiest with the service stopped (`STATE_DIR` is the module's `stateDir` or your `MATSTAT_ROOT`):

```bash
STATE_DIR=/var/lib/matstat
sudo systemctl stop matstat
sudo tar -C "$STATE_DIR" -czf /backup/matstat-$(date +%F).tar.gz cfg stats vids
sudo systemctl start matstat
curl -fsS http://127.0.0.1:8000/readyz
```

For recurring backups, point restic/borg at `$STATE_DIR` and exclude `tmp/` and `MatStat.log`. If you cannot stop the service, snapshot the filesystem or accept that an upload or reel being written at that moment may be partial.

Restore into a fresh host:

```bash
STATE_DIR=/var/lib/matstat
sudo systemctl stop matstat
sudo tar -C "$STATE_DIR" -xzf /backup/matstat-YYYY-MM-DD.tar.gz
sudo chown -R matstat:matstat "$STATE_DIR"
sudo systemctl start matstat
```

Then confirm `/readyz` returns `200`, open the app, and check that **Match Outcomes**/reports show the expected date range. Restoring `cfg/` before the first start prevents the module's default-config seeding from racing your restore.

### Troubleshooting

-   **`readyz` returns `503`:** check the `checks` object. `data_root: error` is usually ownership or permissions on `stateDir`; `ffmpeg: error` means `ffmpeg` is not on the service's `PATH` (the packaged launcher supplies it); `config: error` means an unreadable or malformed `cfg/config.json`.
-   **`502 Bad Gateway`:** the proxy cannot reach the app. Confirm `systemctl status matstat` and that the proxy dials the same `host:port` (`127.0.0.1:8000` by default).
-   **Login loops or the cookie is not stored:** you are serving over HTTP with `MATSTAT_SECURE_COOKIE=1`, or the proxy strips `X-Forwarded-Proto`. Use HTTPS (or set the cookie flag to `0` for a deliberate HTTP-only test).
-   **`403 Cross-origin request rejected`:** the proxy rewrote `Host` or you opened the app under a different hostname than the one in `Origin`. Preserve the public `Host` header and use one canonical hostname.
-   **Uploads return `413`:** align the proxy body limit (`request_body` / `client_max_body_size`) with `MATSTAT_MAX_REQUEST_MB`; the per-file cap is `MATSTAT_MAX_UPLOAD_MB`.
-   **`401` on most URLs:** expected when auth is on. Only `/healthz`, `/readyz`, `/login`, `/static/js/login.js`, and the login/logout endpoints are public.
-   **Data appears empty after moving the instance:** `MATSTAT_ROOT` (or `WorkingDirectory`) points at the wrong directory. The app `chdir`s to the data root before resolving `cfg/`, `vids/`, and `stats/`.
-   **Uploads fail with a free-space error:** `MATSTAT_MIN_FREE_MB` reserves free space; lower it or add disk capacity.

## Module Overview

-   **`helpers.py`**: Contains shared data classes (`ChapterSequence`) and functions used by the web app for video processing, data formatting, and statistical calculation. Also contains many useful functions for analyzing the csv files in a Jupyter Notebook.
-   **`scripts/web/`**: The FastAPI web app (app.py, ffmpeg.py, jobs.py, ws.py, transcode.py, and the tab modules tag/stats/team_eval/clips/search/pca/trend/audit).

## Configuration

Customize the tagging options via JSON files in the `cfg/` directory (or the structured editors in the web Config Editor):

-   `config.json` (committed): Holds `active_ruleset`, `moves`, `ties`, and per-ruleset `rulesets` (each with `outcomes: {code: {points, description, counts_as_pin}}`). Seeded with Folkstyle (real values) plus Freestyle and Greco-Roman (UWW scoring templates — review before relying on them). Move/ties/outcome lists are shared across rulesets; only the scoring outcomes differ per ruleset.
-   `Wrestlers.json` (gitignored — contains PII): Holds `wrestlers: [names]` and `teams: {team: [names]}`. A wrestler may belong to multiple teams. The committed `Wrestlers.json.example` placeholder (`UNKNOWN` wrestler) is used as a fresh-clone fallback, so **never commit real wrestler names** to the repo.
-   The **active ruleset** determines `score_to_pts` and pin counts used for net-point statistics; it is read when tagged CSVs are compiled, so switch it via the header gear button (web) and re-run Tabulate Sequences to regenerate `stats/wrestler_data/`.
-   All editors (the web Config Editor gear button) support Add, Edit, and Delete with automatic `.bak` backup creation on save.
