import {
  isAuthenticated,
  isPasswordProtectionEnabled,
} from './auth-middleware'

/**
 * Paths that must be reachable without authentication.
 *
 * /login               — the auth form itself, can't redirect to itself
 * /api/auth            — POST {password}, used by the login form
 * /api/auth-check      — GET status for the client to know if it's already authed
 * /api/auth-logout     — POST clears the session cookie
 * /health              — liveness probe; must always return 200
 *
 * Anything else (HTML pages, JSON APIs, server fns) is gated when HERMES_PASSWORD is set.
 *
 * Static assets under /assets/* and /favicon.ico are exempted by prefix match
 * (below) — they are hashed/immutable and must load so the login page can render.
 */
const EXEMPT_PATHS: ReadonlySet<string> = new Set([
  '/login',
  '/api/auth',
  '/api/auth-check',
  '/api/auth-logout',
  '/health',
])

/** Path prefixes that bypass the gate. */
const EXEMPT_PREFIXES: readonly string[] = ['/assets/', '/favicon.ico']

export type AuthGateNext = (ctx?: unknown) => Promise<unknown>
export type AuthGateInvocation = {
  request: Request
  next: AuthGateNext
}

function isExempt(pathname: string): boolean {
  if (EXEMPT_PATHS.has(pathname)) return true
  for (const prefix of EXEMPT_PREFIXES) {
    if (pathname.startsWith(prefix)) return true
  }
  return false
}

/**
 * SSR auth-gate middleware.
 *
 * Contract:
 *   receive({ request, next }) →
 *     - next()        : continue to next middleware / route handler
 *     - throw redirect: short-circuit with a 302 to /login
 *
 * Registration: this project uses a minimal TanStack Start setup without
 * `createStart()` (router is built via `createRouter` only), so request
 * middleware has no built-in global registration slot. The middleware is
 * invoked from:
 *   - vite.config.ts dev server (vite middleware chain runs before TanStack
 *     Start's request handler)
 *   - server-entry.js production wrapper (runs before `server.fetch(request)`)
 *
 * Both intercept the request before any UI bytes ship — a 302 short-circuits
 * before the SSR renderer runs.
 *
 * The middleware is exported as a plain function (not `createMiddleware().server()`)
 * because that factory is only meaningful when paired with `createStart()`.
 */
export async function authGateMiddleware({
  request,
  next,
}: AuthGateInvocation): Promise<Response> {
  const pathname = new URL(request.url).pathname

  if (isExempt(pathname)) {
    return (await next()) as Response
  }
  if (!isPasswordProtectionEnabled()) {
    return (await next()) as Response
  }
  if (isAuthenticated(request)) {
    return (await next()) as Response
  }

  // Throw a plain 302 Response with the Location header already set.
  //
  // Why not TanStack Router's redirect() helper: that helper returns a
  // Response carrying a synthetic `options` field (to/replace/statusCode)
  // — the router framework reads `options` later via isRedirect() and
  // synthesizes the actual redirect Response. Here we don't have the
  // router framework in the loop: vite's middleware chain (dev) and
  // server-entry.js (prod) catch the thrown value directly and return
  // it as the HTTP response. So we return a ready-to-serve 302 with
  // Location set in place.
  //
  // Status 302 (Temporary Redirect) — auth state can change, so the
  // redirect is conditional, not permanent. Curl/browsers handle it as
  // "follow this one and try again".
  throw new Response(null, {
    status: 302,
    headers: { Location: '/login' },
  })
}