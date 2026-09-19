// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react'
import { LoginPage } from '../../routes/login'

// Default auth-check response: not authenticated, form renders normally.
const defaultAuthCheckResponse = {
  ok: true,
  status: 200,
  json: async () => ({ authenticated: false, authRequired: true }),
}

describe('LoginPage', () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue(defaultAuthCheckResponse)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders password input and submit button', async () => {
    render(<LoginPage onAuthenticated={vi.fn()} />)

    expect(screen.getByLabelText(/password/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeTruthy()
  })

  it('calls onAuthenticated on successful submit', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      defaultAuthCheckResponse,
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    })

    const onAuth = vi.fn()
    render(<LoginPage onAuthenticated={onAuth} />)

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(onAuth).toHaveBeenCalled())

    // Verify it POSTed to /api/auth with credentials: include
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/auth',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ password: 'secret' }),
      }),
    )
  })

  it('shows inline error on 401', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      defaultAuthCheckResponse,
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ ok: false, error: 'Invalid password' }),
    })

    render(<LoginPage onAuthenticated={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'wrong' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/invalid/i)
  })

  it('shows inline error when fetch throws (network error)', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      defaultAuthCheckResponse,
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('boom'),
    )

    render(<LoginPage onAuthenticated={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'whatever' },
    })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/network error/i)
  })

  it('bypasses the form when already authenticated (useEffect auth-check)', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ authenticated: true, authRequired: true }),
    })

    const onAuth = vi.fn()
    render(<LoginPage onAuthenticated={onAuth} />)

    await waitFor(() => expect(onAuth).toHaveBeenCalledTimes(1))
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/auth-check',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('does not call onAuthenticated when auth-check returns not-authenticated', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ authenticated: false, authRequired: true }),
    })

    const onAuth = vi.fn()
    render(<LoginPage onAuthenticated={onAuth} />)

    // Wait for the fetch to resolve, then verify no callback
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/auth-check',
        expect.any(Object),
      ),
    )
    expect(onAuth).not.toHaveBeenCalled()
  })

  it('disables submit button while submitting', async () => {
    let resolveFetch: ((value: unknown) => void) | null = null
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      defaultAuthCheckResponse,
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve
        }),
    )

    render(<LoginPage onAuthenticated={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'pw' },
    })

    const btn = screen.getByRole('button', { name: /sign in/i })
    expect(btn.hasAttribute('disabled')).toBe(false)

    fireEvent.click(btn)

    await waitFor(() => expect(btn.hasAttribute('disabled')).toBe(true))
    expect(btn.textContent).toMatch(/signing in/i)

    // Resolve the pending fetch so React can finish updating
    await act(async () => {
      resolveFetch!({ ok: true, status: 200, json: async () => ({}) })
      await Promise.resolve()
    })
  })

  it('disables submit button when password is empty', () => {
    render(<LoginPage onAuthenticated={vi.fn()} />)
    const btn = screen.getByRole('button', { name: /sign in/i })
    expect(btn.hasAttribute('disabled')).toBe(true)
  })
})