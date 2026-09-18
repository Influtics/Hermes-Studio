/**
 * GET    /api/crews/:crewId/usage — fetch cumulative token usage for a crew
 * POST   /api/crews/:crewId/usage — record a member's latest token snapshot
 * DELETE /api/crews/:crewId/usage — reset usage data for a crew
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { requireJsonContentType } from '../../../server/rate-limit'
import { getCrew } from '../../../server/crew-store'
import {
  getCrewUsage,
  recordMemberUsage,
  resetCrewUsage,
} from '../../../server/cost-store'

export const Route = createFileRoute('/api/crews/$crewId/usage')({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        if (!getCrew(params.crewId)) {
          return json({ ok: false, error: 'Crew not found' }, { status: 404 })
        }
        return json({ ok: true, usage: getCrewUsage(params.crewId) })
      },

      POST: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const csrfCheck = requireJsonContentType(request)
        if (csrfCheck) return csrfCheck

        if (!getCrew(params.crewId)) {
          return json({ ok: false, error: 'Crew not found' }, { status: 404 })
        }

        const body = (await request.json().catch(() => ({}))) as Record<
          string,
          unknown
        >

        const sessionKey =
          typeof body.sessionKey === 'string' ? body.sessionKey : ''
        if (!sessionKey) {
          return json(
            { ok: false, error: 'sessionKey is required' },
            { status: 400 },
          )
        }

        const displayName =
          typeof body.displayName === 'string' ? body.displayName : sessionKey
        const model =
          typeof body.model === 'string' ? body.model : null
        const inputTokens =
          typeof body.inputTokens === 'number' ? Math.max(0, body.inputTokens) : 0
        const outputTokens =
          typeof body.outputTokens === 'number' ? Math.max(0, body.outputTokens) : 0

        const usage = recordMemberUsage(
          params.crewId,
          sessionKey,
          displayName,
          model,
          inputTokens,
          outputTokens,
        )
        return json({ ok: true, usage })
      },

      DELETE: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        resetCrewUsage(params.crewId)
        return json({ ok: true })
      },
    },
  },
})
