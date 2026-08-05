{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    # ── Python runtime ──
    python3

    # ── Python packages (PyPI equivalents) ──
    python3Packages.pyqt6          # PyQt6 + QtMultimedia + QtMultimediaWidgets
    python3Packages.pandas
    python3Packages.numpy
    python3Packages.openpyxl       # Excel writer (.xlsx)
    python3Packages.pyfiglet       # Banner text in Run.sh preview (DEPRECATED — CLI-only, remove with CLI)

    # ── Python dev / test ──
    python3Packages.pytest
    python3Packages.pytest-mock
    pipenv

    # ── System tools ──
    ffmpeg                         # Includes ffprobe
    fzf                            # DEPRECATED — CLI-only (Run.sh menu), remove with CLI

    # ── Qt Multimedia backends (required for video preview) ──
    qt6.qtmultimedia               # Qt Multimedia + GStreamer/FFmpeg plugin .so files
    pipewire                       # PipeWire backend
    gst_all_1.gstreamer            # GStreamer core
    gst_all_1.gst-plugins-base     # Essential GStreamer plugins
    gst_all_1.gst-plugins-good     # Good-quality plugins (common codecs)
    gst_all_1.gst-libav            # FFmpeg-based GStreamer plugin (broad codec support)
  ];

  QT_PLUGIN_PATH = "${pkgs.qt6.qtmultimedia}/lib/qt-6/plugins";

  shellHook = ''
    export QT_PLUGIN_PATH="${pkgs.qt6.qtmultimedia}/lib/qt-6/plugins:$QT_PLUGIN_PATH"
    echo ""
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║         MatStat Dev Environment           ║"
    echo "  ║         Python ${pkgs.python3.version} + Qt6              ║"
    echo "  ╚═══════════════════════════════════════════╝"
    echo ""
    echo "  python scripts/qt_app/main.py     Qt6 GUI"
    echo "  pytest                            Tests"
    echo "  ./Run.sh                          Headless TUI"
    echo ""
  '';
}
