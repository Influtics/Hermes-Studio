import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { searchKnowledgePages } from '../../../server/knowledge-browser'

export const Route = createFileRoute('/api/knowledge/search')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        const url = new URL(request.url)
        const query = url.searchParams.get('q') || ''

        try {
          return json({ results: searchKnowledgePages(query) })
        } catch (error) {
          return json(
            {
              error:
                error instanceof Error
                  ? error.message
                  : 'Failed to search knowledge pages',
            },
            { status: 500 },
          )
        }
      },
    },
  },
})
