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
#
# Two stages keep the runtime image minimal: the builder keeps Nix, the
# evaluated flake, and every build input, while the final stage is the same
# minimal NixOS base with only the package's runtime closure imported.
#
# syntax=docker/dockerfile:1
FROM nixos/nix:2.35.6 AS build

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
RUN nix build .#default --print-out-paths > /app/result-path \
 && nix-store --export $(nix-store -qR "$(cat /app/result-path)") \
      > /matstat-closure.nar


FROM nixos/nix:2.35.6

# Import only the runtime closure (Python, ffmpeg, mkvtoolnix, the app), not
# the build inputs the builder used, then point /app/result at the launcher.
# The NAR is bind-mounted from the builder so it never becomes an image layer
# (COPY would keep a second copy of the whole closure in the final image).
COPY --from=build /app/result-path /app/result-path
RUN --mount=type=bind,from=build,source=/matstat-closure.nar,target=/matstat-closure.nar \
    nix-store --import < /matstat-closure.nar \
 && ln -s "$(cat /app/result-path)" /app/result

COPY docker/entrypoint.sh /usr/local/bin/matstat-entrypoint
RUN chmod 0755 /usr/local/bin/matstat-entrypoint

# The data root is the only writable path; mount a volume there.
ENV MATSTAT_ROOT=/data \
    MATSTAT_HOST=0.0.0.0 \
    MATSTAT_PORT=8000

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/matstat-entrypoint"]
