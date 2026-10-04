import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createFakeAuthService, namedError, testUser } from '../test/fakeAuthService'
import type { AuthService } from './authService'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './context'

function Probe() {
  const { status, user, signIn, signOut } = useAuth()
  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="email">{user?.email ?? ''}</p>
      <button onClick={() => signIn('constanza@example.com', 'Secret123').catch(() => {})}>sign in</button>
      <button onClick={() => signOut()}>sign out</button>
    </div>
  )
}

function renderWithService(service: AuthService) {
  return render(
    <AuthProvider service={service}>
      <Probe />
    </AuthProvider>,
  )
}

describe('AuthProvider', () => {
  it('starts loading, then restores an existing session (page refresh)', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    renderWithService(service)

    expect(screen.getByTestId('status')).toHaveTextContent('loading')
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(screen.getByTestId('email')).toHaveTextContent(testUser.email)
  })

  it('is unauthenticated when there is no stored session', async () => {
    const { service } = createFakeAuthService()
    renderWithService(service)

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
  })

  it('is unauthenticated when restoring the session fails', async () => {
    const { service } = createFakeAuthService()
    service.getCurrentUser.mockRejectedValue(new Error('network down'))
    renderWithService(service)

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
  })

  it('becomes authenticated after a successful sign-in', async () => {
    const { service } = createFakeAuthService()
    renderWithService(service)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await userEvent.click(screen.getByRole('button', { name: 'sign in' }))

    expect(service.signIn).toHaveBeenCalledWith('constanza@example.com', 'Secret123')
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('email')).toHaveTextContent(testUser.email)
  })

  it('stays unauthenticated when sign-in is rejected', async () => {
    const { service } = createFakeAuthService()
    service.signIn.mockRejectedValue(namedError('NotAuthorizedException'))
    renderWithService(service)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await userEvent.click(screen.getByRole('button', { name: 'sign in' }))

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
  })

  it('clears the user on sign-out, even if the remote sign-out fails', async () => {
    const { service } = createFakeAuthService({ sessionUser: testUser })
    service.signOut.mockRejectedValue(new Error('network down'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderWithService(service)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))

    await act(async () => {
      await screen.getByRole('button', { name: 'sign out' }).click()
    })

    expect(service.signOut).toHaveBeenCalled()
    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('email')).toHaveTextContent('')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('signs the user out when the session ends elsewhere (e.g. token refresh fails)', async () => {
    const { service, endSession } = createFakeAuthService({ sessionUser: testUser })
    renderWithService(service)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))

    act(() => endSession())

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
  })
})
