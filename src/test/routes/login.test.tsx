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

  it('keeps the button clickable while submitting and shows a "Signing in…" label', async () => {
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

    const btn = screen.getByRole('button', { name: /sign in|signing in/i })
    expect(btn.hasAttribute('disabled')).toBe(false)

    fireEvent.click(btn)

    // Button stays clickable (NOT disabled) while the request is in flight,
    // but the label changes to give the user feedback.
    await waitFor(() => expect(btn.textContent).toMatch(/signing in/i))
    expect(btn.hasAttribute('disabled')).toBe(false)

    // Resolve the pending fetch so React can finish updating
    await act(async () => {
      resolveFetch!({ ok: true, status: 200, json: async () => ({}) })
      await Promise.resolve()
    })
  })

  it('silently drops duplicate submits while the request is in flight (re-entrancy guard)', async () => {
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

    const btn = screen.getByRole('button', { name: /sign in|signing in/i })

    // Three rapid clicks — fetch should only fire once (auth-check is the
    // first call; the auth POST is the second and should not be repeated).
    fireEvent.click(btn)
    fireEvent.click(btn)
    fireEvent.click(btn)

    // Give the click handlers a tick to settle.
    await act(async () => {
      await Promise.resolve()
    })

    const authCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => c[0] === '/api/auth')
    expect(authCalls).toHaveLength(1)

    // Cleanup: resolve the in-flight fetch so React can settle.
    await act(async () => {
      resolveFetch!({ ok: true, status: 200, json: async () => ({}) })
      await Promise.resolve()
    })
  })

  it('never disables the submit button in any state (regression: no `disabled` attr ever)', () => {
    // Pins the "do not disable button at all" UX choice. Disabling the
    // button breaks screen-reader focus, hides the affordance from sighted
    // users, and makes the page look frozen on slow networks. Re-entrancy
    // is handled by a ref guard inside handleSubmit, not by disabling the
    // button.
    render(<LoginPage onAuthenticated={vi.fn()} />)
    const btn = screen.getByRole('button', { name: /sign in/i })
    expect(btn.hasAttribute('disabled')).toBe(false)

    const input = screen.getByLabelText(/password/i)
    expect(input.hasAttribute('disabled')).toBe(false)
  })

  it('does NOT disable the submit button when password is empty (regression: see PR follow-up)', () => {
    // Regression for the "Sign-in button not clickable" bug on the deployed
    // /login route: the SSR-rendered button was hardcoded `disabled=""`
    // because `password` started as `''`, so `disabled={submitting || !password}`
    // gated the button on a value React hadn't seen yet. The input had no
    // visual disabled state (cursor stayed `pointer`, no opacity), so the
    // button looked alive but never fired a submit event. The fix drops the
    // `!password` gate and trusts the input's `required` attribute to block
    // empty submits at the browser level. This test pins that behavior in
    // place — if anyone re-adds the `!password` gate, the SSR'd page will
    // be unclickable again on first paint.
    render(<LoginPage onAuthenticated={vi.fn()} />)
    const btn = screen.getByRole('button', { name: /sign in/i })
    expect(btn.hasAttribute('disabled')).toBe(false)
    expect(screen.getByLabelText(/password/i).hasAttribute('required')).toBe(
      true,
    )
  })

  it('submits the live DOM password value, not the React state value (paste-before-hydration case)', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      defaultAuthCheckResponse,
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    })

    render(<LoginPage onAuthenticated={vi.fn()} />)

    // Simulate the paste-before-hydration race: directly write to the DOM
    // input's `value` (the same path password-manager fills use), then click
    // the button WITHOUT firing the React onChange. The submit handler must
    // read the live DOM value via the ref — it must NOT POST with the empty
    // React state, which would 401 and burn the user's paste.
    const input = screen.getByLabelText(/password/i) as HTMLInputElement
    input.value = 'pasted-secret'
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/auth',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ password: 'pasted-secret' }),
        }),
      ),
    )
  })
})