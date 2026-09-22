#!/bin/sh
# entrypoint.sh — gateway startup with env-driven platform config.
#
# Responsibilities (in order):
#
# 1. Resolve HERMES_HOME and create the config dir. The fork's gateway
#    reads ~/.hermes/config.yaml to decide which platforms to attach.
#    HERMES_HOME defaults to ~/.hermes, falling back to /root/.hermes
#    when HOME is unset (matches the Dockerfile's default USER root).
#
# 2. If TELEGRAM_BOT_TOKEN is set, render a minimal config.yaml that
#    enables the telegram platform. The fork's PlatformConfig defaults
#    `enabled: False` for every platform — without an explicit
#    `enabled: true` block, the gateway ignores the token even when
#    TELEGRAM_BOT_TOKEN is in the environment. Writing the file at
#    startup keeps the token out of image layers: it lives only in
#    Coolify env vars and the rendered file inside the container,
#    which is recreated on every redeploy. File perms are 600 so the
#    token isn't world-readable inside the container.
#
# 3. If MINIMAX_API_KEY is set, unset ANTHROPIC_API_KEY + ANTHROPIC_TOKEN.
#    The agent's auxiliary client can fall back to Anthropic after a
#    primary-provider error and send MiniMax's key to api.anthropic.com,
#    which rejects it with "Invalid API Key". studio surfaces that as:
#    Skills: HTTP 500. Stripping the conflicting creds is the fix.
#
# 4. exec the gateway (hermes gateway run).

set -e

# 1. HERMES_HOME
: "${HERMES_HOME:=${HOME:-/root}/.hermes}"
export HERMES_HOME
mkdir -p "$HERMES_HOME"

# 2. Render telegram platform config when TELEGRAM_BOT_TOKEN is set.
#    The platform block is required even if the token is also exported
#    as an env var — see gateway/config.py: PlatformConfig.enabled
#    defaults to False and is only flipped by an explicit YAML entry
#    or the platform's own check_fn().
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  cat > "$HERMES_HOME/config.yaml" <<YAML
platforms:
  telegram:
    enabled: true
    token: "${TELEGRAM_BOT_TOKEN}"
YAML
  chmod 600 "$HERMES_HOME/config.yaml"
fi

# 3. Drop conflicting Anthropic creds when MiniMax is configured.
if [ -n "$MINIMAX_API_KEY" ]; then
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_TOKEN
fi

# 4. Hand off to the gateway.
exec "$@"
