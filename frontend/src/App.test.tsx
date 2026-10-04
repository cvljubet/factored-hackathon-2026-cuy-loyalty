import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from './api/client'
import { AppRoutes } from './App'
import type { AuthService } from './auth/authService'
import { AuthProvider } from './auth/AuthProvider'
import { ProfileProvider } from './profile/ProfileProvider'
import { apiError, createFakeApiClient } from './test/fakeApiClient'
import { createFakeAuthService, namedError, testUser } from './test/fakeAuthService'

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

function renderApp(service: AuthService, path: string, api: ApiClient = createFakeApiClient()) {
  return render(
    <AuthProvider service={service}>
      <ProfileProvider api={api}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
          <CurrentPath />
        </MemoryRouter>
      </ProfileProvider>
    </AuthProvider>,
  )
}

async function fillAndSubmitLogin(email: string, password: string) {
  await userEvent.type(screen.getByLabelText('Correo electrónico'), email)
  await userEvent.type(screen.getByLabelText('Contraseña'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }))
}

describe('route protection', () => {
  it('redirects an unauthenticated visitor from /chat to /login', async () => {
    const { service } = createFakeAuthService()
    renderApp(service, '/chat')

    expect(await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })).toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/login')
  })

  it('shows a loading state, not the login page, while the session is restored', () => {
    const { service } = createFakeAuthService()
    service.getCurrentUser.mockReturnValue(new Promise(() => {}))
    renderApp(service, '/chat')

    expect(screen.getByRole('status')).toHaveTextContent('Cargando...')
    expect(screen.getByTestId('path')).toHaveTextContent('/chat')
  })

  it('shows /chat with the user from the restored session', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    renderApp(service, '/chat')

    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(screen.getByText('CL')).toBeInTheDocument()
  })

  it('sends an already signed-in user from /login to /chat', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    renderApp(service, '/login')

    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/chat')
  })
})

describe('login and logout', () => {
  it('signs in with the form values and navigates to /chat', async () => {
    const { service } = createFakeAuthService()
    renderApp(service, '/login')
    await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })

    await fillAndSubmitLogin('  constanza@example.com ', 'Secret123')

    expect(service.signIn).toHaveBeenCalledWith('constanza@example.com', 'Secret123')
    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/chat')
  })

  it('shows a translated error and stays on /login when credentials are wrong', async () => {
    const { service } = createFakeAuthService()
    service.signIn.mockRejectedValue(namedError('NotAuthorizedException'))
    renderApp(service, '/login')
    await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })

    await fillAndSubmitLogin('constanza@example.com', 'wrong')

    expect(await screen.findByRole('alert')).toHaveTextContent('Correo electrónico o contraseña incorrectos.')
    expect(screen.getByTestId('path')).toHaveTextContent('/login')
    expect(screen.getByRole('button', { name: 'Iniciar sesión' })).toBeEnabled()
  })

  it('logs out from /chat and returns to /login', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    renderApp(service, '/chat')
    await screen.findByRole('heading', { name: '¡Hola, Constanza!' })

    await userEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }))

    expect(service.signOut).toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })).toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/login')
  })
})

describe('backend profile (GET /me)', () => {
  it('loads /me once for the restored session before showing /chat', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    const api = createFakeApiClient()
    renderApp(service, '/chat', api)

    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(api.getMe).toHaveBeenCalledTimes(1)
  })

  it('shows the loading state while /me is in flight', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    const api = createFakeApiClient()
    api.getMe.mockReturnValue(new Promise(() => {}))
    renderApp(service, '/chat', api)

    await waitFor(() => expect(api.getMe).toHaveBeenCalled())
    expect(screen.getByRole('status')).toHaveTextContent('Cargando...')
    expect(screen.queryByRole('heading', { name: '¡Hola, Constanza!' })).not.toBeInTheDocument()
  })

  it('does not call /me without a session', async () => {
    const { service } = createFakeAuthService()
    const api = createFakeApiClient()
    renderApp(service, '/chat', api)

    await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })
    expect(api.getMe).not.toHaveBeenCalled()
  })

  it('loads /me after signing in', async () => {
    const { service } = createFakeAuthService()
    const api = createFakeApiClient()
    renderApp(service, '/login', api)
    await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })

    await fillAndSubmitLogin('constanza@example.com', 'Secret123')

    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(api.getMe).toHaveBeenCalledTimes(1)
  })

  it('signs out and returns to /login when the backend answers 401', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    const api = createFakeApiClient()
    api.getMe.mockRejectedValue(apiError(401))
    renderApp(service, '/chat', api)

    expect(await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })).toBeInTheDocument()
    expect(service.signOut).toHaveBeenCalled()
    expect(screen.getByTestId('path')).toHaveTextContent('/login')
  })

  it('explains that the account is not linked when the backend answers 403', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    const api = createFakeApiClient()
    api.getMe.mockRejectedValue(apiError(403))
    renderApp(service, '/chat', api)

    expect(await screen.findByRole('alert')).toHaveTextContent('Tu cuenta no está vinculada a un cliente')
    expect(screen.queryByRole('heading', { name: '¡Hola, Constanza!' })).not.toBeInTheDocument()

    // The card's button (the header has an icon-only one with the same label).
    const signOutButtons = screen.getAllByRole('button', { name: 'Cerrar sesión' })
    await userEvent.click(signOutButtons[signOutButtons.length - 1])

    expect(service.signOut).toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })).toBeInTheDocument()
    expect(screen.getByTestId('path')).toHaveTextContent('/login')
  })

  it('still shows /chat when the backend is unreachable', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    const api = createFakeApiClient()
    api.getMe.mockRejectedValue(new TypeError('Failed to fetch'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderApp(service, '/chat', api)

    expect(await screen.findByRole('heading', { name: '¡Hola, Constanza!' })).toBeInTheDocument()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })
})
