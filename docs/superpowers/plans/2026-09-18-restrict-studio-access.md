# Restrict Studio Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate https://hermes.influtics.com behind a single shared password. Unauthenticated visitors get a server-side redirect to `/login` from any non-exempt route.

**Architecture:** Complete the existing single-password auth. Add an SSR-side gate via TanStack Router's `beforeLoad` so unauthenticated visitors get a 302 before any UI bytes ship. Add a `requireAuth(request)` helper and apply it to every API handler. Wire a logout button into the nav.

**Tech Stack:** TanStack Start (React 19, file-based routing, SSR), Zod, better-sqlite3 (unused here), ioredis (already configured for session token persistence), Vitest.

**Spec:** `docs/superpowers/specs/2026-09-18-restrict-studio-access-design.md` — read it first.

**Repo:** `/Users/ivanabramov/Desktop/Hermes-Studio/`

---

## File Map

**New files:**
- `src/routes/login.tsx` — login page UI
- `src/routes/api/auth-logout.ts` — POST logout endpoint
- `src/components/logout-button.tsx` — nav logout button
- `src/test/routes/login.test.tsx` — login page tests
- `src/test/routes/auth-logout.test.ts` — logout endpoint tests

**Modified files:**
- `src/server/auth-middleware.ts` — add `requireAuth()` helper
- `src/test/auth-middleware.test.ts` — extend with `requireAuth` cases
- `src/routes/__root.tsx` — add `beforeLoad` redirect gate
- `src/routes/api/auth-check.ts` — fix `authRequired` semantics on agent-down
- `.env.example` — document `HERMES_PASSWORD` + `HERMES_USER_ID`
- `src/routes/api/*.ts` (43 files) — apply `requireAuth` via grep-and-edit
- Existing nav component (TBD during Task 7 — likely `src/components/nav.tsx` or `src/routes/__root.tsx`'s header) — mount `<LogoutButton />`

**Test commands:**
- Run all tests: `npm test` (or `pnpm test` — check `package.json`)
- Run single file: `npx vitest run src/test/auth-middleware.test.ts`
- Type check: `npx tsc --noEmit`

---

## Task 0: Set Up Worktree

**Files:** none (git only)

- [ ] **Step 1: Confirm we're on `main` and clean**

```bash
cd /Users/ivanabramov/Desktop/Hermes-Studio && git status && git branch --show-current
```

Expected: clean working tree on `main`.

Also confirm the existing test-directory convention before subsequent tasks write tests:

```bash
cd /Users/ivanabramov/Desktop/Hermes-Studio && ls -la src/ | grep -E "test|tests|__tests__" && find src/ -maxdepth 3 -type d -name '*test*' -o -name '__tests__' 2>/dev/null
```

This plan writes tests into `src/test/server/` and `src/test/routes/` — if the existing convention is `__tests__/` or something else, adjust the paths in Tasks 1/4/5/6 accordingly before starting those tasks.

- [ ] **Step 2: Create worktree on a fresh branch**

```bash
cd /Users/ivanabramov/Desktop/Hermes-Studio && git worktree add -b feat/restrict-studio-access .worktrees/restrict-studio-access main
```

- [ ] **Step 3: Verify worktree is functional**

```bash
cd /Users/ivanabramov/Desktop/Hermes-Studio/.worktrees/restrict-studio-access && ls package.json && git branch --show-current
```

Expected: branch is `feat/restrict-studio-access`, `package.json` exists.

**All subsequent tasks run inside `.worktrees/restrict-studio-access/` unless otherwise noted.**

---

## Task 1: Add `requireAuth()` Helper

**Files:**
- Modify: `src/server/auth-middleware.ts`
- Modify: `src/test/auth-middleware.test.ts`

- [ ] **Step 1: Read existing auth-middleware.ts and audit for token-log leaks**

```bash
cd .worktrees/restrict-studio-access && head -30 src/server/auth-middleware.ts
```

Confirm whether `json` from `@tanstack/react-start` (or `@tanstack/react-router`) is already imported. Note the import path to use in Step 4.

Then grep `src/server/` for any logging that could leak the session token:

```bash
cd .worktrees/restrict-studio-access && grep -rEn "console\.(log|warn|error)|logger\.(info|warn|error|debug)" src/server/
```

If any of these match a line that includes a token-shaped variable (`token`, `validTokens`, `tokenUser`, `TOKENS_KEY`, `TOKEN_USER_KEY`), patch it now — the existing scaffolding must not emit tokens in any log path. Commit the patch as a separate `fix(auth): scrub token-shaped values from server logs` commit before proceeding.

- [ ] **Step 2: Write failing tests for `requireAuth`**

Append to `src/test/auth-middleware.test.ts`:

```ts
import { requireAuth, isPasswordProtectionEnabled } from '../server/auth-middleware'

describe('requireAuth', () => {
  beforeEach(() => {
    vi.resetModules()
    process.env.HERMES_PASSWORD = 'test-password'
  })

  afterEach(() => {
    delete process.env.HERMES_PASSWORD
  })

  it('returns null when password protection is disabled', () => {
    delete process.env.HERMES_PASSWORD
    const req = new Request('http://localhost/test')
    expect(requireAuth(req)).toBeNull()
  })

  it('returns null when cookie is valid', () => {
    const token = 'a'.repeat(64)
    const { storeSessionToken } = await import('../server/auth-middleware')
    storeSessionToken(token)
    const req = new Request('http://localhost/test', {
      headers: { cookie: `hermes-auth=${token}` },
    })
    expect(requireAuth(req)).toBeNull()
  })

  it('returns 401 when password is set and cookie missing', () => {
    const req = new Request('http://localhost/test')
    const res = requireAuth(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('returns 401 with UNAUTHENTICATED code', async () => {
    const req = new Request('http://localhost/test')
    const res = requireAuth(req)!
    const body = await res.json()
    expect(body).toMatchObject({ ok: false, code: 'UNAUTHENTICATED' })
  })
})
```

(Adjust import paths to match repo conventions — `@/server/auth-middleware` if path alias is used.)

- [ ] **Step 3: Run test to verify it fails**

```bash
cd .worktrees/restrict-studio-access && npx vitest run src/test/auth-middleware.test.ts
```

Expected: FAIL — `requireAuth` is not exported from auth-middleware.

- [ ] **Step 4: Implement `requireAuth`**

Edit `src/server/auth-middleware.ts`:

1. Add to imports at top:
   ```ts
   import { json } from '@tanstack/react-start'
   ```
   (Check if this package is already used elsewhere in the file. If yes, add to existing import.)

2. Append at end of file:
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

- [ ] **Step 5: Re-run tests to verify pass**

```bash
npx vitest run src/test/auth-middleware.test.ts
```

Expected: PASS — all 4 new cases plus existing cases.

- [ ] **Step 6: Commit**

```bash
git add src/server/auth-middleware.ts src/test/auth-middleware.test.ts
git commit -m "feat(auth): add requireAuth() helper returning 401 Response"
```

---

## Task 2: Migrate 25 Existing Routes from Inline `isAuthenticated` to `requireAuth`

**Files:** 25 API route files (list in spec § "API routes without auth today" — already-gated subset)

**Why as one task:** Mechanical find-and-replace. Per CLAUDE.md "frequent commits" but also "small commits" — split if any file requires non-mechanical changes.

- [ ] **Step 1: Enumerate all routes currently calling `isAuthenticated`**

```bash
cd .worktrees/restrict-studio-access && grep -rln "isAuthenticated(request)" src/routes/api/ | sort > /tmp/gated-routes.txt && wc -l /tmp/gated-routes.txt
```

Expected: ~25 lines.

- [ ] **Step 2: Inspect one route to confirm the pattern**

```bash
head -30 "$(head -1 /tmp/gated-routes.txt)"
```

Pattern to find:
```ts
if (!isAuthenticated(request)) {
  return json({ ok: false, error: 'Authentication required' }, { status: 401 })
}
```

Or close variations. Record the exact body shape used.

- [ ] **Step 3: Apply the migration with sed across all gated routes**

For each file in `/tmp/gated-routes.txt`:
- Add the import: `requireAuth` from the appropriate path (`@/server/auth-middleware` or relative).
- Replace the `if (!isAuthenticated(...)) { return json(...401...) }` block with:
  ```ts
  const authError = requireAuth(request)
  if (authError) return authError
  ```

**If the body shape varies significantly across files**, split into per-file edits. Do not force a uniform pattern if the original code has intentional differences.

- [ ] **Step 4: Verify no remaining `isAuthenticated(request)` calls in API routes that should be gated**

```bash
grep -rln "isAuthenticated(request)" src/routes/api/ | grep -v 'auth.ts\|auth-check.ts'
```

Expected: empty (the only `isAuthenticated(request)` callers in `api/` are the auth-related routes themselves).

- [ ] **Step 5: Run type check and full test suite**

```bash
npx tsc --noEmit && npx vitest run
```

Expected: no type errors; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/routes/api/
git commit -m "refactor(auth): migrate gated API routes to requireAuth() helper"
```

---

## Task 3: Add `requireAuth` to Unprotected API Routes

**Files:** ~18 API route files in `src/routes/api/` (full list in spec § "API routes without auth today")

**Sub-task structure:** group by directory. One commit per directory to keep diffs reviewable.

- [ ] **Step 1: Enumerate unprotected routes**

```bash
cd .worktrees/restrict-studio-access && \
  grep -rL "isAuthenticated\|requireAuth" src/routes/api/ \
  --include='*.ts' --include='*.tsx' \
  | grep -v -E '/(auth|auth-check|health)\.ts$' \
  | sort
```

Expected: ~18 files. Cross-check against spec list; resolve any discrepancy.

- [ ] **Step 2: For each unprotected file, add the gate**

Pattern (top of handler):
```ts
import { requireAuth } from '@/server/auth-middleware'  // or relative path

// ... inside handler:
const authError = requireAuth(request)
if (authError) return authError
```

**Wildcard routes to recurse** (per spec Risks): `/api/events/`, `/api/mcp/`, `/api/memory/`, `/api/agents/`, `/api/audit/`, `/api/knowledge/`. For each folder, list concrete handler files first, then apply to each.

- [ ] **Step 3: Per-directory commits**

Example (single commit per directory):
```bash
git add src/routes/api/files.ts src/routes/api/send-stream.ts
git commit -m "feat(auth): gate files + send-stream API routes"
```

Repeat for each directory until all unprotected routes have `requireAuth`.

- [ ] **Step 4: Verify coverage**

```bash
grep -rL "requireAuth" src/routes/api/ --include='*.ts' | grep -v 'auth.ts\|auth-check.ts'
```

Expected: empty.

- [ ] **Step 5: Type check + tests**

```bash
npx tsc --noEmit && npx vitest run
```

Expected: no errors.

- [ ] **Step 6: Confirm only auth-related routes lack the gate**

```bash
grep -rL "requireAuth" src/routes/api/ --include='*.ts' | sort
```

Expected output (the 3-4 exempt routes):
- `src/routes/api/auth.ts` (login endpoint)
- `src/routes/api/auth-check.ts` (auth status check)
- (Possibly `src/routes/api/auth-logout.ts` once added in Task 4 — also exempt)

---

## Task 4: Build `/api/auth-logout` Endpoint

**Files:**
- Create: `src/routes/api/auth-logout.ts`
- Create: `src/test/routes/auth-logout.test.ts`

- [ ] **Step 1: Write failing tests**

Create `src/test/routes/auth-logout.test.ts`:

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { POST } from '../../routes/api/auth-logout'  // adjust to repo's handler export pattern
import { storeSessionToken, isValidSessionToken, revokeSessionToken } from '../../server/auth-middleware'

describe('POST /api/auth-logout', () => {
  beforeEach(() => {
    process.env.HERMES_PASSWORD = 'test-password'
  })

  it('returns 200 with clearing cookie when no session', async () => {
    const res = await POST({ request: new Request('http://localhost/api/auth-logout', { method: 'POST' }) })
    expect(res.status).toBe(200)
    expect(res.headers.get('Set-Cookie')).toMatch(/hermes-auth=.*Max-Age=0/)
  })

  it('returns 200 and revokes token when session valid', async () => {
    const token = 'b'.repeat(64)
    storeSessionToken(token)
    expect(isValidSessionToken(token)).toBe(true)

    const req = new Request('http://localhost/api/auth-logout', {
      method: 'POST',
      headers: { cookie: `hermes-auth=${token}` },
    })
    const res = await POST({ request: req })

    expect(res.status).toBe(200)
    expect(isValidSessionToken(token)).toBe(false)
    expect(res.headers.get('Set-Cookie')).toMatch(/Max-Age=0/)
  })

  it('is idempotent (already-revoked token succeeds)', async () => {
    const token = 'c'.repeat(64)
    storeSessionToken(token)
    revokeSessionToken(token)

    const req = new Request('http://localhost/api/auth-logout', {
      method: 'POST',
      headers: { cookie: `hermes-auth=${token}` },
    })
    const res = await POST({ request: req })
    expect(res.status).toBe(200)
  })
})
```

(Adjust the handler import shape — TanStack Start routes export `Route` with `server.handlers.POST`. May need to extract the handler into a separate function and import that for testing. See existing `src/routes/api/auth.ts` pattern.)

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run src/test/routes/auth-logout.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the endpoint**

Create `src/routes/api/auth-logout.ts`:

```ts
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import {
  createSessionCookie,
  getSessionTokenFromCookie,
  revokeSessionToken,
} from '../../server/auth-middleware'

/**
 * Clear-cookie header to invalidate the browser's session copy.
 * Same flags as createSessionCookie so the browser accepts the clear.
 */
function clearSessionCookie(): string {
  return `hermes-auth=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0`
}

export const Route = createFileRoute('/api/auth-logout')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const cookieHeader = request.headers.get('cookie')
        const token = getSessionTokenFromCookie(cookieHeader)
        if (token) {
          revokeSessionToken(token)
        }
        return json(
          { ok: true },
          {
            status: 200,
            headers: { 'Set-Cookie': clearSessionCookie() },
          },
        )
      },
    },
  },
})
```

- [ ] **Step 4: Re-run tests**

```bash
npx vitest run src/test/routes/auth-logout.test.ts
```

Expected: PASS.

(If tests fail due to handler export pattern — `POST` is bound to the `Route` object, not exported directly — extract the handler logic into a named function `export async function handleLogout(request)` and have the route delegate to it. Re-import the function in the test.)

- [ ] **Step 5: Commit**

```bash
git add src/routes/api/auth-logout.ts src/test/routes/auth-logout.test.ts
git commit -m "feat(auth): add /api/auth-logout endpoint with session revoke"
```

---

## Task 5: Build `/login` Page

**Files:**
- Create: `src/routes/login.tsx`
- Create: `src/test/routes/login.test.tsx`

- [ ] **Step 1: Inspect existing page styles**

```bash
find src/routes -maxdepth 2 -name "*.tsx" | head -5
cat src/routes/index.tsx 2>/dev/null | head -30
```

Look for: how pages use `var(--theme-*)`, how form components are typically built, whether there's a shared `<Button>` or `<Input>` component.

- [ ] **Step 2: Write failing test**

Create `src/test/routes/login.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LoginPage } from '../../routes/login'  // extract component for testability

describe('LoginPage', () => {
  it('renders password input and submit button', () => {
    render(<LoginPage onAuthenticated={vi.fn()} />)
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('calls onAuthenticated on successful submit', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      headers: new Headers({ 'Set-Cookie': 'hermes-auth=abc; HttpOnly' }),
    })

    const onAuth = vi.fn()
    render(<LoginPage onAuthenticated={onAuth} />)

    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(onAuth).toHaveBeenCalled())
  })

  it('shows inline error on 401', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      status: 401,
      json: async () => ({ ok: false, error: 'Invalid password' }),
    })

    render(<LoginPage onAuthenticated={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(screen.getByText(/invalid/i)).toBeInTheDocument())
  })

  it('bypasses the form when already authenticated (useEffect auth-check)', async () => {
    // First call (useEffect) → /api/auth-check returns authenticated: true
    // Second call (form submit) → would also fire if form were not bypassed
    global.fetch = vi.fn().mockResolvedValue({
      status: 200,
      json: async () => ({ authenticated: true, authRequired: true }),
    })

    const onAuth = vi.fn()
    render(<LoginPage onAuthenticated={onAuth} />)

    // The useEffect runs after mount; wait for the callback to fire
    await waitFor(() => expect(onAuth).toHaveBeenCalledTimes(1))

    // User should NOT have to interact with the form — query for the password
    // input should still render initially (the redirect happens post-mount), but
    // the critical assertion is that onAuthenticated was called automatically.
    expect(global.fetch).toHaveBeenCalledWith('/api/auth-check', expect.objectContaining({ credentials: 'include' }))
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

```bash
npx vitest run src/test/routes/login.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement the page**

Create `src/routes/login.tsx`. **Key constraints:**
- **No loader / SSR data fetching.** Page shell hydrates instantly; auth-check runs in `useEffect`.
- On mount `useEffect`: fetch `/api/auth-check`. If `authenticated: true`, navigate to `/`.
- On submit: POST `/api/auth` with `{ password }`. On 200, call `props.onAuthenticated()` (parent handles navigation). On 401, show error inline.
- Style with `var(--theme-*)` tokens to match the rest of the studio.

Suggested shape:

```tsx
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    // Client-side auth-check on mount: already-authenticated users skip the form
    fetch('/api/auth-check', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        if (data.authenticated) onAuthenticated()
      })
      .catch(() => {})  // network errors here are non-fatal — user can still try to log in
  }, [onAuthenticated])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch('/api/auth', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.ok) {
        onAuthenticated()
      } else {
        const body = await res.json().catch(() => ({}))
        setError(body.error || 'Authentication failed')
      }
    } catch {
      setError('Network error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page" style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100vh', padding: '1rem',
    }}>
      <form onSubmit={handleSubmit} style={{
        width: '100%', maxWidth: 360,
        padding: '2rem', borderRadius: 12,
        background: 'var(--theme-bg-surface, #1a1a1a)',
        boxShadow: '0 4px 24px rgba(0,0,0,0.2)',
      }}>
        <h1 style={{ marginBottom: '1.5rem', fontSize: '1.5rem' }}>Sign in</h1>
        <label htmlFor="password" style={{ display: 'block', marginBottom: '0.5rem' }}>
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
          autoComplete="current-password"
          disabled={submitting}
          style={{
            width: '100%', padding: '0.75rem', marginBottom: '1rem',
            borderRadius: 8, border: '1px solid var(--theme-border, #333)',
            background: 'var(--theme-bg-input, #0f0f0f)',
            color: 'var(--theme-text, #eee)',
          }}
        />
        {error && (
          <div role="alert" style={{ color: 'var(--theme-error, #e55)', marginBottom: '1rem' }}>
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting || !password}
          style={{
            width: '100%', padding: '0.75rem', borderRadius: 8,
            background: 'var(--theme-primary, #4f8ef7)',
            color: 'white', border: 'none', cursor: submitting ? 'not-allowed' : 'pointer',
          }}
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}

export const Route = createFileRoute('/login')({
  component: LoginRouteComponent,
})

function LoginRouteComponent() {
  const navigate = useNavigate()
  return <LoginPage onAuthenticated={() => navigate({ to: '/', replace: true })} />
}
```

- [ ] **Step 5: Re-run tests**

```bash
npx vitest run src/test/routes/login.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/routes/login.tsx src/test/routes/login.test.tsx
git commit -m "feat(auth): add /login page with password form"
```

---

## Task 6: Add SSR Gate via TanStack Start Server Middleware

**Why this task uses `createMiddleware` instead of `beforeLoad`:**

`beforeLoad` runs both server-side and client-side, but the `request` object is not directly available in `beforeLoad` on the server — only `location` and `context`. Synthesizing a `Request` from `location` is brittle: it can lose the `Cookie` header and cause infinite redirect loops. **TanStack Start's `createMiddleware().server(...)`** is the documented pattern that receives the actual `Request` on the server, can read cookies directly, and can throw `redirect()` to produce a real 302 before any UI bytes ship.

**Files:**
- Create: `src/server/auth-gate-middleware.ts`
- Modify: `src/router.tsx` (or wherever the TanStack Start server pipeline is initialized — confirm by reading `src/main.tsx` / `src/server.tsx` if present)

- [ ] **Step 1: Locate the TanStack Start server pipeline entrypoint**

```bash
cd .worktrees/restrict-studio-access && find src/ -maxdepth 3 -type f \( -name "*.ts" -o -name "*.tsx" \) | xargs grep -lE "createStartHandler|createMiddleware|defaultPendingComponent" 2>/dev/null
```

Read whichever file comes up — it should show how the router is built and where server middleware would slot in. Likely candidates: `src/router.tsx`, `src/main.tsx`, or `src/server.tsx`. Note the actual entrypoint file path for Step 4.

- [ ] **Step 2: Verify both `createMiddleware` and `redirect` resolve from the expected package**

```bash
cd .worktrees/restrict-studio-access && \
  grep -E "createMiddleware|createStartHandler|^\s*export.*redirect" node_modules/@tanstack/react-start/dist/esm/index.d.ts 2>/dev/null | head -10
```

Confirm both `createMiddleware` AND `redirect` are exported from `@tanstack/react-start` (TanStack Start re-exports `redirect` from `@tanstack/react-router`). If absent, try alternative package names (`@tanstack/start`, `@tanstack/react-router`) and grep each. **Record both actual import paths** in a scratch comment for Step 5 — the middleware import AND the redirect import. Mixing them (e.g. `redirect` from `@tanstack/react-router` when the framework expects `@tanstack/react-start`) will surface as a confusing TS error at Step 8.

- [ ] **Step 3: Write failing test for the gate middleware**

Create `src/test/server/auth-gate-middleware.test.ts`:

```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { authGateMiddleware } from '../../server/auth-gate-middleware'

function invoke(request: Request) {
  // The middleware contract is: ({ request, next }) => next() or throw.
  // We pass a stub `next` and assert what it receives.
  return authGateMiddleware({
    request,
    next: async (ctx?: unknown) =>
      new Response(JSON.stringify({ passedThrough: true, ctx }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  } as any)
}

describe('authGateMiddleware', () => {
  beforeEach(() => { process.env.HERMES_PASSWORD = 'pw' })
  afterEach(() => { delete process.env.HERMES_PASSWORD })

  it('passes through exempt paths', async () => {
    for (const path of ['/login', '/api/auth', '/api/auth-check', '/api/auth-logout', '/health']) {
      const res = await invoke(new Request(`http://localhost${path}`))
      expect(res.status).toBe(200)
    }
  })

  it('passes through when HERMES_PASSWORD is unset', async () => {
    delete process.env.HERMES_PASSWORD
    const res = await invoke(new Request('http://localhost/dashboard'))
    expect(res.status).toBe(200)
  })

  it('throws redirect to /login when unauthenticated on a protected path', async () => {
    await expect(
      invoke(new Request('http://localhost/dashboard')),
    ).rejects.toMatchObject({ status: 302, headers: { Location: '/login' } })
    // (If the redirect type doesn't expose .status/.headers directly, assert on the redirect target by catching the thrown object.)
  })

  it('passes through when authenticated cookie is present', async () => {
    const { storeSessionToken } = await import('../../server/auth-middleware')
    const token = 'd'.repeat(64)
    storeSessionToken(token)
    const res = await invoke(
      new Request('http://localhost/dashboard', {
        headers: { cookie: `hermes-auth=${token}` },
      }),
    )
    expect(res.status).toBe(200)
  })
})
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd .worktrees/restrict-studio-access && npx vitest run src/test/server/auth-gate-middleware.test.ts
```

Expected: FAIL — `auth-gate-middleware.ts` does not exist.

- [ ] **Step 5: Implement the middleware**

Create `src/server/auth-gate-middleware.ts`:

```ts
import { redirect } from '@tanstack/react-router'
import {
  isAuthenticated,
  isPasswordProtectionEnabled,
} from './auth-middleware'

/**
 * Paths that must NEVER require authentication.
 * - /login: the login page itself
 * - /api/auth, /api/auth-check, /api/auth-logout: auth endpoints
 * - /health: Docker HEALTHCHECK probe
 */
const EXEMPT_PATHS: ReadonlySet<string> = new Set([
  '/login',
  '/api/auth',
  '/api/auth-check',
  '/api/auth-logout',
  '/health',
])

export type AuthGateContext = {
  /** Set to true for unauthenticated visitors who were redirected to /login */
  authRedirected?: boolean
}

export const authGateMiddleware = createMiddleware().server<AuthGateContext>(async ({
  request,
  next,
}) => {
  const pathname = new URL(request.url).pathname

  // Exempt paths always pass through.
  if (EXEMPT_PATHS.has(pathname)) return next()

  // No password configured → studio is publicly accessible (dev mode).
  if (!isPasswordProtectionEnabled()) return next()

  // Password is configured — must have a valid session cookie.
  if (isAuthenticated(request)) return next()

  // Unauthenticated visitor — redirect to /login.
  // `throw redirect(...)` produces a real 302 on the SSR path with no UI bytes shipped.
  throw redirect({ to: '/login', replace: true })
})
```

**Note:** the exact signature of `createMiddleware().server(...)` may differ slightly across TanStack Start versions. If `createMiddleware` is not the right import name, use whatever the package exports (Step 2 verified this). The key contract is: receive `{ request, next }`, call `next()` to continue, `throw redirect(...)` to short-circuit.

- [ ] **Step 6: Re-run tests**

```bash
cd .worktrees/restrict-studio-access && npx vitest run src/test/server/auth-gate-middleware.test.ts
```

Expected: PASS.

- [ ] **Step 7: Register the middleware in the TanStack Start pipeline**

In the file identified in Step 1, register the middleware at the top of the server handler chain. The exact API varies by TanStack Start version — the canonical pattern is:

```ts
// src/router.tsx or src/server.tsx (adjust to actual location)
import { authGateMiddleware } from './server/auth-gate-middleware'

// If using createStartHandler:
export const startHandler = createStartHandler({
  // ... existing config
  middleware: [authGateMiddleware],  // register first — runs before everything else
})

// If middleware is registered per-router (TanStack Router):
const router = createRouter({
  // ... existing config
  routeTree,
})

// Add the middleware to the server config (location depends on framework version).
```

**Verify in code, not in this plan** — read the entrypoint file from Step 1 and slot the middleware in following whatever pattern is already there for any existing middleware.

**Fallback if neither shape fits** (no global middleware slot is exposed): gate at the route level by wrapping each protected route's `loader` (and `server.handlers` for `/api/*`) with the same exempt-paths check. This is functionally equivalent (each request still runs the gate before UI/data is shipped) but requires touching ~43 route files instead of one entrypoint. Trigger condition for the fallback: if Step 1's grep finds `createRouter` and `createRoute` patterns with no obvious middleware-registration slot, and there's no example middleware in the existing codebase, switch to the per-route loader approach. Document which path you took in the commit body.

- [ ] **Step 8: Type check**

```bash
cd .worktrees/restrict-studio-access && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 9: Manual SSR test — both gated and ungated modes**

Start dev server with a tracked PID so we can reliably stop it:

```bash
cd .worktrees/restrict-studio-access && HERMES_PASSWORD=test npm run dev & echo $! > /tmp/dev.pid; sleep 5
```

Probe a protected path unauthenticated:
```bash
curl -I http://localhost:3000/dashboard
# Expected: HTTP/1.1 302 with Location: /login
```

Probe exempt paths (must NOT redirect):
```bash
curl -I http://localhost:3000/login
# Expected: 200
curl -I http://localhost:3000/api/auth-check
# Expected: 200 (or 503 if agent down, but not 302)
curl -I http://localhost:3000/health
# Expected: 200
```

Stop the server, then re-test with `HERMES_PASSWORD` unset:
```bash
kill "$(cat /tmp/dev.pid)" 2>/dev/null; rm -f /tmp/dev.pid
HERMES_PASSWORD= npm run dev & echo $! > /tmp/dev.pid; sleep 5
curl -I http://localhost:3000/dashboard
# Expected: 200 (no redirect — gate is disabled when password is unset)
kill "$(cat /tmp/dev.pid)" 2>/dev/null; rm -f /tmp/dev.pid
```

If you see 200 when the password IS set, the middleware is not wired in correctly — debug before proceeding. The SSR gate is the security-critical core of this feature.

- [ ] **Step 10: Verify Dockerfile `/health` route exists (or add it)**

```bash
cd .worktrees/restrict-studio-access && grep -rEn "createFileRoute\(['\"]/health['\"]" src/
```

If the route exists, `/health` will be served. If it does NOT exist but the Dockerfile has a `HEALTHCHECK` probing `/health`, the gate would 503 health checks. Add a minimal `/health` route handler if missing:

```ts
// src/routes/health.ts (only if missing)
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'

export const Route = createFileRoute('/health')({
  server: {
    handlers: {
      GET: async () => json({ status: 'ok' }),
    },
  },
})
```

- [ ] **Step 11: Commit**

```bash
cd .worktrees/restrict-studio-access && git add src/server/auth-gate-middleware.ts src/test/server/auth-gate-middleware.test.ts src/router.tsx src/main.tsx src/server.tsx src/routes/health.ts 2>/dev/null
git commit -m "feat(auth): add SSR gate middleware redirecting unauthenticated visitors to /login"
```

---

## Task 7: Build Logout Button Component

**Files:**
- Create: `src/components/logout-button.tsx`

(No dedicated test — exercised via Task 8.)

- [ ] **Step 1: Implement the component**

Create `src/components/logout-button.tsx`:

```tsx
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

export function LogoutButton() {
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  async function handleClick() {
    if (busy) return
    setBusy(true)
    try {
      await fetch('/api/auth-logout', { method: 'POST', credentials: 'include' })
    } catch {
      // even if logout fails server-side, navigate away
    } finally {
      setBusy(false)
      navigate({ to: '/login', replace: true })
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      style={{
        padding: '0.4rem 0.8rem',
        borderRadius: 6,
        background: 'transparent',
        border: '1px solid var(--theme-border, #444)',
        color: 'var(--theme-text-muted, #aaa)',
        cursor: busy ? 'not-allowed' : 'pointer',
        fontSize: '0.875rem',
      }}
    >
      {busy ? 'Signing out…' : 'Sign out'}
    </button>
  )
}
```

- [ ] **Step 2: Verify it builds**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/logout-button.tsx
git commit -m "feat(auth): add LogoutButton component"
```

---

## Task 8: Mount LogoutButton in Existing Nav

**Files:** (TBD — locate the nav component) Likely `src/components/nav.tsx`, `src/components/app-shell.tsx`, or `src/routes/__root.tsx` itself.

- [ ] **Step 1: Locate the nav/header**

```bash
cd .worktrees/restrict-studio-access && grep -rln "className=\"nav\|nav-header\|app-header" src/ --include='*.tsx' | head -5
```

If nothing matches, check `__root.tsx` for `<Outlet />` surroundings and any header markup.

- [ ] **Step 2: Add `<LogoutButton />` to the header**

Insert `<LogoutButton />` in a sensible spot — typically the right side of the nav bar. If there's no obvious spot, add it just before the closing `</header>` or `</nav>`.

```tsx
import { LogoutButton } from '../components/logout-button'

// ... in JSX:
<LogoutButton />
```

- [ ] **Step 3: Type check + build**

```bash
npx tsc --noEmit && npm run build
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/components/ src/routes/__root.tsx  # whichever files changed
git commit -m "feat(auth): mount LogoutButton in app nav"
```

---

## Task 9: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Read current `.env.example`**

```bash
cat .env.example
```

- [ ] **Step 2: Add auth-related documentation**

Append to `.env.example`:

```bash
# ═══════════════════════════════════════════════════════════════
# Studio Access Control
# ═══════════════════════════════════════════════════════════════
# When unset, /login is bypassed and the studio is publicly accessible.
# When set, all routes (except /login and the auth endpoints) require this
# password. Set it BEFORE first use in production.
#
# HERMES_PASSWORD=

# Optional: identity to associate with this deployment's sessions.
# Currently single-user only. Falls back to "studio" if unset.
# HERMES_USER_ID=
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: document HERMES_PASSWORD + HERMES_USER_ID env vars"
```

---

## Task 10: Cosmetic Fix for `/api/auth-check`

**Files:**
- Modify: `src/routes/api/auth-check.ts`

- [ ] **Step 1: Read current handler**

```bash
cat src/routes/api/auth-check.ts
```

- [ ] **Step 2: Apply the fix**

The current handler returns 503 with `authRequired: false` when the agent is unreachable. This conflates two states. Change so that `authRequired` always reflects `isPasswordProtectionEnabled()`, regardless of agent reachability:

```ts
const authRequired = isPasswordProtectionEnabled()
// ... rest unchanged
return json({
  authenticated,
  authRequired,  // now reflects env, not agent state
})
```

The 503 status when agent is down remains correct (so the UI knows to show "agent unreachable" instead of "logged out"), but the `authRequired` value is now accurate.

- [ ] **Step 3: Type check + tests**

```bash
npx tsc --noEmit && npx vitest run
```

- [ ] **Step 4: Confirm no client relies on the old `authRequired: false` semantics when agent is down**

Grep clients that consume `/api/auth-check`:
```bash
grep -rln "auth-check\|authRequired" src/ --include='*.ts' --include='*.tsx' | grep -v auth-check.ts | grep -v auth-middleware.ts
```

If a client branches on `authRequired === false` to mean "agent down", document it but don't fix in this PR — leave a TODO.

- [ ] **Step 5: Commit**

```bash
git add src/routes/api/auth-check.ts
git commit -m "fix(auth): auth-check returns correct authRequired regardless of agent reachability"
```

---

## Task 11: Final Verification

- [ ] **Step 1: Full test suite**

```bash
npx vitest run
```

Expected: all tests pass.

- [ ] **Step 2: Type check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Build**

```bash
npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Local end-to-end test**

```bash
HERMES_PASSWORD=test-password npm run dev &
sleep 5
```

Then in another terminal:
```bash
# 1. Hit protected page unauth — expect 302
curl -I http://localhost:3000/dashboard
# Expected: HTTP/1.1 302 with Location: /login

# 2. Hit exempt paths — expect 200
curl -I http://localhost:3000/login
curl -I http://localhost:3000/api/auth-check
# Expected: 200 (or 503 if agent down, but not 302)

# 3. Submit password via API — expect 200 + cookie
curl -i -X POST http://localhost:3000/api/auth \
  -H 'Content-Type: application/json' \
  -d '{"password":"test-password"}'
# Expected: HTTP/1.1 200, Set-Cookie: hermes-auth=...

# 4. Submit wrong password — expect 401
curl -i -X POST http://localhost:3000/api/auth \
  -H 'Content-Type: application/json' \
  -d '{"password":"wrong"}'
# Expected: HTTP/1.1 401 { ok: false, error: 'Invalid password' }

# 5. Hit protected page with cookie — expect 200
curl -I http://localhost:3000/dashboard \
  -H "Cookie: hermes-auth=$(curl -s -X POST http://localhost:3000/api/auth -H 'Content-Type: application/json' -d '{"password":"test-password"}' -i | grep -oP 'hermes-auth=\K[^;]+')"
# Expected: HTTP/1.1 200

# 6. Logout
curl -i -X POST http://localhost:3000/api/auth-logout \
  -H "Cookie: hermes-auth=<token-from-step-5>"
# Expected: HTTP/1.1 200, Set-Cookie: hermes-auth=; Max-Age=0

# 7. After logout, dashboard should redirect again
curl -I http://localhost:3000/dashboard \
  -H "Cookie: hermes-auth=<token-from-step-5>"
# Expected: 302 with Location: /login (token revoked server-side)
```

Kill dev server: `kill %1`

- [ ] **Step 5: Cleanup**

If any test artifacts (`/tmp/gated-routes.txt`, etc.) remain, remove.

---

## Task 12: Commit Plan and Push Branch

- [ ] **Step 1: Confirm all changes are committed**

```bash
git status
```

Expected: clean working tree.

- [ ] **Step 2: Push branch to remote**

```bash
git push -u origin feat/restrict-studio-access
```

- [ ] **Step 3: Hand off to user for deployment**

**⚠️ Member-role Coolify MCP cannot write env vars.** The `COOLIFY_TOKEN` configured for this Claude Code session is Member-role, which `mcp__coolify__application action=update` rejects with `"Missing required permissions: write"`. The env var must be set by a human in the Coolify UI, or the user must temporarily switch the MCP token to admin role (Influtics memory `feedback_coolify-mcp-read-only-member-token.md`).

**UI flow (manual):**
1. Open Coolify UI → Apps → `hermes-studio` → **Environment Variables**.
2. Add `HERMES_PASSWORD=<chosen-password>` (use a strong secret; this is the only credential protecting the studio).
3. Save. Coolify triggers a redeploy automatically (~2-5 min).
4. From this moment the studio is gated.

**Recovery (lockout):** set `HERMES_PASSWORD=` (empty) in the same UI → save → redeploy → `isPasswordProtectionEnabled()` returns false → studio reopens.

The branch is pushed; PR is optional (user may merge directly per repo conventions).

---

## Risks (carry-over from spec)

- **Docker HEALTHCHECK gating.** If `docker/workspace/Dockerfile` probes `/health`, that route must be in the exempt list. The `EXEMPT_PATHS` set in Task 6 includes `/health`. Verify the actual route exists; if not, no-op.
- **Token leak via logs.** Mid-implementation, grep `src/server/` for any logging that emits the token. If found, patch.
- **Login page SSR pattern.** The `useEffect`-based auth-check is non-negotiable — do not add a loader to `login.tsx`.

---

## YAGNI reminders

- Do NOT add a signup page, password reset, email verification.
- Do NOT add MFA / TOTP.
- Do NOT add IP allowlisting.
- Do NOT add a CSRF token system beyond what already exists (`SameSite=Strict` + JSON content-type guard).
- Do NOT add a "remember me" toggle — cookie is always 30 days.
- Do NOT add sliding session timeout — cookie is fixed 30 days.
