/**
 * GET /api/operations — aggregated agent overview across crews and missions
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { getOperationsOverview } from '../../../server/operations-aggregator'

export const Route = createFileRoute('/api/operations/')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        return json({ ok: true, agents: await getOperationsOverview() })
      },
    },
  },
})
