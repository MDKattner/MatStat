{ self }:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.matstat;
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;

  secureCookie =
    if cfg.secureCookie == null then cfg.virtualHost != null else cfg.secureCookie;

  # Caddy should dial loopback when the app binds to a wildcard address.
  proxyHost = if cfg.host == "0.0.0.0" || cfg.host == "::" then "127.0.0.1" else cfg.host;

  # Create the writable data tree and copy in the packaged defaults exactly
  # once. Existing user config is never overwritten on upgrade.
  seedScript = pkgs.writeShellApplication {
    name = "matstat-seed";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      install -d -m 0750 \
        "${cfg.stateDir}/cfg" \
        "${cfg.stateDir}/vids/untaged" \
        "${cfg.stateDir}/vids/taged" \
        "${cfg.stateDir}/vids/clips" \
        "${cfg.stateDir}/stats/wrestler_data" \
        "${cfg.stateDir}/stats/reports" \
        "${cfg.stateDir}/tmp"

      for src in "${cfg.package}/share/matstat/cfg"/*; do
        [ -e "$src" ] || continue
        dest="${cfg.stateDir}/cfg/$(basename "$src")"
        if [ ! -e "$dest" ]; then
          cp "$src" "$dest"
        fi
      done
    '';
  };
in
{
  options.services.matstat = {
    enable = lib.mkEnableOption "the MatStat wrestling analysis web app";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      description = "MatStat package to run.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "matstat";
      description = "User that owns and runs the service.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "matstat";
      description = "Group that owns and runs the service.";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/matstat";
      description = ''
        Writable data root. Holds `cfg/`, `vids/`, `stats/`, `tmp/`, and
        `MatStat.log`.
      '';
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address the app binds to. Keep the default when using a reverse proxy.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port the app listens on.";
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the app port in the firewall. Prefer a reverse proxy instead.";
    };

    auth = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable password authentication (MATSTAT_AUTH).";
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/matstat.env";
      description = ''
        Path to a systemd EnvironmentFile containing `MATSTAT_PASSWORD=...`.
        Required when {option}`services.matstat.auth` is enabled. Use a
        runtime path (not a Nix path literal) so the secret is not copied
        into the store.
      '';
    };

    sessionHours = lib.mkOption {
      type = lib.types.ints.positive;
      default = 12;
      description = "How long a login stays valid.";
    };

    secureCookie = lib.mkOption {
      type = lib.types.nullOr lib.types.bool;
      default = null;
      description = ''
        Set the `Secure` cookie attribute. Defaults to `true` when
        {option}`services.matstat.virtualHost` is set.
      '';
    };

    virtualHost = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "matstat.example.com";
      description = ''
        Optional Caddy virtual host. Setting this enables Caddy and proxies
        the host to the local MatStat port over HTTPS.
      '';
    };

    visibleLogging = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Show the in-browser log panel (MATSTAT_VISIBLE_LOGGING).";
    };

    extraEnvironment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = "Extra environment variables for the service.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = !cfg.auth || cfg.passwordFile != null;
        message = "services.matstat.passwordFile must be set when authentication is enabled.";
      }
    ];

    warnings =
      lib.optional (cfg.openFirewall && !cfg.auth)
        "services.matstat is exposed on the firewall with authentication disabled."
      ++ lib.optional (cfg.openFirewall && (cfg.host == "127.0.0.1" || cfg.host == "::1"))
        "services.matstat.openFirewall is enabled but host is loopback-only; set host = \"0.0.0.0\" to expose the app.";

    users.groups.${cfg.group} = { };

    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.stateDir;
      description = "MatStat service user";
    };

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0750 ${cfg.user} ${cfg.group} -"
    ];

    systemd.services.matstat = {
      description = "MatStat wrestling analysis web app";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      environment = {
        MATSTAT_ROOT = cfg.stateDir;
        MATSTAT_HOST = cfg.host;
        MATSTAT_PORT = toString cfg.port;
        MATSTAT_AUTH = if cfg.auth then "on" else "off";
        MATSTAT_SESSION_HOURS = toString cfg.sessionHours;
        MATSTAT_SECURE_COOKIE = if secureCookie then "1" else "0";
        MATSTAT_VISIBLE_LOGGING = if cfg.visibleLogging then "1" else "0";
      } // cfg.extraEnvironment;

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.stateDir;
        ExecStartPre = "${seedScript}/bin/matstat-seed";
        ExecStart = "${cfg.package}/bin/matstat";
        Restart = "on-failure";
        RestartSec = 3;
        TimeoutStopSec = 30;
        SyslogIdentifier = "matstat";

        # MatStat is a single-process app: jobs, cancellations, sessions, and
        # in-flight transcodes live in memory. Do not add workers.
        EnvironmentFile = lib.mkIf (cfg.passwordFile != null) cfg.passwordFile;

        # Hardening: the store is read-only, only the data root is writable.
        UMask = "0077";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectClock = true;
        ProtectControlGroups = true;
        ProtectHostname = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        ReadWritePaths = [ cfg.stateDir ];
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];

    services.caddy = lib.mkIf (cfg.virtualHost != null) {
      enable = true;
      virtualHosts.${cfg.virtualHost}.extraConfig = ''
        reverse_proxy ${proxyHost}:${toString cfg.port}
      '';
    };
  };
}
