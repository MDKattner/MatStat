"""MatStat web application (FastAPI backend + static SPA frontend).

The web app replaces the Qt6 GUI as the supported frontend. It reuses
``scripts.helpers`` for all data/video logic and adds:

- ``ffmpeg.py``     — run ffmpeg with parsed progress + cancellation.
- ``jobs.py``       — background job runner (thread pool) with progress events.
- ``ws.py``         — WebSocket hub bridging logging + job events to the browser.
- ``transcode.py``  — progressive HLS previews for browser playback.
- ``app.py``        — FastAPI application entry point.
"""
