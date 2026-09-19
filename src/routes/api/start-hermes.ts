import { json } from '@tanstack/react-start'
import { createFileRoute } from '@tanstack/react-router'
import { requireAuth } from '../../server/auth-middleware'
import { startHermesAgent } from '../../server/hermes-agent'

export const Route = createFileRoute('/api/start-hermes')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        try {
          const authError = requireAuth(request)
          if (authError) return authError

          const result = await startHermesAgent()
          return json(result, { status: result.ok ? 200 : 500 })
        } catch (err) {
          return json(
            {
              ok: false,
              error: err instanceof Error ? err.message : String(err),
            },
            { status: 500 },
          )
        }
      },
    },
  },
})
