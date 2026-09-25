#!/bin/sh
# entrypoint.sh — gateway startup with env-driven platform + MCP config.
#
# Responsibilities (in order):
#
# 1. Resolve HERMES_HOME and create the config dir. The fork's gateway
#    reads ~/.hermes/config.yaml to decide which platforms and MCP
#    servers to attach. HERMES_HOME defaults to ~/.hermes, falling back
#    to /root/.hermes when HOME is unset (matches the Dockerfile's
#    default USER root).
#
# 2. If TELEGRAM_BOT_TOKEN is set, append a minimal `platforms.telegram`
#    block that enables the telegram platform. The fork's PlatformConfig
#    defaults `enabled: False` for every platform — without an explicit
#    `enabled: true` block, the gateway ignores the token even when
#    TELEGRAM_BOT_TOKEN is in the environment.
#
# 3. If any MCP server env var is set (METABASE_API_KEY, GRAFANA_API_KEY,
#    SENTRY_AUTH_TOKEN), append one consolidated `mcp_servers:` block whose
#    children are the server entries whose env var is non-empty. The fork's
#    MCP server registry defaults to off; without explicit YAML entries,
#    the gateway ignores the env vars even when set. The merge matters:
#    if the script wrote a separate top-level `mcp_servers:` block per
#    server, PyYAML last-wins on duplicate keys and any earlier server
#    would silently drop on the next config reload. Consolidating into
#    one block is the only way to keep multiple servers registered.
#
#    Currently supported servers (gated by env var):
#
#    - metabase: HTTP transport to metabase's hosted MCP gateway
#      (`/api/metabase-mcp`). Auth via `X-API-Key` header (Bearer is not
#      accepted by metabase's /api/* endpoints). Gated by METABASE_API_KEY.
#      Added in PR #15.
#
#    - grafana: stdio transport via `mcp-grafana` (pip-installed in
#      Dockerfile). `GRAFANA_URL` and `GRAFANA_API_KEY` flow through the
#      subprocess env. Gated by GRAFANA_API_KEY.
#
#    - sentry: HTTP transport to the Sentry-hosted MCP
#      (`https://mcp.sentry.dev/mcp`). Bearer auth via SENTRY_AUTH_TOKEN.
#      Gated by SENTRY_AUTH_TOKEN.
#
#    Env-driven blocks use `>>` (append), so the platforms block (step 2)
#    and the MCP servers block (step 3) compose into one config.yaml
#    rather than clobbering each other on every entrypoint run.
#
#    Writing the file at startup keeps tokens out of image layers: they
#    live only in Coolify env vars and the rendered file inside the
#    container, which is recreated on every redeploy. File perms are 600
#    so the tokens aren't world-readable inside the container.
#
# 4. If MINIMAX_API_KEY is set, unset ANTHROPIC_API_KEY + ANTHROPIC_TOKEN.
#    The agent's auxiliary client can fall back to Anthropic after a
#    primary-provider error and send MiniMax's key to api.anthropic.com,
#    which rejects it with "Invalid API Key". studio surfaces that as:
#    Skills: HTTP 500. Stripping the conflicting creds is the fix.
#
# 5. exec the gateway (hermes gateway run).

set -e

# 1. HERMES_HOME
: "${HERMES_HOME:=${HOME:-/root}/.hermes}"
export HERMES_HOME
mkdir -p "$HERMES_HOME"

# 2 + 3. Render env-driven config blocks. Both writers append, so they
#    compose into one config.yaml. We truncate first if any writer will
#    run so stale contents from a prior container don't leak across
#    redeploys where the env-var set shrinks (e.g. METABASE_API_KEY was
#    set, then unset in Coolify — we want no `mcp_servers:` block at
#    all, not a stale one from the previous container).
if [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$METABASE_API_KEY" ] || [ -n "$GRAFANA_API_KEY" ] || [ -n "$SENTRY_AUTH_TOKEN" ]; then
  : > "$HERMES_HOME/config.yaml"
fi

# 2. Render telegram platform config when TELEGRAM_BOT_TOKEN is set.
#    The platform block is required even if the token is also exported
#    as an env var — see gateway/config.py: PlatformConfig.enabled
#    defaults to False and is only flipped by an explicit YAML entry
#    or the platform's own check_fn().
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  cat >> "$HERMES_HOME/config.yaml" <<YAML
platforms:
  telegram:
    enabled: true
    token: "${TELEGRAM_BOT_TOKEN}"
YAML
fi

# 3. Render MCP server blocks when their env vars are set. All servers
#    share one top-level `mcp_servers:` block; emitting one block per
#    server would let PyYAML last-wins silently drop the earlier entries
#    on the next config reload. The `{ ... } >> file` POSIX group
#    redirects all child command stdout into one append, so the whole
#    block lands as one atomic write to config.yaml.
#
#    Per-server heredocs are gated by their own env var so a partial
#    rollout (only metabase today, grafana next week) renders cleanly —
#    an unset env var means its server is omitted, not stubbed.
#
#    metabase: HTTP transport to metabase's hosted MCP gateway
#      (`/api/metabase-mcp`). Auth via X-API-Key header (Bearer is not
#      accepted by metabase's /api/* endpoints). Added in PR #15.
#
#    grafana: stdio transport — the gateway spawns `mcp-grafana` as a
#      subprocess (installed in the Dockerfile). GRAFANA_URL +
#      GRAFANA_API_KEY flow through the subprocess env so the stdio
#      server can reach grafana.influtics.com.
#
#    sentry: HTTP transport to the Sentry-hosted MCP
#      (`https://mcp.sentry.dev/mcp`). Bearer auth via SENTRY_AUTH_TOKEN.
if [ -n "$METABASE_API_KEY" ] || [ -n "$GRAFANA_API_KEY" ] || [ -n "$SENTRY_AUTH_TOKEN" ]; then
  {
    echo "mcp_servers:"
    [ -n "$METABASE_API_KEY" ] && cat <<EOSERVER
  metabase:
    url: https://metabase.influtics.com/api/metabase-mcp
    headers:
      X-API-Key: "${METABASE_API_KEY}"
EOSERVER
    [ -n "$GRAFANA_API_KEY" ] && cat <<EOSERVER
  grafana:
    command: mcp-grafana
    env:
      GRAFANA_URL: https://grafana.influtics.com
      GRAFANA_API_KEY: "${GRAFANA_API_KEY}"
EOSERVER
    [ -n "$SENTRY_AUTH_TOKEN" ] && cat <<EOSERVER
  sentry:
    url: https://mcp.sentry.dev/mcp
    headers:
      Authorization: Bearer ${SENTRY_AUTH_TOKEN}
EOSERVER
  } >> "$HERMES_HOME/config.yaml"
fi

# Lock file perms if any env-driven block was written.
if [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$METABASE_API_KEY" ] || [ -n "$GRAFANA_API_KEY" ] || [ -n "$SENTRY_AUTH_TOKEN" ]; then
  chmod 600 "$HERMES_HOME/config.yaml"
fi

# 4. Drop conflicting Anthropic creds when MiniMax is configured.
if [ -n "$MINIMAX_API_KEY" ]; then
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_TOKEN
fi

# 5. Hand off to the gateway.
exec "$@"
