/**
 * Jobs API proxy — forwards individual job operations to Hermes FastAPI
 */
import { createFileRoute } from '@tanstack/react-router'
import { requireAuth } from '../../server/auth-middleware'
import {
  BEARER_TOKEN,
  HERMES_API,
  HERMES_UPGRADE_INSTRUCTIONS,
  ensureGatewayProbed,
  getCapabilities,
} from '../../server/gateway-capabilities'

const authHeaders = (): Record<string, string> =>
  BEARER_TOKEN ? { Authorization: `Bearer ${BEARER_TOKEN}` } : {}

export const Route = createFileRoute('/api/hermes-jobs/$jobId')({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        if (!getCapabilities().jobs) {
          return new Response(
            JSON.stringify({
              error: `Gateway does not support /api/jobs. ${HERMES_UPGRADE_INSTRUCTIONS}`,
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          )
        }
        const url = new URL(request.url)
        // Support sub-actions: /api/hermes-jobs/:id/output, /pause, /resume, /run
        const subPath = url.searchParams.get('action') || ''
        const target = subPath
          ? `${HERMES_API}/api/jobs/${params.jobId}/${subPath}${url.search}`
          : `${HERMES_API}/api/jobs/${params.jobId}`
        const res = await fetch(target, { headers: authHeaders() })
        return new Response(await res.text(), {
          status: res.status,
          headers: { 'Content-Type': 'application/json' },
        })
      },
      POST: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        if (!getCapabilities().jobs) {
          return new Response(
            JSON.stringify({
              error: `Gateway does not support /api/jobs. ${HERMES_UPGRADE_INSTRUCTIONS}`,
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          )
        }
        const url = new URL(request.url)
        const action = url.searchParams.get('action') || ''
        const body = await request.text()
        const target = action
          ? `${HERMES_API}/api/jobs/${params.jobId}/${action}`
          : `${HERMES_API}/api/jobs/${params.jobId}`
        const res = await fetch(target, {
          method: 'POST',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: body || undefined,
        })
        return new Response(await res.text(), {
          status: res.status,
          headers: { 'Content-Type': 'application/json' },
        })
      },
      PATCH: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        if (!getCapabilities().jobs) {
          return new Response(
            JSON.stringify({
              error: `Gateway does not support /api/jobs. ${HERMES_UPGRADE_INSTRUCTIONS}`,
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          )
        }
        const body = await request.text()
        const res = await fetch(`${HERMES_API}/api/jobs/${params.jobId}`, {
          method: 'PATCH',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body,
        })
        return new Response(await res.text(), {
          status: res.status,
          headers: { 'Content-Type': 'application/json' },
        })
      },
      DELETE: async ({ request, params }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        if (!getCapabilities().jobs) {
          return new Response(
            JSON.stringify({
              error: `Gateway does not support /api/jobs. ${HERMES_UPGRADE_INSTRUCTIONS}`,
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          )
        }
        const res = await fetch(`${HERMES_API}/api/jobs/${params.jobId}`, {
          method: 'DELETE',
          headers: authHeaders(),
        })
        return new Response(await res.text(), {
          status: res.status,
          headers: { 'Content-Type': 'application/json' },
        })
      },
    },
  },
})
