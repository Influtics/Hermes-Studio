import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

vi.mock('@/server/redis-client', () => ({
  getRedisClient: () => Promise.resolve(null),
  getRedisClientSync: () => null,
}))

import { handleLogout } from '../../routes/api/auth-logout'
import {
  generateSessionToken,
  storeSessionToken,
  isValidSessionToken,
  revokeSessionToken,
} from '../../server/auth-middleware'

beforeEach(() => {
  delete process.env.HERMES_PASSWORD
})

afterEach(() => {
  delete process.env.HERMES_PASSWORD
  revokeSessionToken('b'.repeat(64))
  revokeSessionToken('c'.repeat(64))
})

describe('POST /api/auth-logout', () => {
  it('returns 200 with clearing cookie when no session', async () => {
    const req = new Request('http://localhost/api/auth-logout', {
      method: 'POST',
    })
    const res = await handleLogout(req)
    expect(res.status).toBe(200)
    expect(res.headers.get('Set-Cookie')).toMatch(/hermes-auth=.*Max-Age=0/)
    expect(res.headers.get('Set-Cookie')).toMatch(/HttpOnly/i)
    expect(res.headers.get('Set-Cookie')).toMatch(/SameSite=Strict/i)
    expect(res.headers.get('Set-Cookie')).toMatch(/Path=\//i)
  })

  it('returns 200 and revokes token when session valid', async () => {
    const token = 'b'.repeat(64)
    storeSessionToken(token)
    expect(isValidSessionToken(token)).toBe(true)

    const req = new Request('http://localhost/api/auth-logout', {
      method: 'POST',
      headers: { cookie: `hermes-auth=${token}` },
    })
    const res = await handleLogout(req)

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
    const res = await handleLogout(req)
    expect(res.status).toBe(200)
    expect(res.headers.get('Set-Cookie')).toMatch(/Max-Age=0/)
  })

  it('body is { ok: true }', async () => {
    const res = await handleLogout(
      new Request('http://localhost/api/auth-logout', { method: 'POST' }),
    )
    const body = await res.json()
    expect(body).toEqual({ ok: true })
  })
})