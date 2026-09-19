/**
 * GET  /api/tasks   — list tasks with optional filters (column, assignee, priority, sourceType, sourceId)
 * POST /api/tasks   — create a task (requires title)
 */
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import { requireAuth, getUserIdFromRequest } from '../../../server/auth-middleware'
import { requireJsonContentType } from '../../../server/rate-limit'
import { listTasks, createTask } from '../../../server/task-store'
import { getUserProfile } from '../../../server/user-profiles'
import type { TaskColumn, TaskPriority, TaskSourceType } from '../../../types/task'

const VALID_COLUMNS: TaskColumn[] = ['backlog', 'todo', 'in_progress', 'review', 'done']
const VALID_PRIORITIES: TaskPriority[] = ['high', 'medium', 'low']
const VALID_SOURCES: TaskSourceType[] = ['manual', 'conductor', 'crew']

export const Route = createFileRoute('/api/tasks/')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError

        const url = new URL(request.url)
        const filter: Parameters<typeof listTasks>[0] = {}

        const column = url.searchParams.get('column')
        if (column && VALID_COLUMNS.includes(column as TaskColumn)) {
          filter.column = column as TaskColumn
        }

        const assignee = url.searchParams.get('assignee')
        if (assignee) filter.assignee = assignee

        const priority = url.searchParams.get('priority')
        if (priority && VALID_PRIORITIES.includes(priority as TaskPriority)) {
          filter.priority = priority as TaskPriority
        }

        const sourceType = url.searchParams.get('sourceType')
        if (sourceType && VALID_SOURCES.includes(sourceType as TaskSourceType)) {
          filter.sourceType = sourceType as TaskSourceType
        }

        const sourceId = url.searchParams.get('sourceId')
        if (sourceId) filter.sourceId = sourceId

        // Get all tasks matching the filter
        let tasks = listTasks(filter)

        // Apply role-based filtering (Issue #8)
        // Super_admin users see all tasks
        // Regular_admin users see only their own tasks (based on createdBy field)
        const userId = getUserIdFromRequest(request)
        if (userId) {
          const userProfile = getUserProfile(userId)
          if (userProfile.role !== 'super_admin') {
            // Filter to only tasks created by this user
            tasks = tasks.filter((task) => task.createdBy === userId)
          }
        }

        return json({ ok: true, tasks })
      },

      POST: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        const csrfCheck = requireJsonContentType(request)
        if (csrfCheck) return csrfCheck

        const body = (await request.json().catch(() => ({}))) as Record<string, unknown>

        const title = typeof body.title === 'string' ? body.title.trim() : ''
        if (!title) {
          return json({ ok: false, error: 'title is required' }, { status: 400 })
        }

        const input: Parameters<typeof createTask>[0] = { title }

        if (typeof body.description === 'string') input.description = body.description
        if (typeof body.column === 'string' && VALID_COLUMNS.includes(body.column as TaskColumn)) {
          input.column = body.column as TaskColumn
        }
        if (typeof body.priority === 'string' && VALID_PRIORITIES.includes(body.priority as TaskPriority)) {
          input.priority = body.priority as TaskPriority
        }
        if (typeof body.assignee === 'string' || body.assignee === null) {
          input.assignee = body.assignee as string | null
        }
        if (Array.isArray(body.tags)) {
          input.tags = (body.tags as unknown[]).filter((t) => typeof t === 'string') as string[]
        }
        if (typeof body.dueDate === 'string' || body.dueDate === null) {
          input.dueDate = body.dueDate as string | null
        }
        if (typeof body.sourceType === 'string' && VALID_SOURCES.includes(body.sourceType as TaskSourceType)) {
          input.sourceType = body.sourceType as TaskSourceType
        }
        if (typeof body.sourceId === 'string' || body.sourceId === null) {
          input.sourceId = body.sourceId as string | null
        }

        // Always set createdBy to the current user (cannot be overridden)
        const userId = getUserIdFromRequest(request)
        input.createdBy = userId || 'unknown'

        const task = createTask(input)
        return json({ ok: true, task }, { status: 201 })
      },
    },
  },
})
