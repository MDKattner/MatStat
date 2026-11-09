{ self, pkgs, python }:

let
  # Copy only the tracked application source and default config into the
  # store. The flake source is git-based, so untracked PII (for example
  # cfg/Wrestlers.json) and video/stat data never end up in the package.
  srcTree = pkgs.runCommand "matstat-source" {
    nativeBuildInputs = [ pkgs.findutils ];
  } ''
    mkdir -p $out/share/matstat
    cp -r ${self}/scripts ${self}/cfg $out/share/matstat/
    find $out/share/matstat -name __pycache__ -type d -prune -exec rm -rf {} +
  '';

  launcher = pkgs.writeShellApplication {
    name = "matstat";
    runtimeInputs = [ python pkgs.ffmpeg pkgs.mkvtoolnix ];
    text = ''
      export PYTHONPATH="${srcTree}/share/matstat''${PYTHONPATH:+:$PYTHONPATH}"
      export MATSTAT_ROOT="''${MATSTAT_ROOT:-$PWD}"
      exec python -m uvicorn scripts.web.app:app \
        --host "''${MATSTAT_HOST:-127.0.0.1}" \
        --port "''${MATSTAT_PORT:-8000}" \
        "$@"
    '';
  };
in
pkgs.symlinkJoin {
  name = "matstat-0.1.0";
  paths = [ srcTree launcher ];
  meta = {
    description = "MatStat wrestling analysis and film generation web app";
    homepage = "https://github.com/MDKattner/MatStat";
    license = pkgs.lib.licenses.gpl3Only;
    mainProgram = "matstat";
    platforms = pkgs.lib.platforms.unix;
  };
}
