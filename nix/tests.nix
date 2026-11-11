{
  self,
  pkgs,
  python,
}:

# Run the fast (non-browser) pytest suite against the flake source. The
# browser suite is opt-in and needs Playwright + Chromium, so it stays in the
# development shell rather than the sandboxed check. Only the trees pytest
# needs are copied, which keeps the check from hashing media-sized assets.
pkgs.runCommand "matstat-tests"
  {
    nativeBuildInputs = [
      python
      pkgs.ffmpeg
      pkgs.mkvtoolnix
    ];
  }
  ''
    mkdir source
    cp -r ${self}/scripts ${self}/cfg ${self}/tests source/
    cp ${self}/pytest.ini ${self}/VERSION source/
    chmod -R u+w source
    cd source
    export HOME=$TMPDIR
    python -m pytest -q
    touch $out
  ''
