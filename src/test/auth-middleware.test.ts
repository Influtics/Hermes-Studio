import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// We test the pure, non-Redis-dependent exports directly.
// Redis interactions are triggered only on module load; we suppress them
// by mocking the redis-client module before importing auth-middleware.

vi.mock('@/server/redis-client', () => ({
  getRedisClient: () => Promise.resolve(null),
  getRedisClientSync: () => null,
}))

import {
  generateSessionToken,
  storeSessionToken,
  isValidSessionToken,
  revokeSessionToken,
  isPasswordProtectionEnabled,
  verifyPassword,
  getSessionTokenFromCookie,
  isAuthenticated,
  createSessionCookie,
  requireAuth,
} from '@/server/auth-middleware'

beforeEach(() => {
  delete process.env.HERMES_PASSWORD
})

afterEach(() => {
  delete process.env.HERMES_PASSWORD
  vi.restoreAllMocks()
})

describe('generateSessionToken()', () => {
  it('returns a 64-character hex string', () => {
    const token = generateSessionToken()
    expect(token).toMatch(/^[0-9a-f]{64}$/)
  })

  it('generates unique tokens each call', () => {
    expect(generateSessionToken()).not.toBe(generateSessionToken())
  })
})

describe('storeSessionToken() / isValidSessionToken() / revokeSessionToken()', () => {
  it('stored token is valid', () => {
    const token = generateSessionToken()
    storeSessionToken(token)
    expect(isValidSessionToken(token)).toBe(true)
  })

  it('unknown token is invalid', () => {
    expect(isValidSessionToken('not-a-real-token')).toBe(false)
  })

  it('revoked token is no longer valid', () => {
    const token = generateSessionToken()
    storeSessionToken(token)
    revokeSessionToken(token)
    expect(isValidSessionToken(token)).toBe(false)
  })
})

describe('isPasswordProtectionEnabled()', () => {
  it('returns false when HERMES_PASSWORD is not set', () => {
    expect(isPasswordProtectionEnabled()).toBe(false)
  })

  it('returns true when HERMES_PASSWORD is set', () => {
    process.env.HERMES_PASSWORD = 'secret'
    expect(isPasswordProtectionEnabled()).toBe(true)
  })

  it('returns false when HERMES_PASSWORD is empty string', () => {
    process.env.HERMES_PASSWORD = ''
    expect(isPasswordProtectionEnabled()).toBe(false)
  })
})

describe('verifyPassword()', () => {
  it('returns false when no password is configured', () => {
    expect(verifyPassword('anything')).toBe(false)
  })

  it('returns true for correct password', () => {
    process.env.HERMES_PASSWORD = 'correct'
    expect(verifyPassword('correct')).toBe(true)
  })

  it('returns false for wrong password', () => {
    process.env.HERMES_PASSWORD = 'correct'
    expect(verifyPassword('wrong')).toBe(false)
  })

  it('returns false for password with different length (timing-safe path)', () => {
    process.env.HERMES_PASSWORD = 'short'
    expect(verifyPassword('much-longer-password')).toBe(false)
  })
})

describe('getSessionTokenFromCookie()', () => {
  it('returns null for null cookie header', () => {
    expect(getSessionTokenFromCookie(null)).toBeNull()
  })

  it('extracts hermes-auth token from cookie string', () => {
    expect(getSessionTokenFromCookie('hermes-auth=abc123')).toBe('abc123')
  })

  it('extracts token when multiple cookies are present', () => {
    expect(
      getSessionTokenFromCookie('other=xyz; hermes-auth=mytoken; foo=bar'),
    ).toBe('mytoken')
  })

  it('returns null when hermes-auth cookie is absent', () => {
    expect(getSessionTokenFromCookie('session=abc; foo=bar')).toBeNull()
  })

  it('returns empty string for empty hermes-auth value', () => {
    expect(getSessionTokenFromCookie('hermes-auth=')).toBe('')
  })
})

describe('isAuthenticated()', () => {
  function makeRequest(cookie?: string): Request {
    const headers: Record<string, string> = {}
    if (cookie) headers['cookie'] = cookie
    return new Request('http://localhost/api/test', { headers })
  }

  it('returns true when no password is configured', () => {
    expect(isAuthenticated(makeRequest())).toBe(true)
  })

  it('returns false when password is set and no cookie provided', () => {
    process.env.HERMES_PASSWORD = 'secret'
    expect(isAuthenticated(makeRequest())).toBe(false)
  })

  it('returns false when password is set and cookie has wrong token', () => {
    process.env.HERMES_PASSWORD = 'secret'
    expect(isAuthenticated(makeRequest('hermes-auth=bad-token'))).toBe(false)
  })

  it('returns true when password is set and cookie has valid token', () => {
    process.env.HERMES_PASSWORD = 'secret'
    const token = generateSessionToken()
    storeSessionToken(token)
    expect(isAuthenticated(makeRequest(`hermes-auth=${token}`))).toBe(true)
    revokeSessionToken(token)
  })
})

describe('createSessionCookie()', () => {
  it('includes the token in the cookie value', () => {
    expect(createSessionCookie('mytoken')).toContain('hermes-auth=mytoken')
  })

  it('is HttpOnly', () => {
    expect(createSessionCookie('t').toLowerCase()).toContain('httponly')
  })

  it('has SameSite=Strict', () => {
    expect(createSessionCookie('t')).toContain('SameSite=Strict')
  })

  it('has Path=/', () => {
    expect(createSessionCookie('t')).toContain('Path=/')
  })

  it('has a Max-Age value', () => {
    expect(createSessionCookie('t')).toContain('Max-Age=')
  })
})

describe('requireAuth()', () => {
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

  it('returns null when password is set and the request carries a valid cookie', () => {
    const token = generateSessionToken()
    storeSessionToken(token)
    const req = new Request('http://localhost/test', {
      headers: { cookie: `hermes-auth=${token}` },
    })
    expect(requireAuth(req)).toBeNull()
    revokeSessionToken(token)
  })

  it('returns a 401 Response when password is set and the cookie is missing', () => {
    const req = new Request('http://localhost/test')
    const res = requireAuth(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('401 body has shape { ok: false, code: "UNAUTHENTICATED", error: string }', async () => {
    const req = new Request('http://localhost/test')
    const res = requireAuth(req)
    expect(res).not.toBeNull()
    const body = await res!.json()
    expect(body).toMatchObject({ ok: false, code: 'UNAUTHENTICATED' })
    expect(typeof body.error).toBe('string')
  })
})
