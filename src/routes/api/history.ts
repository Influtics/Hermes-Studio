import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'
import {
  ensureGatewayProbed,
  getGatewayCapabilities,
  getMessages,
  listSessions,
  toChatMessage,
} from '../../server/hermes-api'
import {
  getLocalMessages,
  listLocalSessions,
  toLocalChatMessage,
} from '../../server/local-session-store'
import { resolveSessionKey } from '../../server/session-utils'
import { requireAuth } from '@/server/auth-middleware'

export const Route = createFileRoute('/api/history')({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const authError = requireAuth(request)
        if (authError) return authError
        await ensureGatewayProbed()
        if (!getGatewayCapabilities().sessions) {
          const url2 = new URL(request.url)
          const rawKey = url2.searchParams.get('sessionKey')?.trim()
          const fid = url2.searchParams.get('friendlyId')?.trim()
          // Resolve: prefer explicit key; fallback to most recent local session
          let localKey = rawKey || fid || ''
          if (!localKey || localKey === 'main' || localKey === 'new') {
            const sessions = listLocalSessions()
            localKey = sessions[0]?.id ?? 'new'
          }
          if (localKey === 'new') {
            return json({ sessionKey: 'new', sessionId: 'new', messages: [], source: 'local' })
          }
          const limit2 = Number(url2.searchParams.get('limit') || '200')
          const msgs = getLocalMessages(localKey)
          const bounded = limit2 > 0 ? msgs.slice(-limit2) : msgs
          return json({
            sessionKey: localKey,
            sessionId: localKey,
            messages: bounded.map((m, i) => toLocalChatMessage(m, i)),
            source: 'local',
          })
        }
        try {
          const url = new URL(request.url)
          const limit = Number(url.searchParams.get('limit') || '200')
          const rawSessionKey = url.searchParams.get('sessionKey')?.trim()
          const friendlyId = url.searchParams.get('friendlyId')?.trim()
          let { sessionKey } = await resolveSessionKey({
            rawSessionKey,
            friendlyId,
            defaultKey: 'main',
          })
          // "main" doesn't exist in Hermes — resolve to latest session
          if (sessionKey === 'main' || sessionKey === 'new') {
            try {
              const sessions = await listSessions(1, 0)
              if (sessions.length > 0) {
                sessionKey = sessions[0].id
              } else {
                return json({
                  sessionKey: 'new',
                  sessionId: 'new',
                  messages: [],
                })
              }
            } catch {
              return json({ sessionKey: 'new', sessionId: 'new', messages: [] })
            }
          }
          const messages = await getMessages(sessionKey)
          const boundedMessages = limit > 0 ? messages.slice(-limit) : messages

          return json({
            sessionKey,
            sessionId: sessionKey,
            messages: boundedMessages.map((message, index) =>
              toChatMessage(message, { historyIndex: index }),
            ),
          })
        } catch (err) {
          return json(
            {
              error: err instanceof Error ? err.message : String(err),
            },
            { status: 500 },
          )
        }
      },
    },
  },
})
