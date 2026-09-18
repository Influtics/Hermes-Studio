/**
 * POST /api/approvals/:approvalId/deny
 *
 * Denies a pending dangerous-command execution request.
 * Mirror of approvals.$approvalId.approve.ts — sends "/deny" to the session.
 */

import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../server/auth-middleware'
import { requireJsonContentType } from '../../server/rate-limit'
import {
  ensureGatewayProbed,
  getGatewayCapabilities,
  sendChat,
  HERMES_API,
} from '../../server/hermes-api'

export const Route = createFileRoute('/api/approvals/$approvalId/deny')({
  server: {
    handlers: {
      POST: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const csrfCheck = requireJsonContentType(request)
        if (csrfCheck) return csrfCheck

        const { approvalId } = params
        await ensureGatewayProbed()

        const colonIdx = approvalId.indexOf(':')
        const sessionKey =
          colonIdx > 0 ? approvalId.slice(0, colonIdx) : 'main'

        // Strategy 1: gateway native endpoint
        const caps = getGatewayCapabilities()
        if (caps.sessions) {
          try {
            const res = await fetch(
              `${HERMES_API}/api/sessions/${sessionKey}/deny`,
              {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: AbortSignal.timeout(5_000),
              },
            )
            if (res.ok) {
              return json({ ok: true, method: 'gateway-endpoint' })
            }
          } catch {
            // fall through
          }
        }

        // Strategy 2: chat command
        try {
          await sendChat(sessionKey, { message: '/deny' })
          return json({ ok: true, method: 'chat-command' })
        } catch (err) {
          return json(
            {
              ok: false,
              error:
                err instanceof Error ? err.message : 'Failed to send denial',
            },
            { status: 500 },
          )
        }
      },
    },
  },
})
