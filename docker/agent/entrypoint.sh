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
#    SENTRY_AUTH_TOKEN, GITHUB_TOKEN, POSTHOG_API_KEY), append one consolidated `mcp_servers:`
#    block whose children are the server entries whose env var is non-empty.
#    The fork's MCP server registry defaults to off; without explicit YAML
#    entries, the gateway ignores the env vars even when set. The merge
#    matters: if the script wrote a separate top-level `mcp_servers:` block
#    per server, PyYAML last-wins on duplicate keys and any earlier server
#    would silently drop on the next config reload. Consolidating into one
#    block is the only way to keep multiple servers registered.
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
#    - posthog: HTTP transport to the PostHog-hosted MCP
#      (`https://mcp.posthog.com/mcp`). Auth via a personal API key
#      (`phx_…`) as `Authorization: Bearer` — the same header shape as
#      sentry. The hosted gateway normally authenticates by OAuth, which a
#      headless container cannot complete, so the API key is the only
#      workable path here. Gated by POSTHOG_API_KEY.
#      POSTHOG_ORGANIZATION_ID / POSTHOG_PROJECT_ID are optional and pin the
#      server to one org/project via the `x-posthog-organization-id` /
#      `x-posthog-project-id` headers. Pinning removes the
#      `switch-organization` / `switch-project` tools, so a pinned agent
#      cannot wander into another project of the same account. Unset means
#      account-wide access — the key's own permissions are then the only
#      boundary. Added in PR #22.
#
#    - github: stdio transport via `npx -y @modelcontextprotocol/server-github`
#      (uses the bundled Node 22 — see Dockerfile's xz-utils rationale). The
#      subprocess talks directly to api.github.com using a GitHub PAT exposed
#      as `GITHUB_PERSONAL_ACCESS_TOKEN`. We deliberately avoid GitHub's
#      hosted `api.githubcopilot.com/mcp/` endpoint: it historically tied to
#      a Copilot subscription and a separate auth layer that might filter
#      orgs; the stdio server hits api.github.com with whatever scopes the
#      PAT has, so "Influtics org access" is purely a token-scoping question.
#      Gated by GITHUB_TOKEN. First call triggers an `npx` package fetch
#      (cached after), so add ~5-10 s of warm-up to the first MCP tool call.
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
# 4. If HERMES_MODEL is set, render the `model:` block. This is the single
#    knob that makes every platform — web UI and Telegram alike — use the
#    same model. Two reasons it has to live in config.yaml:
#
#    - hermes-agent stopped reading LLM_MODEL from .env (see its .env.example:
#      "LLM_MODEL is no longer read from .env"). The model is read from
#      config.yaml's `model.default` only. An LLM_MODEL env var is a no-op.
#
#    - API_SERVER_MODEL_NAME (gateway/config.py) binds a model to
#      Platform.API_SERVER alone, so setting it fixes the web path and leaves
#      Telegram on the provider profile's own default. That is exactly the
#      split where web reports MiniMax-M3 and Telegram reports MiniMax-M2.7.
#      `model.default` is platform-agnostic and closes it at the source.
#
#    provider and default are read as separate keys (run.py: `_model_cfg.get
#    ("provider")`, `.get("default")`), so HERMES_MODEL is the bare model id
#    and HERMES_MODEL_PROVIDER is the provider name.
#
#    context_length overrides the agent's hardcoded MiniMax entry. In
#    agent/model_metadata.py the provider defaults to 204,800 ("official
#    docs: 204,800 context for all models"), and unmapped models fall back to
#    DEFAULT_FALLBACK_CONTEXT = 256,000. Neither is 512k, so without an
#    explicit value the agent's own budget disagrees with the real window.
#    This key is read as `model.context_length` (run.py) and takes precedence
#    over the metadata table.
#
# 5. If MINIMAX_API_KEY is set, unset ANTHROPIC_API_KEY + ANTHROPIC_TOKEN.
#    The agent's auxiliary client can fall back to Anthropic after a
#    primary-provider error and send MiniMax's key to api.anthropic.com,
#    which rejects it with "Invalid API Key". studio surfaces that as:
#    Skills: HTTP 500. Stripping the conflicting creds is the fix.
#
# 6. exec the gateway (hermes gateway run).

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
#
#    CONFIG_RENDERED is the single "did anything get written" flag: the
#    truncate here and the chmod at the end both need that answer, and
#    restating the full env-var list twice was the part of this file most
#    likely to drift out of sync when a server is added.
CONFIG_RENDERED=""
if [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$METABASE_API_KEY" ] || [ -n "$GRAFANA_API_KEY" ] || [ -n "$SENTRY_AUTH_TOKEN" ] || [ -n "$GITHUB_TOKEN" ] || [ -n "$POSTHOG_API_KEY" ] || [ -n "$HERMES_MODEL" ]; then
  CONFIG_RENDERED=1
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
#
#    github: stdio transport — the gateway spawns `npx -y
#      @modelcontextprotocol/server-github` as a subprocess. The bundled
#      Node 22 (xz-utils rationale in Dockerfile) provides `npx`. The PAT
#      flows through as `GITHUB_PERSONAL_ACCESS_TOKEN` — the env var name
#      the stdio server reads.
#
#    posthog: HTTP transport — PostHog's hosted MCP at
#      `https://mcp.posthog.com/mcp`, Bearer-authenticated with a personal
#      API key. The optional org/project pins are emitted as sibling
#      headers under the same `headers:` key, so they must land AFTER the
#      `Authorization:` line that opens the mapping — hence the explicit
#      if/fi rather than the `[ -n … ] &&` one-liners used above. A
#      `[ -n "$X" ] && echo …` as the last command of this group would
#      return non-zero when $X is empty and `set -e` would kill the
#      container on the way to `exec "$@"`.
if [ -n "$METABASE_API_KEY" ] || [ -n "$GRAFANA_API_KEY" ] || [ -n "$SENTRY_AUTH_TOKEN" ] || [ -n "$GITHUB_TOKEN" ] || [ -n "$POSTHOG_API_KEY" ]; then
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
    [ -n "$GITHUB_TOKEN" ] && cat <<EOSERVER
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
EOSERVER
    if [ -n "$POSTHOG_API_KEY" ]; then
      cat <<EOSERVER
  posthog:
    url: https://mcp.posthog.com/mcp
    headers:
      Authorization: Bearer ${POSTHOG_API_KEY}
EOSERVER
      if [ -n "$POSTHOG_ORGANIZATION_ID" ]; then
        echo "      x-posthog-organization-id: \"${POSTHOG_ORGANIZATION_ID}\""
      fi
      if [ -n "$POSTHOG_PROJECT_ID" ]; then
        echo "      x-posthog-project-id: \"${POSTHOG_PROJECT_ID}\""
      fi
    fi
  } >> "$HERMES_HOME/config.yaml"
fi

# 4. Render the `model:` block when HERMES_MODEL is set. The block is
#    emitted last so it composes with the platforms/mcp_servers blocks above
#    into one config.yaml. See the step-4 note in the header for why this
#    lives in config.yaml rather than an LLM_MODEL env var, and why
#    API_SERVER_MODEL_NAME doesn't cover Telegram.
#
#    The `model:` key is distinct from `platforms:` and `mcp_servers:`, so
#    appending it here is safe — PyYAML merges top-level keys from a single
#    document and there is no duplicate `model:` writer.
if [ -n "$HERMES_MODEL" ]; then
  {
    echo "model:"
    [ -n "$HERMES_MODEL_PROVIDER" ] && echo "  provider: \"$HERMES_MODEL_PROVIDER\""
    echo "  default: \"$HERMES_MODEL\""
    [ -n "$HERMES_MODEL_CONTEXT_LENGTH" ] && echo "  context_length: $HERMES_MODEL_CONTEXT_LENGTH"
  } >> "$HERMES_HOME/config.yaml"
fi

# Lock file perms if any env-driven block was written.
if [ -n "$CONFIG_RENDERED" ]; then
  chmod 600 "$HERMES_HOME/config.yaml"
fi

# 4. Drop conflicting Anthropic creds when MiniMax is configured.
if [ -n "$MINIMAX_API_KEY" ]; then
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_TOKEN
fi

# 5. Hand off to the gateway.
exec "$@"
