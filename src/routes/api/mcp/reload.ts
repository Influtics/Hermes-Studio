import { createFileRoute } from '@tanstack/react-router'
import { requireAuth } from '../../../server/auth-middleware'
import { BEARER_TOKEN, HERMES_API } from '../../../server/gateway-capabilities'

function authHeaders(): Record<string, string> {
  return BEARER_TOKEN ? { Authorization: `Bearer ${BEARER_TOKEN}` } : {}
}

const RELOAD_PATHS = ['/api/reload-mcp', '/api/mcp/reload']

export const Route = createFileRoute('/api/mcp/reload')({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        for (const path of RELOAD_PATHS) {
          try {
            const response = await fetch(`${HERMES_API}${path}`, {
              method: 'POST',
              headers: authHeaders(),
            })

            if (response.ok) {
              return Response.json({
                ok: true,
                message: 'MCP server reload requested.',
              })
            }
          } catch {
            // Try the next candidate endpoint.
          }
        }

        return Response.json({
          ok: false,
          message: 'Use /reload-mcp in chat to reload MCP servers.',
        })
      },
    },
  },
})
