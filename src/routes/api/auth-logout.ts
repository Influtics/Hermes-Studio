import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import {
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

/**
 * Extracted handler for testability. Route delegates to this.
 */
export async function handleLogout(request: Request): Promise<Response> {
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
}

export const Route = createFileRoute('/api/auth-logout')({
  server: {
    handlers: {
      POST: async ({ request }) => handleLogout(request),
    },
  },
})