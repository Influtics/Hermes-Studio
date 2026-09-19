import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { ensureGatewayProbed } from '../../../server/gateway-capabilities'
import { searchMemoryFiles } from '../../../server/memory-browser'

export const Route = createFileRoute('/api/memory/search')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        const url = new URL(request.url)
        const query = url.searchParams.get('q') || ''
        try {
          return json({ results: searchMemoryFiles(query) })
        } catch (error) {
          return json(
            {
              error:
                error instanceof Error
                  ? error.message
                  : 'Failed to search memory files',
            },
            { status: 500 },
          )
        }
      },
    },
  },
})
