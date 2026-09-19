import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

/**
 * Extracted component for testability — pure UI + state, no router dependencies.
 * The route file (below) wraps it with `useNavigate` so the test suite can
 * render `LoginPage` directly without standing up the router.
 */
export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Client-side auth-check on mount: already-authenticated users skip the form.
  // Network errors here are non-fatal — user can still attempt to log in.
  useEffect(() => {
    let cancelled = false
    fetch('/api/auth-check', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return
        if (data.authenticated) onAuthenticated()
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [onAuthenticated])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch('/api/auth', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.ok) {
        onAuthenticated()
        return
      }
      const body = (await res.json().catch(() => ({}))) as {
        error?: string
      }
      setError(body.error || 'Authentication failed')
    } catch {
      setError('Network error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main
      className="login-page"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        padding: '1rem',
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          width: '100%',
          maxWidth: 360,
          padding: '2rem',
          borderRadius: 12,
          background: 'var(--theme-bg-surface, #1a1a1a)',
          boxShadow: '0 4px 24px rgba(0,0,0,0.2)',
        }}
      >
        <h1 style={{ marginBottom: '1.5rem', fontSize: '1.5rem' }}>Sign in</h1>

        <label
          htmlFor="password"
          style={{ display: 'block', marginBottom: '0.5rem' }}
        >
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
          autoComplete="current-password"
          disabled={submitting}
          style={{
            width: '100%',
            padding: '0.75rem',
            marginBottom: '1rem',
            borderRadius: 8,
            border: '1px solid var(--theme-border, #333)',
            background: 'var(--theme-bg-input, #0f0f0f)',
            color: 'var(--theme-text, #eee)',
          }}
        />

        {error && (
          <div
            role="alert"
            style={{
              color: 'var(--theme-error, #e55)',
              marginBottom: '1rem',
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !password}
          style={{
            width: '100%',
            padding: '0.75rem',
            borderRadius: 8,
            background: 'var(--theme-primary, #4f8ef7)',
            color: 'white',
            border: 'none',
            cursor: submitting ? 'not-allowed' : 'pointer',
          }}
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}

export const Route = createFileRoute('/login')({
  component: LoginRouteComponent,
})

function LoginRouteComponent() {
  const navigate = useNavigate()
  return (
    <LoginPage onAuthenticated={() => navigate({ to: '/', replace: true })} />
  )
}