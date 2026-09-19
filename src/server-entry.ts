/**
 * Custom server entry for TanStack Start.
 *
 * Replaces the default entry (which just wraps `createStartHandler`) to add
 * an SSR auth-gate: requests that arrive without a valid session are 302'd
 * to `/login` before any UI bytes ship.
 *
 * This entry runs INSIDE the SSR process, so it shares the in-memory
 * session-token store (`validTokens` in `./server/auth-middleware.ts`) with
 * the `/api/auth` login and `/api/auth-logout` endpoints that mutate it.
 *
 * Why not the default entry's `createMiddleware().server(...)` slot:
 * this project uses a minimal TanStack Start setup without `createStart()`,
 * so middleware registration happens at the server-entry boundary instead.
 *
 * Dev mirror: `vite.config.ts` registers an equivalent gate as a vite
 * middleware that runs before TanStack Start's request handler.
 */

import {
  createStartHandler,
  defaultStreamHandler,
} from '@tanstack/react-start/server'
import type { Register } from '@tanstack/react-router'
import type { RequestHandler } from '@tanstack/react-start/server'

import { authGateMiddleware } from './server/auth-gate-middleware'

const fetch = createStartHandler(defaultStreamHandler)

/**
 * Gate wrapped around the TanStack Start fetch handler.
 *
 * The gate throws a Response on 302 — we catch it here and return it as
 * the actual HTTP response. Otherwise the wrapped fetch runs normally.
 */
const gatedFetch: RequestHandler<Register> = async (request) => {
  try {
    await authGateMiddleware({
      request,
      next: () => Promise.resolve(undefined),
    })
  } catch (thrown) {
    if (thrown instanceof Response) {
      return thrown
    }
    // Programming error — let the inner handler surface it through normal
    // request handling rather than swallowing it.
    throw thrown
  }
  return fetch(request)
}

// Providing `RequestHandler` from `@tanstack/react-start/server` is required so
// that the output types don't import it from `@tanstack/start-server-core`.
export type ServerEntry = { fetch: RequestHandler<Register> }

export function createServerEntry(entry: ServerEntry): ServerEntry {
  return {
    async fetch(...args) {
      return await entry.fetch(...args)
    },
  }
}

export default createServerEntry({ fetch: gatedFetch })
