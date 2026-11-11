#!/usr/bin/env bash
# Assert the frozen public contract of the NixOS module. Called at build time
# from nix/compat-check.nix with a JSON snapshot of two evaluated configs.
set -euo pipefail

snapshot=${1:?usage: compat-check.sh <snapshot.json>}

fail() {
  printf 'module compat check failed: %s\n' "$*" >&2
  exit 1
}

get() {
  local value
  value=$(jq -r "$1" "$snapshot")
  [[ $value != 'null' ]] || fail "missing $1"
  printf '%s' "$value"
}

get_allow_null() {
  jq -r "$1" "$snapshot"
}

expect() {
  local path=$1 expected=$2 actual
  actual=$(get "$path")
  [[ $actual == "$expected" ]] || fail "$path is '$actual', expected '$expected'"
}

check_environment() {
  local scenario=$1 key expected actual
  while IFS=$'\t' read -r key expected; do
    actual=$(jq -er --arg k "$key" "$scenario.environment[\$k]" "$snapshot") \
      || fail "$scenario.environment is missing $key"
    [[ $actual == "$expected" ]] \
      || fail "$scenario.environment[$key] is '$actual', expected '$expected'"
  done < <(jq -r '.expected.environment | to_entries[] | "\(.key)\t\(.value)"' "$snapshot")
}

# --- Scenario A: the documented legacy deployment (auth = false) -----------
expect '.legacy.host' '127.0.0.1'
expect '.legacy.port' '8000'
expect '.legacy.stateDir' '/var/lib/matstat'
expect '.legacy.auth' 'false'
expect '.legacy.visibleLogging' 'false'
expect '.legacy.caddyEnable' 'false'
expect '.legacy.caddyHosts | length' '0'
[[ $(get_allow_null '.legacy.environmentFile') == 'null' ]] \
  || fail "legacy deployment should not set EnvironmentFile"
expect '.legacy.execStart' "$(get '.expected.execStart')"
check_environment '.legacy'

# --- Scenario B: Caddy + passwordFile (the proxy upgrade path) -------------
expect '.proxied.host' '127.0.0.1'
expect '.proxied.auth' 'true'
expect '.proxied.environmentFile' '/etc/matstat/matstat.env'
expect '.proxied.caddyEnable' 'true'
expect '.proxied.caddyHosts | length' '1'
expect '.proxied.caddyHosts[0]' 'matstat.example.com'
expect '.proxied.environment.MATSTAT_AUTH' 'on'
expect '.proxied.environment.MATSTAT_SECURE_COOKIE' '1'
expect '.proxied.execStart' "$(get '.expected.execStart')"

# --- Scenario C: IPv6 wildcard bind behind Caddy ---------------------------
expect '.ipv6.host' '::'
expect '.ipv6.environment.MATSTAT_HOST' '::'
expect '.ipv6.caddyEnable' 'true'
proxy=$(get '.ipv6.caddyExtraConfig')
[[ $proxy == *'reverse_proxy [::1]:8000'* ]] \
  || fail "IPv6 wildcard bind should proxy to [::1]:8000, got: $proxy"

# --- Shared invariants -----------------------------------------------------
pre=$(get '.legacy.execStartPre')
[[ $pre == */bin/matstat-seed ]] \
  || fail "ExecStartPre is '$pre', expected the packaged seed script"

for secret in MATSTAT_PASSWORD MATSTAT_SECRET; do
  in_env=$(jq -r --arg k "$secret" '.proxied.environment | has($k)' "$snapshot")
  [[ $in_env == 'false' ]] \
    || fail "$secret must come from passwordFile, not the service environment"
done
