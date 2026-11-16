# MatStat container: build the flake, then run the packaged launcher.
#
# This is a deployment path for non-Nix hosts only. On NixOS use
# nixosModules.default; on other hosts with Nix, `nix build .#default` is
# simpler. Build from the repository root (see wiki/Deployment.md):
#
#   docker build -t matstat:0.2.2 .
#
# .dockerignore keeps real rosters, media, stats, and build output out of the
# image. The container is single-process on purpose: jobs, sessions, and
# transcodes live in memory, so do not run more than one instance against the
# same data volume.
FROM nixos/nix:2.35.6

# Enable flakes for `nix build .#default`. The Nix sandbox cannot create its
# user/mount namespaces in an unprivileged Docker builder, so it is disabled;
# the container already provides the isolation boundary.
RUN printf '%s\n' \
      'experimental-features = nix-command flakes' \
      'sandbox = false' \
      >> /etc/nix/nix.conf

WORKDIR /app
COPY flake.nix flake.lock VERSION ./
COPY nix ./nix
COPY scripts ./scripts
COPY cfg ./cfg
RUN nix build .#default --out-link /app/result

COPY docker/entrypoint.sh /usr/local/bin/matstat-entrypoint
RUN chmod 0755 /usr/local/bin/matstat-entrypoint

# The data root is the only writable path; mount a volume there.
ENV MATSTAT_ROOT=/data \
    MATSTAT_HOST=0.0.0.0 \
    MATSTAT_PORT=8000

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/matstat-entrypoint"]
