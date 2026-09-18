/**
 * GET    /api/crews/:crewId/workflow  — fetch workflow for a crew (null if none)
 * PUT    /api/crews/:crewId/workflow  — upsert workflow (tasks + edges)
 * DELETE /api/crews/:crewId/workflow  — remove workflow
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth } from '../../../server/auth-middleware'
import { requireJsonContentType } from '../../../server/rate-limit'
import { getCrew } from '../../../server/crew-store'
import {
  getWorkflow,
  upsertWorkflow,
  deleteWorkflow,
} from '../../../server/workflow-store'
import type { WorkflowTask, WorkflowEdge } from '../../../types/workflow'

// ─── Cycle detection (DFS) ──────────────────────────────────────────────────

function hasCycle(taskIds: string[], edges: WorkflowEdge[]): boolean {
  const adj = new Map<string, string[]>()
  for (const id of taskIds) adj.set(id, [])
  for (const e of edges) {
    const list = adj.get(e.from)
    if (list) list.push(e.to)
  }

  const WHITE = 0, GRAY = 1, BLACK = 2
  const color = new Map<string, number>()
  for (const id of taskIds) color.set(id, WHITE)

  function dfs(id: string): boolean {
    color.set(id, GRAY)
    for (const next of adj.get(id) ?? []) {
      const c = color.get(next)
      if (c === GRAY) return true
      if (c === WHITE && dfs(next)) return true
    }
    color.set(id, BLACK)
    return false
  }

  for (const id of taskIds) {
    if (color.get(id) === WHITE && dfs(id)) return true
  }
  return false
}

export const Route = createFileRoute('/api/crews/$crewId/workflow')({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        if (!getCrew(params.crewId)) {
          return json({ ok: false, error: 'Crew not found' }, { status: 404 })
        }
        return json({ ok: true, workflow: getWorkflow(params.crewId) })
      },

      PUT: async ({ request, params }) => {
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

        if (!Array.isArray(body.tasks) || !Array.isArray(body.edges)) {
          return json(
            { ok: false, error: 'tasks and edges must be arrays' },
            { status: 400 },
          )
        }

        const tasks = body.tasks as WorkflowTask[]
        const edges = body.edges as WorkflowEdge[]

        // Validate edge references
        const taskIds = new Set(tasks.map((t) => t.id))
        for (const e of edges) {
          if (!taskIds.has(e.from) || !taskIds.has(e.to)) {
            return json(
              { ok: false, error: 'Edge references unknown task id' },
              { status: 400 },
            )
          }
        }

        // Cycle detection
        if (hasCycle(Array.from(taskIds), edges)) {
          return json(
            { ok: false, error: 'Workflow contains a cycle' },
            { status: 400 },
          )
        }

        const workflow = upsertWorkflow(params.crewId, { tasks, edges })
        return json({ ok: true, workflow })
      },

      DELETE: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        deleteWorkflow(params.crewId)
        return json({ ok: true })
      },
    },
  },
})
