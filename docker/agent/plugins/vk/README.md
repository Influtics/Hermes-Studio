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

## The `lp_version=2` event shape

The plugin asks for `lp_version=2`, and that version changes three
things about an update. Each one fails *silently* — the poll succeeds,
the events arrive, and nothing is handled — so they are worth stating
in full:

```json
{"group_id": 243045540, "type": "message_new", "event_id": "…", "v": "5.199",
 "object": {"client_info": {…},
            "message": {"from_id": 191655492, "peer_id": 191655492,
                       "conversation_message_id": 23,
                       "text": "which model are you", "out": 0, …}}}
```

1. **`type` is a string** (`"message_new"`), not the integer `4` that
   `lp_version=1` sends. Only the integer is dropped silently.
2. **The message is nested under `object.message`.** In `lp_version=1`
   the fields sit straight on `object`; reading `object["from_id"]` on a
   version-2 update gives `None`.
3. **`peer_id` is the bare user id** (`191655492`), not
   `2000000001 + user_id`. A personal dialog is recognised by
   `peer_id == from_id` as well as by the offset form.

Both event types are accepted (integer `4` and `"message_new"`), and
the reply is sent to the peer VK named in the event.

**If you need to see what VK actually sends**, poll the Long Poll
yourself — the documentation does not describe the version-2 shape, and
assumptions drawn from it tend to be wrong:

```bash
# server, key and ts come from groups.getLongPollServer
curl "https://$SERVER?act=a_check&key=$KEY&ts=$TS&wait=30"
```

Write a message in the community's chat inside that window and the raw
payload appears in the response.

## Two egress paths, on purpose

| Call | Path | Why |
|---|---|---|
| `groups.getLongPollServer`, `messages.send`, `users.get` | **via `vk-proxy.influtics.com`** (default) | The proxy runs on the RU VPS, so every token-bearing call egresses from a single static IP — VK's requirement for IP-locked tokens, and what the rest of the Influtics stack does (`vk-worker` → proxy → VK). |
| Long Poll polling | **direct** to `lp.vk.com` (default) | The poll URL carries only the longpoll key, never the access token, and VK does not bind that key to an IP. |

Set `VK_API_MODE=direct` to call `api.vk.com` straight from the container.
Do that only if the community token is confirmed not to be IP-locked.

`VK_LONGPOLL_MODE` controls the poll path: `auto` (default) tries
direct and falls back to the proxy on the first failure, `proxy` never
uses the direct path, `direct` never uses the proxy. The proxy relays
the poll through `POST /vk/longpoll`, which is restricted to VK's Long
Poll hosts and carries no access token — only the longpoll key.


## Configuration

All environment variables — no `config.yaml` editing needed.

| Variable | Required | Secret | Purpose |
|---|---|---|---|
| `VK_GROUP_TOKEN` | **yes** | yes | Community access token with the `messages` scope |
| `VK_GROUP_ID` | **yes** | no | Numeric community id (e.g. `243045540`) |
| `VK_ALLOWED_USERS` | no | no | Comma-separated VK user ids allowed to talk to the agent. **Unset = nobody** (fail-closed) |
| `VK_ALLOW_ALL_USERS` | no | no | Allow any user — dev only, disables the allowlist |
| `VK_HOME_CHANNEL` | no | no | Peer id for cron / notification delivery. **The bare user id** (e.g. `191655492`) — that is what VK reports as `peer_id` and what `messages.send` accepts. The `2000000001 + user id` form is rejected with `peer_id is invalid` |
| `VK_HOME_CHANNEL_NAME` | no | no | Label for the home channel |
| `VK_API_MODE` | no | no | `proxy` (default) or `direct` |
| `VK_PROXY_URL` | no | no | Default `https://vk-proxy.influtics.com` |
| `VK_PROXY_API_KEY` | no | yes | Bearer key for the proxy (same secret as the backend's `VK_PROXY_API_KEY`) |
| `VK_LONGPOLL_WAIT` | no | no | Long Poll wait seconds, 10–90, default 25 |
| `VK_LONGPOLL_MODE` | no | no | `auto` (default — direct, falling back to the proxy), `proxy`, or `direct` |

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
6. Your own numeric VK id goes in `VK_ALLOWED_USERS`, and the **same
   bare id** is `VK_HOME_CHANNEL` — not `2000000001 + id`, which VK
   rejects as an invalid peer.
7. Open the community's messenger and write to it. The community's
   messenger and the chat widget on the community page are different
   conversations; the messenger is the one that reaches the Long Poll.

### Checking the community's own settings

`groups.getLongPollSettings` reports the event flags VK will deliver.
`message_new` must be `1` — enabling Long Poll is only half the job,
because the *events* live in a separate set of flags that are all off
until they are saved. The adapter checks this on every server
acquisition and logs a warning naming the place to fix it, but it does
not change them: that is a persistent change to the community.

`events` is a flat map of event name to `1`/`0`, not a nested
`{in, out, enabled}` object.

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

## Tests

```bash
pip install pytest pytest-asyncio "httpx==0.28.1"
pytest docker/agent/plugins/vk/tests -v
```

The suite is hermetic: `tests/conftest.py` stubs the two `gateway.*`
modules the plugin imports, so no Hermes install and no network access
are required. CI runs it in the `agent-plugin-tests` job.

## Troubleshooting

**Start with the VK proxy's log, not the agent's.** The proxy records
every token-bearing VK call, so it shows whether a message arrived
(`users.get`), whether a reply was attempted (`messages.send`) and what
VK answered. The agent's own log is filtered to `WARNING` and above —
a healthy adapter says nothing at all, which is exactly what makes
"no log lines" ambiguous.

| Symptom | Cause | Fix |
|---|---|---|
| `vk` missing from `hermes gateway status` | Token or group id missing | Set `VK_GROUP_TOKEN` + `VK_GROUP_ID` |
| `Failed to create adapter for platform 'VK'` | The adapter constructor raised — usually a type mismatch between the config seed and what the constructor expects | Check the adapter's log line just above it |
| `VK error 5` / `1130` in the logs | Token rejected, or IP-locked to another host | Use `VK_API_MODE=proxy` and check `VK_PROXY_API_KEY` |
| `VK error 900` / `901` | The community is not allowed to message that user | Enable *Community messages*; the user must have written first |
| `peer_id is invalid` (error 100) on send | The peer id is the `2000000001 + user id` form. VK wants the **bare user id** | Set `VK_HOME_CHANNEL` to the bare user id |
| `Long Poll rejected (HTTP 401/403)` | The longpoll key rotated | The adapter re-fetches it automatically; if it persists, check Long Poll is enabled in the community settings |
| Poll connects, but no message ever arrives | The community's `message_new` flag is off | The adapter logs this at `WARNING`; otherwise check `groups.getLongPollSettings` |
| Poll connects, no reply, no `users.get` in the proxy log | The event was received but not handled — check the update shape against [the `lp_version=2` section](#the-lp_version2-event-shape) | Compare with a live poll capture |
| Replies render `**bold**` literally | Markdown reached VK | The adapter strips emphasis; check the agent is using the `vk` platform hint |

### A note on `ts`

The Long Poll `ts` is VK's event counter for the community, and it
advances for **any** enabled event type — a community with `message_read`,
`message_event`, `wall_*` and `video_*` enabled produces plenty of
events that are not messages. A rising `ts` does not mean messages are
being delivered; poll it yourself and look for `type: "message_new"`.

