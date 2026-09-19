#!/bin/sh
# entrypoint.sh — drop conflicting Anthropic credentials when MiniMax is configured.
#
# Why this exists:
#   Upstream hermes-agent's provider router uses URL-based heuristic: a base_url
#   ending in /anthropic → anthropic_messages adapter → MINIMAX_API_KEY
#   (NOT ANTHROPIC_API_KEY). See agent/agent_init.py:
#     "Only fall back to ANTHROPIC_TOKEN when the provider is actually
#      Anthropic. Other anthropic_messages providers (MiniMax, Alibaba, etc.)
#      must use their own API key."
#
#   The /api/skills enumeration calls an LLM to summarize each skill. When
#   BOTH ANTHROPIC_API_KEY and MINIMAX_API_KEY are set, the agent's auxiliary
#   client can fall back to Anthropic after a primary-provider error and send
#   MiniMax's key to api.anthropic.com, which rejects it with "Invalid API Key".
#   studio surfaces that as: Skills: HTTP 500.
#
# Behavior:
#   - If MINIMAX_API_KEY is set: unset ANTHROPIC_API_KEY + ANTHROPIC_TOKEN so
#     no path in the agent can route to api.anthropic.com.
#   - Otherwise: passthrough. ANTHROPIC_API_KEY stays available for native
#     Anthropic users.

set -e

if [ -n "$MINIMAX_API_KEY" ]; then
  unset ANTHROPIC_API_KEY
  unset ANTHROPIC_TOKEN
fi

exec "$@"
