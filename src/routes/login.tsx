import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

/**
 * Extracted component for testability — pure UI + state, no router dependencies.
 * The route file (below) wraps it with `useNavigate` so the test suite can
 * render `LoginPage` directly without standing up the router.
 */
export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Read the live DOM input at submit time. The `password` state can lag
  // behind the actual input — e.g. when the user types before React
  // hydrates, or pastes via the password manager before the first onChange
  // has fired. `password === '' && inputRef.current.value === 'secret'`
  // would otherwise POST with the empty state and fail authentication.
  const passwordRef = useRef<HTMLInputElement>(null)
  // Re-entrancy guard for handleSubmit. We intentionally do NOT disable the
  // submit button while the request is in flight — disabling the button
  // breaks screen-reader focus, hides the affordance from sighted users,
  // and makes the page look frozen on slow networks. Instead we keep the
  // button always-clickable and silently drop duplicate submits via this
  // ref. `submitting` (React state) still drives the visible "Signing in…"
  // label so the user knows the request is in flight.
  const submittingRef = useRef(false)

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
    // Re-entrancy guard: silently drop duplicate submits while a request
    // is in flight. The button stays clickable (no `disabled` attr), so a
    // frantic user clicking again sees no broken affordance — they just
    // don't trigger a second POST.
    if (submittingRef.current) return
    // Prefer the live DOM value over React state. The browser's `required`
    // attribute already blocked an empty submit at the form level, so we
    // trust whatever the input holds. Falls back to state when the ref
    // hasn't been attached yet (theoretical — ref is set on first render).
    const submittedPassword = passwordRef.current?.value ?? password
    if (!submittedPassword) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch('/api/auth', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: submittedPassword }),
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
      submittingRef.current = false
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
          ref={passwordRef}
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
          autoComplete="current-password"
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
          style={{
            width: '100%',
            padding: '0.75rem',
            borderRadius: 8,
            background: 'var(--theme-primary, #4f8ef7)',
            color: 'white',
            border: 'none',
            cursor: 'pointer',
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