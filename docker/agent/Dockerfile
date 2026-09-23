FROM python:3.11-slim

# xz-utils: required so hermes-agent can extract its bundled Node.js 22 tarball
# (node-v22.x-linux-x64.tar.xz). Without it, every startup logs:
#   tar (child): xz: Cannot exec: No such file or directory
# and browser tools stay disabled.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Default MiniMax endpoint for hermes-agent. The agent's provider router uses
# URL-based heuristic: a base_url ending in /anthropic → anthropic_messages
# adapter → MINIMAX_API_KEY (NOT ANTHROPIC_API_KEY). If the user overrides
# this in Coolify env vars at runtime, the runtime value wins (as expected).
# Set this even when no key is set so the value stays correct after redeploys
# that swap keys.
ENV MINIMAX_BASE_URL=https://api.minimax.io/anthropic

# Clone hermes-agent and checkout latest compatible version
# Set HERMES_AGENT_VERSION env var to pin to a specific version (e.g., 0.18.0)
ARG HERMES_AGENT_VERSION=main
RUN git clone https://github.com/outsourc-e/hermes-agent.git /app

WORKDIR /app

RUN if [ "$HERMES_AGENT_VERSION" != "main" ]; then \
      git fetch origin && git checkout "v${HERMES_AGENT_VERSION}" || git checkout "${HERMES_AGENT_VERSION}"; \
    fi

# Patch missing _parse_bool() helper in gateway/platforms/api_server.py.
# Bug in outsourc-e/hermes-agent@main: _TRUE_REQUEST_BOOL_STRINGS is
# defined at module scope and the function is called at line ~715 inside
# ApiServerPlatform.__init__, but the function itself was never written.
# Symptom on first boot: NameError: name '_parse_bool' is not defined
# (Python suggests '_parse_tags', which is the only similar symbol).
# Inject a minimal implementation that reuses the existing constant.
# Idempotent: skips if the marker is absent (fork fixed upstream) or the
# function is already defined (rebuild with stale layer cache).
RUN python3 - <<'PY'
import pathlib, sys
p = pathlib.Path("gateway/platforms/api_server.py")
src = p.read_text()
marker = "_TRUE_REQUEST_BOOL_STRINGS = frozenset"
if marker not in src:
    print("patch: marker absent, skipping (fork fixed upstream?)", file=sys.stderr)
    sys.exit(0)
if "def _parse_bool(" in src:
    print("patch: _parse_bool already defined, skipping", file=sys.stderr)
    sys.exit(0)
fn = '''

def _parse_bool(value, default: bool = False) -> bool:
    """Coerce a string env/config value to bool. Empty/None -> default."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_REQUEST_BOOL_STRINGS
'''
src = src.replace(marker, marker + fn, 1)
p.write_text(src)
print("patch: _parse_bool injected")
PY

# Install hermes-agent with the [mcp] extra. The `mcp` SDK is declared in
# pyproject.toml under [project.optional-dependencies], not [project]
# dependencies, so a plain `pip install -e .` skips it. Without the SDK
# installed, `tools/mcp_tool.py:_MCP_AVAILABLE` stays False and
# `discover_mcp_tools()` short-circuits at line 3305 — the gateway then
# reports "no MCP servers configured" regardless of what's in
# ~/.hermes/config.yaml. Symptom: mcp_servers.metabase is rendered into
# config.yaml by the entrypoint, but the agent says it sees no Metabase
# tools. Adding `[mcp]` pulls in mcp==1.26.0 and the transitive deps the
# SDK needs.
RUN pip install --no-cache-dir -e ".[mcp]"

# Wrapper entrypoint: when MINIMAX_API_KEY is set, drop Anthropic credentials
# so the upstream router can't send MiniMax's key to api.anthropic.com as a
# fallback (visible symptom: studio's /api/skills returns 500 with
# {"type":"error","error":{"type":"invalid_request_error","message":"Invalid API Key"}}
# because auxiliary clients fall back through Anthropic).
# Idempotent: if MINIMAX_API_KEY is unset, the wrapper is a passthrough.
COPY docker/agent/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8642

CMD ["/entrypoint.sh", "hermes", "gateway", "run"]
