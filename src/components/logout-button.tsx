import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

export function LogoutButton() {
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  async function handleClick() {
    if (busy) return
    setBusy(true)
    try {
      await fetch('/api/auth-logout', { method: 'POST', credentials: 'include' })
    } catch {
      // even if logout fails server-side, navigate away
    } finally {
      setBusy(false)
      navigate({ to: '/login', replace: true })
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      style={{
        padding: '0.4rem 0.8rem',
        borderRadius: 6,
        background: 'transparent',
        border: '1px solid var(--theme-border, #444)',
        color: 'var(--theme-text-muted, #aaa)',
        cursor: busy ? 'not-allowed' : 'pointer',
        fontSize: '0.875rem',
      }}
    >
      {busy ? 'Signing out…' : 'Sign out'}
    </button>
  )
}