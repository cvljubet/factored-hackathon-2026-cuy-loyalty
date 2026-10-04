import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppRoutes } from './App'
import type { AuthService } from './auth/authService'
import { AuthProvider } from './auth/AuthProvider'
import { createFakeAuthService, namedError, testUser } from './test/fakeAuthService'

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>
}

function renderApp(service: AuthService, path: string) {
  return render(
    <AuthProvider service={service}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
        <CurrentPath />
      </MemoryRouter>
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
