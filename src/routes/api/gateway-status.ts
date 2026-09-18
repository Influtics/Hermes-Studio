import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../server/auth-middleware'
import {
  HERMES_API,
  ensureGatewayProbed,
  getCapabilities,
} from '../../server/gateway-capabilities'

export const Route = createFileRoute('/api/gateway-status')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        const capabilities = await ensureGatewayProbed()
        return json({
          capabilities,
          hermesUrl: HERMES_API,
        })
      },
    },
  },
})
