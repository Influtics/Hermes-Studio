# Restrict Studio Access — Design Spec

**Date:** 2026-09-18
**Status:** Approved (awaiting user review of written spec)
**Scope:** Gate https://hermes.influtics.com behind a single shared password. No multi-user accounts.

---

## Problem

`https://hermes.influtics.com` is currently public. The studio can perform sensitive actions (start/stop agents, edit Hermes config, run conductor missions, manage cron jobs, terminate sessions, read memory) via the same routes a random internet visitor can hit. The single-user deployment is fine functionally — only the operator uses it — but the lack of any gate means:

1. Anyone scanning the URL sees the full UI scaffolding and can poke at `/api/*` endpoints.
2. Search engines can index internal agent state.
3. If the FQDN leaks (logs, screenshots, shared links), there's no friction before someone lands in front of the controls.

The auth scaffolding (`src/server/auth-middleware.ts`, `/api/auth`, `/api/auth-check`) is already 100% built and unit-tested. Of the ~43 API routes in the project today, 25 call `isAuthenticated` and 18 skip the check (full list in the table below). What's missing is the wiring that turns this dormant middleware into a working login wall.

## Goal

Single shared password (env var) gates the studio. Visitors to any path other than `/login`, `/api/auth`, `/api/auth-check`, `/api/auth-logout`, or `/health` get a server-side redirect to `/login` until they authenticate. Successful login sets a 30-day session cookie. Logout clears it.

**Non-goals (explicit):**
- Multi-user accounts / signup / password reset / email verification
- Sliding session timeout / idle-timeout
- MFA / TOTP / WebAuthn
- Rate limiting beyond what `/api/auth` already does (5/min/IP)
- IP allowlisting (edge concern, not in scope here)

---

## Architecture

**Approach:** Complete the existing single-password auth. Add an SSR-side gate via TanStack Router's `beforeLoad` so unauthenticated visitors get a 302 to `/login` before any UI bytes ship. Add a `requireAuth(request)` helper to `auth-middleware.ts` and apply it to every API handler, including the ~18 that currently skip the check. Wire a logout button into the existing nav.

**What stays:**
- `src/server/auth-middleware.ts` — lexer-level logic, just gains one helper
- `src/routes/api/auth.ts` — already handles POST + rate limit + session cookie
- `src/routes/api/auth-check.ts` — already returns `{ authenticated, authRequired }`
- The 25 API routes that already call `isAuthenticated` — behavior unchanged, call site swapped to the helper

**What gets added:**
- `src/routes/login.tsx` — single-page password form
- `src/routes/api/auth-logout.ts` — POST handler that revokes the token and clears the cookie
- `src/components/logout-button.tsx` — nav-header button calling the logout endpoint
- `requireAuth(request)` helper in `src/server/auth-middleware.ts`

**What gets modified:**
- `src/routes/__root.tsx` — `beforeLoad` runs `isAuthenticated(request)`, throws `redirect({ to: '/login' })` for protected paths
- 18 API routes that lack auth today — `requireAuth(request)` near the top of each handler
- `.env.example` — documents `HERMES_PASSWORD` and `HERMES_USER_ID`

**Data flow (unauthenticated visitor to dashboard):**
```
GET https://hermes.influtics.com/dashboard
  → Coolify/Traefik proxies to studio
  → Root beforeLoad runs server-side
  → isAuthenticated(request): no cookie, HERMES_PASSWORD set → false
  → beforeLoad throws redirect({ to: '/login' })
  → SSR returns 302 with Location: /login
  → Browser follows, renders /login
  → User submits password
  → POST /api/auth → verifies → Set-Cookie: hermes-auth=<token>; HttpOnly; SameSite=Strict; Max-Age=2592000 → 200
  → Client navigates to /
  → beforeLoad runs again with cookie → isAuthenticated returns true
  → Dashboard renders
```

**Data flow (already-logged-in user):**
```
GET /dashboard with hermes-auth cookie
  → beforeLoad: isAuthenticated → true → render as normal
```

**Data flow (API call from logged-in UI):**
```
fetch('/api/files', { credentials: 'include' })
  → Handler: const authError = requireAuth(request); if (authError) return authError
  → isAuthenticated → true → handler runs as normal
```

---

## Component Decomposition

### New files

**`src/routes/login.tsx`** — full-page login form. Single `password` input, submit button, error message slot. **No loader / SSR data fetching** — the page shell hydrates instantly and the auth-check runs client-side in a `useEffect`. Rationale: the auth-check probes the Hermes agent, which may be in-flight or down; doing it client-side means a slow probe doesn't block the page shell from rendering. On mount, the `useEffect` calls `/api/auth-check`; if already authenticated, navigates to `/`. On submit, POSTs to `/api/auth`, on 200 calls `router.navigate({ to: '/', replace: true })`, on 401 shows error. Reuses existing theme tokens (`var(--theme-*)`).

**`src/routes/api/auth-logout.ts`** — POST handler. Reads `hermes-auth` cookie via `getSessionTokenFromCookie`. If present, calls `revokeSessionToken(token)`. Returns a `Set-Cookie` header with `hermes-auth=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0` to clear the browser copy. Body: `{ ok: true }`. Idempotent — logout when already logged out returns 200, not an error. Not gated by `requireAuth` (logout must work when called by the client before navigation, and must succeed even if the cookie is already expired).

**`src/components/logout-button.tsx`** — small inline button placed in the existing nav header. On click: `await fetch('/api/auth-logout', { method: 'POST', credentials: 'include' })` → `router.navigate({ to: '/login', replace: true })`. Disabled while in-flight to prevent double-submit.

### Modified files

**`src/server/auth-middleware.ts`** — add one function near the bottom:
```ts
/**
 * Returns a 401 Response if the request is unauthenticated, or null if OK.
 * Use at the top of any handler that should be gated:
 *
 *   const authError = requireAuth(request)
 *   if (authError) return authError
 */
export function requireAuth(request: Request): Response | null {
  if (isAuthenticated(request)) return null
  return json(
    { ok: false, error: 'Authentication required', code: 'UNAUTHENTICATED' },
    { status: 401 },
  )
}
```
Existing `isAuthenticated`, `verifyPassword`, `storeSessionToken`, `revokeSessionToken`, `createSessionCookie`, `getSessionTokenFromCookie` stay untouched.

**`src/routes/__root.tsx`** — add a `beforeLoad` to the root route that:
- If `pathname` matches an exempt path (`/login`, `/api/auth`, `/api/auth-check`, `/api/auth-logout`, `/health` if it exists), return early
- Otherwise, call `isAuthenticated(request)` directly from `auth-middleware`
- On `isPasswordProtectionEnabled() && !isAuthenticated(request)`: throw `redirect({ to: '/login', replace: true })`

The check runs on every navigation, both server (SSR) and client (post-hydration). Server-side check produces a real 302 — no UI bytes shipped to unauthenticated visitors.

**API routes without auth today** — add near the top of each handler:
```ts
import { requireAuth } from '../server/auth-middleware' // or '@/server/auth-middleware'
// ...
const authError = requireAuth(request)
if (authError) return authError
```

Full list (will grep-confirm in implementation; may shift by a route or two if any have moved or been added):

| Path | File |
|---|---|
| `/api/files` | `src/routes/api/files.ts` |
| `/api/send-stream` | `src/routes/api/send-stream.ts` |
| `/api/hermes-runs` | `src/routes/api/hermes-runs.ts` |
| `/api/hermes-runs/$runId/events` | `src/routes/api/hermes-runs.$runId.events.ts` |
| `/api/approvals/$approvalId/approve` | `src/routes/api/approvals.$approvalId.approve.ts` |
| `/api/approvals/$approvalId/deny` | `src/routes/api/approvals.$approvalId.deny.ts` |
| `/api/events` | `src/routes/api/events.ts` |
| `/api/events/*` | `src/routes/api/events/*` |
| `/api/crews/$crewId/dispatch` | `src/routes/api/crews/$crewId.dispatch.ts` |
| `/api/crews/$crewId/workflow` | `src/routes/api/crews/$crewId.workflow.ts` |
| `/api/mcp/*` | `src/routes/api/mcp/*` |
| `/api/memory/*` | `src/routes/api/memory/*` (folder) |
| `/api/agents/*` | `src/routes/api/agents/*` (folder) |
| `/api/audit/*` | `src/routes/api/audit/*` (folder) |
| `/api/knowledge/*` | `src/routes/api/knowledge/*` (folder) |
| `/api/models` | `src/routes/api/models.ts` |
| `/api/oauth/device-code` | `src/routes/api/oauth.device-code.ts` |
| `/api/oauth/poll-token` | `src/routes/api/oauth.poll-token.ts` |

The 25 routes already calling `isAuthenticated` will be migrated to `requireAuth` for consistency (one source of truth for the 401 body shape).

**.env.example** — add:
```
# ═══════════════════════════════════════════════════════════════
# REQUIRED to enable studio access control
# When unset, /login is bypassed and the studio is publicly accessible.
# When set, all routes (except /login and the auth endpoints) require this
# password. Set it BEFORE first use in production.
# ═══════════════════════════════════════════════════════════════
# HERMES_PASSWORD=

# Optional: identity to associate with this deployment's sessions.
# Falls back to "studio" if unset. Currently single-user only.
# HERMES_USER_ID=
```

---

## Exempt routes (allowlist — these NEVER require auth)

| Route | Why |
|---|---|
| `/login` | The login page itself |
| `POST /api/auth` | Login endpoint (already rate-limited 5/min/IP) |
| `GET /api/auth-check` | Client needs to know `authRequired` + `authenticated` to render the right screen |
| `POST /api/auth-logout` | Idempotent; must succeed even if cookie is missing/expired |
| `/health` | Docker HEALTHCHECK probe — gating it breaks container health |

`/health` may not exist as a route — will grep `Dockerfile` and `src/routes/` during implementation. If absent, no-op.

---

## Tests

Extend `src/test/auth-middleware.test.ts` with `requireAuth` cases:
- Returns `null` when password protection is disabled (regardless of cookie)
- Returns `null` when password is enabled and cookie is valid
- Returns a 401 `Response` when password is enabled and cookie is missing/invalid
- Response body has `{ ok: false, code: 'UNAUTHENTICATED' }`

New `src/test/routes/login.test.tsx`:
- Renders the password input + submit button
- Submits → calls `/api/auth` → on 200, navigates to `/`
- Submits → on 401, shows error message inline

New `src/test/routes/auth-logout.test.ts`:
- POST without cookie → 200, returns Set-Cookie clearing the cookie
- POST with valid cookie → 200, token removed from `validTokens` set
- POST with already-revoked cookie → 200 (idempotent)

Manual e2e test (run before declaring done):
1. `HERMES_PASSWORD` unset → existing behavior unchanged, no `/login` redirect
2. `wrangler deploy` (or Coolify webhook) → app live
3. Set `HERMES_PASSWORD` in Coolify env vars for hermes-studio
4. Wait for redeploy (~2-5 min)
5. `curl -I https://hermes.influtics.com/dashboard` from outside → expect 302 with `Location: /login`
6. Open browser → see `/login` page → submit correct password → land on dashboard
7. Open DevTools → Application → Cookies → confirm `hermes-auth` cookie is HttpOnly + 30-day expiry
8. Wrong password → inline error, no cookie set
9. Click logout → return to `/login`, cookie cleared
10. `curl /health` → still 200 (not gated)

---

## Deployment order (matters)

1. **Merge code with `HERMES_PASSWORD` still UNSET.** Studio behaves exactly as today — no surprise lockout for the operator. No `/login` redirect. Docker container healthy.
2. **Confirm deployment healthy.** Visit `https://hermes.influtics.com/dashboard` → still loads. Skip the `/login` redirect for now.
3. **User sets `HERMES_PASSWORD` in Coolify UI** for the hermes-studio app. (Member-role MCP can't write env vars — this is a manual step. UI flow: Coolify → Apps → hermes-studio → Environment Variables → add `HERMES_PASSWORD`.)
4. **Coolify redeploys** on env-var change. From this moment onward, the studio is gated.

**Inverting this order (set password first, then deploy code) would 401 every visitor with no recovery path until the code lands.** We do not do that.

If the operator locks themselves out (forgotten password), the recovery is: set `HERMES_PASSWORD=` (empty) in Coolify → redeploy → `isPasswordProtectionEnabled()` returns false → studio open again. No data loss.

---

## Risks

- **Docker HEALTHCHECK gating:** if `docker/workspace/Dockerfile` (or whichever Dockerfile Coolify uses for hermes-studio) has `HEALTHCHECK CMD wget ... http://localhost/health`, the `/health` route must remain in the exempt list or the container flips to `unhealthy`. **Mitigation:** grep Dockerfile + routes for `/health` during implementation; verify exempt before deploy.
- **Member-role Coolify MCP can't `env_vars:update`:** env var setting is a manual UI step. The plan will spell out the exact UI flow.
- **Token leak via logs:** the existing middleware stores tokens in memory only. Will grep `src/server/` for any `console.log` / structured-log emit that includes the token. If found, patch.
- **`/api/auth-check` cosmetic issue:** currently returns 503 with `authRequired: false` when the agent is unreachable. This conflates "auth off" with "agent down". One-line fix in scope: always return `authRequired: isPasswordProtectionEnabled()`; only trust the auth result when the agent is reachable. Same 503 if `reachable === false`, just with the correct `authRequired` value.
- **Login page SSR pattern:** documented inline on `login.tsx` — the auth-check is client-side (`useEffect`), not a loader. No risk to ship.
- **Wildcard routes in the unauthenticated list:** paths like `/api/events/*` expand to multiple concrete handler files (one per sub-route). The implementer's grep must recurse into those folders, not match the wildcard literally. Already noted in the table caption.

---

## Out of scope (YAGNI)

- Multi-user accounts / signup / password reset
- Sliding session timeout / idle-timeout
- MFA / TOTP / WebAuthn
- CSRF tokens (existing protection: SameSite=Strict + JSON content-type guard on `/api/auth`)
- IP allowlisting (edge concern, layer via Coolify/Cloudflare if needed)
- Reverse-proxy auth (Cloudflare Access) — can be added later without code changes
- Password change UI — env-var-driven password change via Coolify UI is sufficient for single-user
- "Remember me" toggle — 30-day cookie is always-on; logout button covers the "sign out" need

---

## Open questions for review

1. **Logout button placement:** I propose the existing nav header. If there's a more natural spot (sidebar footer? avatar dropdown?), confirm.
2. **Login page styling:** matching existing studio theme (`var(--theme-*)` tokens) — confirm or specify a custom layout.
3. **The `/api/auth-check` cosmetic fix** (always return `authRequired: isPasswordProtectionEnabled()`) — in scope, or split into a separate change?
