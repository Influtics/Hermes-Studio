import { describe, it, expect, beforeEach, afterEach } from 'vitest'

// We test the pure, non-Redis-dependent exports directly.
// Redis interactions are triggered only on module load; we suppress them
// by mocking the redis-client module before importing auth-middleware.

import { authGateMiddleware } from '../../server/auth-gate-middleware'

type NextFn = (ctx?: unknown) => Promise<Response>

function invoke(request: Request, next: NextFn = stubNext) {
  return authGateMiddleware({
    request,
    next,
  } as any)
}

async function stubNext(ctx?: unknown) {
  return new Response(JSON.stringify({ passedThrough: true, ctx }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('authGateMiddleware', () => {
  beforeEach(() => {
    process.env.HERMES_PASSWORD = 'pw'
  })
  afterEach(() => {
    delete process.env.HERMES_PASSWORD
  })

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
    let thrown: unknown = null
    try {
      await invoke(new Request('http://localhost/dashboard'))
    } catch (e) {
      thrown = e
    }
    expect(thrown).toBeInstanceOf(Response)
    const res = thrown as Response
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toBe('/login')
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