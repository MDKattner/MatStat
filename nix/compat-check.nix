{
  self,
  nixpkgs,
  system,
}:

# Evaluates the NixOS module the way an upgrading operator would and pins the
# public contract: option defaults, service environment, and the ExecStart
# wiring. Any removal or changed default fails `nix flake check`.
#
# Scenario A is the documented legacy deployment (`auth = false`); scenario B
# exercises the Caddy + passwordFile path. Extra environment variables are
# allowed, so adding a compatible knob does not trip the check.
let
  lib = nixpkgs.lib;
  pkgs = import nixpkgs { inherit system; };

  evaluate =
    extra:
    lib.nixosSystem {
      inherit system;
      modules = [
        self.nixosModules.default
        {
          services.matstat = {
            enable = true;
          }
          // extra;
        }
      ];
    };

  legacy = evaluate { auth = false; };
  proxied = evaluate {
    passwordFile = "/etc/matstat/matstat.env";
    virtualHost = "matstat.example.com";
  };
  ipv6 = evaluate {
    host = "::";
    passwordFile = "/etc/matstat/matstat.env";
    virtualHost = "matstat.example.com";
  };

  snapshot = config: {
    host = config.services.matstat.host;
    port = config.services.matstat.port;
    stateDir = config.services.matstat.stateDir;
    auth = config.services.matstat.auth;
    visibleLogging = config.services.matstat.visibleLogging;
    execStart = config.systemd.services.matstat.serviceConfig.ExecStart;
    execStartPre = config.systemd.services.matstat.serviceConfig.ExecStartPre;
    environmentFile = config.systemd.services.matstat.serviceConfig.EnvironmentFile or null;
    environment = config.systemd.services.matstat.environment;
    caddyEnable = config.services.caddy.enable;
    caddyHosts = builtins.attrNames config.services.caddy.virtualHosts;
    caddyExtraConfig =
      if config.services.matstat.virtualHost == null then
        ""
      else
        config.services.caddy.virtualHosts.${config.services.matstat.virtualHost}.extraConfig;
  };

  payload = {
    expected = {
      execStart = "${self.packages.${system}.default}/bin/matstat";
      environment = {
        MATSTAT_ROOT = "/var/lib/matstat";
        MATSTAT_HOST = "127.0.0.1";
        MATSTAT_PORT = "8000";
        MATSTAT_AUTH = "off";
        MATSTAT_SESSION_HOURS = "12";
        MATSTAT_SECURE_COOKIE = "0";
        MATSTAT_VISIBLE_LOGGING = "0";
        MATSTAT_FORWARDED_ALLOW_IPS = "127.0.0.1,::1";
        MATSTAT_MAX_UPLOAD_MB = "4096";
        MATSTAT_MAX_REQUEST_MB = "8192";
        MATSTAT_MIN_FREE_MB = "1024";
        MATSTAT_MAX_UPLOAD_FILES = "20";
      };
    };
    legacy = snapshot legacy.config;
    proxied = snapshot proxied.config;
    ipv6 = snapshot ipv6.config;
  };
in
pkgs.runCommand "matstat-module-compat"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.jq
    ];
  }
  ''
    cat > snapshot.json <<'EOF'
    ${builtins.toJSON payload}
    EOF
    bash ${./compat-check.sh} snapshot.json
    touch $out
  ''
