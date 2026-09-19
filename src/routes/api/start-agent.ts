import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../server/auth-middleware'
import { startHermesAgent } from '../../server/hermes-agent'

export const Route = createFileRoute('/api/start-agent')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        const result = await startHermesAgent()
        return json(result, { status: result.ok ? 200 : 500 })
      },
    },
  },
})
