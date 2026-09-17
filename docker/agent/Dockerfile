FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

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

RUN pip install --no-cache-dir -e .

EXPOSE 8642

CMD ["hermes", "gateway", "run"]
