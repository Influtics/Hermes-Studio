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
# 3. If METABASE_API_KEY is set, append an `mcp_servers.metabase` block.
#    The fork's MCP server registry defaults to off; without an explicit
#    YAML entry, the gateway ignores the env var even when set. HTTP
#    transport; X-API-Key is metabase's native auth (Bearer not accepted
#    by /api/* endpoints).
#
#    Env-driven blocks in this file use `>>` (append), not `>` (overwrite),
#    so multiple platforms / MCP servers stack into one config.yaml rather
#    than clobbering each other on every entrypoint run.
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
if [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$METABASE_API_KEY" ]; then
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

# 3. Render metabase MCP server block when METABASE_API_KEY is set.
#    HTTP transport — the gateway connects to the metabase MCP endpoint
#    on first tool call.
if [ -n "$METABASE_API_KEY" ]; then
  cat >> "$HERMES_HOME/config.yaml" <<YAML
mcp_servers:
  metabase:
    url: https://metabase.influtics.com/api/metabase-mcp
    headers:
      X-API-Key: "${METABASE_API_KEY}"
YAML
fi

# Lock file perms if any env-driven block was written.
if [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$METABASE_API_KEY" ]; then
  chmod 600 "$HERMES_HOME/config.yaml"
fi

# 4. Drop conflicting Anthropic creds when MiniMax is configured.
if [ -n "$MINIMAX_API_KEY" ]; then
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_TOKEN
fi

# 5. Hand off to the gateway.
exec "$@"
