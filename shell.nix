{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    # ── Python runtime ──
    python3

    # ── Python packages (PyPI equivalents) ──
    python3Packages.pandas
    python3Packages.numpy
    python3Packages.openpyxl       # Excel writer (.xlsx)
    python3Packages.fastapi        # Web backend (scripts/web)
    python3Packages.uvicorn        # ASGI server for the web app
    python3Packages.python-multipart  # Multipart uploads (web video upload)
    python3Packages.httpx          # FastAPI TestClient
    python3Packages.plotly          # Interactive PCA figures (web)

    # ── Python dev / test ──
    python3Packages.pytest
    python3Packages.pytest-mock
    pipenv

    # ── System tools ──
    ffmpeg                         # Includes ffprobe
  ];

  shellHook = ''
    echo ""
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║         MatStat Dev Environment           ║"
    echo "  ║         Python ${pkgs.python3.version}                    ║"
    echo "  ╚═══════════════════════════════════════════╝"
    echo ""
    echo "  MATSTAT_AUTH=off uvicorn scripts.web.app:app   Web app (auth off, http://127.0.0.1:8000)"
    echo "  pytest                                        Tests"
    echo ""
  '';
}
