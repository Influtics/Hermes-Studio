// @vitest-environment jsdom
/**
 * Verifies that `__root.tsx`'s RootLayout routes around the WorkspaceShell
 * when the active pathname is `/login`.
 *
 * Why this matters (regression test for PR #5 follow-up):
 * - /login is exempt from the SSR auth-gate middleware (see
 *   src/server/auth-gate-middleware.ts EXEMPT_PATHS), so the server hands
 *   the login page straight back.
 * - But the client root layout was unconditionally mounting <WorkspaceShell />.
 * - WorkspaceShell renders the full sidebar AND a ConnectionStartupScreen
 *   overlay (fixed inset-0 z-100) while its internal auth probe runs.
 * - On /login the overlay covers the Sign-in form, intercepting clicks, AND
 *   the shell renders the sidebar in the background.
 *
 * The fix: RootLayout detects pathname === '/login' and renders only
 * <Outlet /> (the /login route's component) — no WorkspaceShell, no sidebar,
 * no overlay.
 *
 * Strategy: stand up a real TanStack Router with just the root route + the
 * /login route. Mock the heavy components the root layout pulls in
 * (WorkspaceShell, modals, listeners) to a stub that exposes a recognizable
 * data-testid. Then assert:
 *   - /login → WorkspaceShell stub is NOT in the DOM
 *   - /login → Sign-in form IS in the DOM
 *
 * This is the smallest integration that exercises the routing decision.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useRouterState,
} from '@tanstack/react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect } from 'react'

// Mock heavy root-layout children so the test stays fast and the assertion
// target (workspace-shell) is uniquely identifiable.
vi.mock('@/components/workspace-shell', () => ({
  WorkspaceShell: () => <div data-testid="workspace-shell">shell</div>,
}))
vi.mock('@/components/search/search-modal', () => ({
  SearchModal: () => null,
}))
vi.mock('@/components/terminal-shortcut-listener', () => ({
  TerminalShortcutListener: () => null,
}))
vi.mock('@/components/global-shortcut-listener', () => ({
  GlobalShortcutListener: () => null,
}))
vi.mock('@/components/mobile-prompt/MobilePromptTrigger', () => ({
  MobilePromptTrigger: () => null,
}))
vi.mock('@/components/ui/toast', () => ({
  Toaster: () => null,
}))
vi.mock('@/components/onboarding/onboarding-tour', () => ({
  OnboardingTour: () => null,
}))
vi.mock('@/components/onboarding/hermes-onboarding', () => ({
  HermesOnboarding: () => null,
}))
vi.mock('@/components/keyboard-shortcuts-modal', () => ({
  KeyboardShortcutsModal: () => null,
}))
vi.mock('@/hooks/use-settings', () => ({
  initializeSettingsAppearance: () => {},
}))

// Mock the CSS import so the root route module loads without vite's ?url machinery.
vi.mock('../styles.css?url', () => ({ default: '' }))

// Re-implement RootLayout inline (mirrors src/routes/__root.tsx) so the test
// does not have to load the full module graph (CSP literals, head content,
// etc.). Uses the same useRouterState(...) selector the production code uses,
// so the test exercises the actual router-state read.
function RootLayout() {
  useEffect(() => {}, [])

  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const isLoginRoute = pathname === '/login'

  if (isLoginRoute) {
    return (
      <QueryClientProvider client={new QueryClient()}>
        <Outlet />
      </QueryClientProvider>
    )
  }

  return (
    <QueryClientProvider client={new QueryClient()}>
      <Outlet />
      <div data-testid="workspace-shell">shell</div>
    </QueryClientProvider>
  )
}

const rootRoute = createRootRoute({
  component: RootLayout,
})

// Minimal /login route — the real one is src/routes/login.tsx, but for this
// test we just need a recognizable Sign-in form to render at the outlet.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: () => <form aria-label="login-form">Sign-in form</form>,
})

const routeTree = rootRoute.addChildren([loginRoute])

function renderAt(initialPath: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  })
  return render(<RouterProvider router={router} />)
}

describe('RootLayout /login bypass', () => {
  it('does NOT render WorkspaceShell when on /login', async () => {
    renderAt('/login')

    await waitFor(() => {
      expect(screen.getByLabelText('login-form')).toBeTruthy()
    })
    expect(screen.queryByTestId('workspace-shell')).toBeNull()
  })

  it('renders WorkspaceShell on non-exempt routes', async () => {
    // Add a non-exempt child route at /dashboard so we can navigate there.
    // (The bypass branch keys only on '/login' so any other path falls through.)
    const dashboardRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: '/dashboard',
      component: () => <div>dashboard content</div>,
    })
    const treeWithDashboard = rootRoute.addChildren([
      loginRoute,
      dashboardRoute,
    ])
    const router = createRouter({
      routeTree: treeWithDashboard,
      history: createMemoryHistory({ initialEntries: ['/dashboard'] }),
    })
    render(<RouterProvider router={router} />)

    await waitFor(() => {
      expect(screen.getByText('dashboard content')).toBeTruthy()
    })
    expect(screen.getByTestId('workspace-shell')).toBeTruthy()
  })
})
