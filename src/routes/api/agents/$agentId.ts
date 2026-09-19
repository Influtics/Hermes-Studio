/**
 * GET    /api/agents/:agentId  — get a single agent
 * PATCH  /api/agents/:agentId  — update a custom agent
 * DELETE /api/agents/:agentId  — delete a custom agent
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { requireJsonContentType } from '../../../server/rate-limit'
import {
  getAgent,
  updateAgent,
  deleteAgent,
} from '../../../server/agent-definitions-store'

export const Route = createFileRoute('/api/agents/$agentId')({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const agent = getAgent(params.agentId)
        if (!agent) return json({ ok: false, error: 'Not found' }, { status: 404 })
        return json({ ok: true, agent })
      },

      PATCH: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const csrfCheck = requireJsonContentType(request)
        if (csrfCheck) return csrfCheck

        const existing = getAgent(params.agentId)
        if (!existing) return json({ ok: false, error: 'Not found' }, { status: 404 })
        if (existing.isBuiltIn) {
          return json({ ok: false, error: 'Built-in agents cannot be modified' }, { status: 403 })
        }

        const body = (await request.json().catch(() => ({}))) as Record<string, unknown>
        const updates: Parameters<typeof updateAgent>[1] = {}

        if (typeof body.name === 'string' && body.name.trim()) {
          updates.name = body.name.trim().slice(0, 40)
        }
        if (typeof body.emoji === 'string' && body.emoji.trim()) {
          updates.emoji = body.emoji.trim()
        }
        if (typeof body.color === 'string') {
          updates.color = body.color
        }
        if (typeof body.roleLabel === 'string') {
          updates.roleLabel = body.roleLabel.trim()
        }
        if (typeof body.systemPrompt === 'string') {
          updates.systemPrompt = body.systemPrompt.trim()
        }
        if (body.model === null || (typeof body.model === 'string' && body.model.trim())) {
          updates.model = body.model === null ? null : (body.model as string).trim()
        }
        if (Array.isArray(body.tags)) {
          updates.tags = (body.tags as unknown[])
            .filter((t): t is string => typeof t === 'string')
            .slice(0, 10)
        }

        const agent = updateAgent(params.agentId, updates)
        if (!agent) return json({ ok: false, error: 'Not found' }, { status: 404 })
        return json({ ok: true, agent })
      },

      DELETE: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        const existing = getAgent(params.agentId)
        if (!existing) return json({ ok: false, error: 'Not found' }, { status: 404 })
        if (existing.isBuiltIn) {
          return json({ ok: false, error: 'Built-in agents cannot be deleted' }, { status: 403 })
        }

        const ok = deleteAgent(params.agentId)
        return json({ ok })
      },
    },
  },
})
