# VK (VKontakte) platform plugin

A Hermes gateway platform adapter that lets you talk to the Hermes agent
from a **VK community** — the VKontakte equivalent of the Telegram bot.

Implemented as a [Hermes platform plugin](https://github.com/outsourc-e/hermes-agent):
it registers through `ctx.register_platform()` and needs **no changes to
Hermes core**. The plugin system handles adapter creation, config
parsing, user authorization, cron delivery, `send_message` routing and
system-prompt hints.

## How it receives messages

Via the **VK Long Poll API**, not the Callback API.

The Hermes agent container has no public URL — it is reached only over
the internal Docker network as `http://hermes-agent:8642`. The Callback
API would need a public HTTPS endpoint plus VK's confirmation-secret
handshake. Long Poll needs nothing public: the adapter asks VK for a
longpoll server + key, then polls it for events.

```
You ──► VK community messages ──► lp.vk.com (poll, no token)
                                        │
                                        ▼
                          hermes-agent container
                            └─ plugins/vk adapter
                                 ├─ api.vk.com calls ──► vk-proxy.influtics.com ──► VK
                                 └─ handle_message() ──► Hermes gateway ──► MiniMax
```

## Two egress paths, on purpose

| Call | Path | Why |
|---|---|---|
| `groups.getLongPollServer`, `messages.send`, `users.get` | **via `vk-proxy.influtics.com`** (default) | The proxy runs on the RU VPS, so every token-bearing call egresses from a single static IP — VK's requirement for IP-locked tokens, and what the rest of the Influtics stack does (`vk-worker` → proxy → VK). |
| Long Poll polling | **direct** to `lp.vk.com` | The poll URL carries only the longpoll key, never the access token, and VK does not bind that key to an IP. |

Set `VK_API_MODE=direct` to call `api.vk.com` straight from the container.
Do that only if the community token is confirmed not to be IP-locked.

## Configuration

All environment variables — no `config.yaml` editing needed.

| Variable | Required | Secret | Purpose |
|---|---|---|---|
| `VK_GROUP_TOKEN` | **yes** | yes | Community access token with the `messages` scope |
| `VK_GROUP_ID` | **yes** | no | Numeric community id (e.g. `243045540`) |
| `VK_ALLOWED_USERS` | no | no | Comma-separated VK user ids allowed to talk to the agent. **Unset = nobody** (fail-closed) |
| `VK_ALLOW_ALL_USERS` | no | no | Allow any user — dev only, disables the allowlist |
| `VK_HOME_CHANNEL` | no | no | Default peer id for cron / notification delivery. A personal dialog is `2000000001 + <user id>` |
| `VK_HOME_CHANNEL_NAME` | no | no | Label for the home channel |
| `VK_API_MODE` | no | no | `proxy` (default) or `direct` |
| `VK_PROXY_URL` | no | no | Default `https://vk-proxy.influtics.com` |
| `VK_PROXY_API_KEY` | no | yes | Bearer key for the proxy (same secret as the backend's `VK_PROXY_API_KEY`) |
| `VK_LONGPOLL_WAIT` | no | no | Long Poll wait seconds, 10–90, default 25 |

The plugin auto-enables itself from these variables: the platform's
`check_fn()` returns true when the token and group id are present, which
is what flips `PlatformConfig.enabled` on. No `platforms: vk:` block in
`config.yaml` is needed.

## VK-side setup

1. Create the community — `vk.com/groups?act=create`.
2. **Manage → Messages**: enable *Community messages* and allow the
   community to send messages.
3. **Manage → API usage → Long Poll API**: switch it to **Enabled**
   (API version 5.199 is correct).
4. **Manage → API usage → Access keys**: create a community access token
   with the `messages` scope. That is `VK_GROUP_TOKEN`.
5. The community id is the number in the URL — `vk.com/club243045540` →
   `243045540`. That is `VK_GROUP_ID`.
6. Your own numeric VK id goes in `VK_ALLOWED_USERS`. A personal dialog
   with the community is addressed as `2000000001 + <your id>` — that is
   `VK_HOME_CHANNEL`.
7. Open the community's messenger and write to it.

## Deploying

The plugin is copied into the agent image by
`docker/agent/Dockerfile`:

```dockerfile
COPY docker/agent/plugins/vk /app/plugins/platforms/vk
```

Bundled platform plugins auto-load — the Hermes plugin loader scans
`plugins/platforms/*/plugin.yaml` at startup, so no `plugins.enabled`
entry is required.

Then set the environment variables on the Coolify `hermes-agent` app and
redeploy. The `hermes-agent` app must be on branch `main` (a stale
`feat/minimax-anthropic-provider` pin will not pick this up).

## Typing and streaming

Both are supported, and both need no changes to Hermes core.

**Typing.** VK's `messages.setActivity` is the counterpart of Telegram's
`sendChatAction`: with a community token it makes the community appear
as typing in the user's dialog. The gateway calls `send_typing` on a
~2-second heartbeat, so one implementation gives a self-refreshing
bubble for free. The adapter sends it only for a personal dialog — a
group conversation has no single user to attach the activity to.

**Streaming.** The gateway's `stream_consumer` sends the first chunk of
a reply with `messages.send` and then rewrites that same message with
`messages.edit` as the answer grows. An adapter that does not implement
`edit_message` gets `SendResult(success=False, error="Not supported")`
from the base class and the consumer falls back to a single message — so
implementing `edit_message` is the whole job.

VK has no draft API, so the transport stays `edit`. The native-draft
transport (`sendMessageDraft`) is Telegram-only.

### Turning streaming on

Streaming is **off by default** (`StreamingConfig.enabled = False`), and
the knob is per-platform:

```yaml
display:
  platforms:
    vk:
      streaming: true
```

`display.platforms.<platform>.streaming` is resolved *before* the
top-level `streaming.enabled`, so setting it for `vk` enables streaming
for VK alone and leaves Telegram exactly as it was.

| Variable | Default | Purpose |
|---|---|---|
| `VK_STREAMING` | `true` | Render the block above when the agent image starts. `false` turns streaming off without a code change |

The agent image renders that block itself: `docker/agent/entrypoint.sh`
writes it whenever `VK_GROUP_TOKEN` is set.

The edit cadence defaults to one edit per 0.8s — tuned for Telegram's
~1 edit/s flood envelope. VK's `messages.edit` has its own limit, and
the stream consumer already backs off when VK reports a rate limit: the
adapter rephrases VK's error 6 ("Too many requests") so the consumer's
flood detection recognises it.

### The VK methods it calls

| Method | Why |
|---|---|
| `groups.getLongPollServer` | The Long Poll server, key and `ts` |
| `groups.getLongPollSettings` | Read the event flags — `message_new` must be on |
| `messages.send` | Send a reply |
| `messages.edit` | Rewrite a reply in place — this is what makes streaming possible |
| `messages.setActivity` | The typing indicator |
| `users.get` | The sender's display name (the Long Poll carries no profile) |
| `groups.getById` | The community's display name |

## Tests

```bash
pip install pytest pytest-asyncio "httpx==0.28.1"
pytest docker/agent/plugins/vk/tests -v
```

The suite is hermetic: `tests/conftest.py` stubs the two `gateway.*`
modules the plugin imports, so no Hermes install and no network access
are required. CI runs it in the `agent-plugin-tests` job.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `vk` missing from `hermes gateway status` | Token or group id missing | Set `VK_GROUP_TOKEN` + `VK_GROUP_ID` |
| `VK error 5` / `1130` in the logs | Token rejected, or IP-locked to another host | Use `VK_API_MODE=proxy` and check `VK_PROXY_API_KEY` |
| `VK error 900` | Invalid or revoked token | Re-create the community access token |
| `Long Poll rejected (HTTP 401/403)` | The longpoll key rotated | The adapter re-fetches it automatically; if it persists, check Long Poll is enabled in the community settings |
| Bot never answers | The community has no messages enabled, or the user has not opened a dialog with the community | Enable *Community messages*; write to the community first |
| Replies render `**bold**` literally | Markdown reached VK | The adapter strips emphasis; check the agent is using the `vk` platform hint |
