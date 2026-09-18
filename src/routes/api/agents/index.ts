/**
 * GET  /api/agents   — list all agents (built-ins + custom)
 * POST /api/agents   — create a custom agent
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { requireJsonContentType } from '../../../server/rate-limit'
import {
  listAgents,
  createAgent,
} from '../../../server/agent-definitions-store'

const VALID_COLORS = [
  'text-blue-400',
  'text-purple-400',
  'text-orange-400',
  'text-emerald-400',
  'text-amber-400',
  'text-cyan-400',
  'text-yellow-400',
  'text-red-400',
  'text-pink-400',
  'text-indigo-400',
  'text-teal-400',
  'text-lime-400',
  'text-rose-400',
  'text-violet-400',
  'text-sky-400',
  'text-green-400',
]

export const Route = createFileRoute('/api/agents/')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        return json({ ok: true, agents: listAgents() })
      },

      POST: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const csrfCheck = requireJsonContentType(request)
        if (csrfCheck) return csrfCheck

        const body = (await request.json().catch(() => ({}))) as Record<string, unknown>

        const name = typeof body.name === 'string' ? body.name.trim() : ''
        if (!name) {
          return json({ ok: false, error: 'name is required' }, { status: 400 })
        }
        if (name.length > 40) {
          return json({ ok: false, error: 'name must be ≤ 40 characters' }, { status: 400 })
        }

        const emoji = typeof body.emoji === 'string' && body.emoji.trim() ? body.emoji.trim() : '🤖'
        const color = typeof body.color === 'string' && VALID_COLORS.includes(body.color)
          ? body.color
          : 'text-blue-400'
        const roleLabel = typeof body.roleLabel === 'string' ? body.roleLabel.trim() : 'Custom Agent'
        const systemPrompt = typeof body.systemPrompt === 'string' ? body.systemPrompt.trim() : ''
        const model = typeof body.model === 'string' && body.model.trim() ? body.model.trim() : null
        const tags = Array.isArray(body.tags)
          ? (body.tags as unknown[]).filter((t): t is string => typeof t === 'string').slice(0, 10)
          : []

        const agent = createAgent({ name, emoji, color, roleLabel, systemPrompt, model, tags })
        return json({ ok: true, agent }, { status: 201 })
      },
    },
  },
})
