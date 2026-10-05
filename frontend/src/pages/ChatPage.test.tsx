import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ApiError, type ApiClient, type ChatReply } from '../api/client'
import { ApiContext } from '../api/context'
import { AppRoutes } from '../App'
import type { AuthService } from '../auth/authService'
import { AuthProvider } from '../auth/AuthProvider'
import { ProfileProvider } from '../profile/ProfileProvider'
import { chatReply, createFakeApiClient } from '../test/fakeApiClient'
import { createFakeAuthService, testUser } from '../test/fakeAuthService'

function renderChat(api = createFakeApiClient(), service: AuthService = createFakeAuthService({ sessionUser: testUser }).service) {
  render(
    <AuthProvider service={service}>
      <ApiContext.Provider value={api}>
        <ProfileProvider api={api}>
          <MemoryRouter initialEntries={['/chat']}>
            <AppRoutes />
          </MemoryRouter>
        </ProfileProvider>
      </ApiContext.Provider>
    </AuthProvider>,
  )
  return api
}

async function openChat() {
  await screen.findByRole('heading', { name: '¡Hola, Constanza!' })
}

async function sendMessage(text: string) {
  await userEvent.type(screen.getByLabelText('Mensaje'), text)
  await userEvent.click(screen.getByRole('button', { name: 'Enviar mensaje' }))
}

/** A sendChat whose reply is released by the test. */
function deferredReply(api: ReturnType<typeof createFakeApiClient>) {
  let release!: (reply: ChatReply) => void
  let fail!: (error: unknown) => void
  api.sendChat.mockImplementationOnce(
    () =>
      new Promise<ChatReply>((resolve, reject) => {
        release = resolve
        fail = reject
      }),
  )
  return { release: (reply: ChatReply) => act(() => release(reply)), fail: (error: unknown) => act(() => fail(error)) }
}

const conversation = () => within(screen.getByRole('main'))

describe('chat with the backend', () => {
  it('shows the backend reply as the assistant message', async () => {
    const api = renderChat()
    await openChat()

    await sendMessage('Muéstrame mi perfil')

    expect(await screen.findByText('Respuesta a: Muéstrame mi perfil')).toBeInTheDocument()
    expect(api.sendChat).toHaveBeenCalledWith({ message: 'Muéstrame mi perfil', sessionId: undefined, language: 'es' })
    expect(screen.getByLabelText('Mensaje')).toHaveValue('')
  })

  it('starts without placeholder messages', async () => {
    renderChat()
    await openChat()

    expect(screen.queryByText(/Japón/)).not.toBeInTheDocument()
  })

  it('shows the user message and a typing indicator while waiting, then the reply', async () => {
    const api = createFakeApiClient()
    const pending = deferredReply(api)
    renderChat(api)
    await openChat()

    await sendMessage('¿Cuál es mi saldo?')

    expect(conversation().getByText('¿Cuál es mi saldo?')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'El asistente está escribiendo...' })).toBeInTheDocument()

    await pending.release(chatReply({ message: '¿Cuál es mi saldo?' }, { reply: 'Aún no tengo esa información.' }))

    expect(screen.getByText('Aún no tengo esa información.')).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'El asistente está escribiendo...' })).not.toBeInTheDocument()
  })

  it('reuses the session_id returned by the backend on the next message', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockImplementation(async (request) => chatReply(request, { sessionId: 'backend-session-42' }))
    renderChat(api)
    await openChat()

    await sendMessage('primera')
    await screen.findByText('Respuesta a: primera')
    await sendMessage('segunda')
    await screen.findByText('Respuesta a: segunda')

    expect(api.sendChat.mock.calls[0][0].sessionId).toBeUndefined()
    expect(api.sendChat.mock.calls[1][0].sessionId).toBe('backend-session-42')
  })

  it('refuses another submission while a reply is pending', async () => {
    const api = createFakeApiClient()
    const pending = deferredReply(api)
    renderChat(api)
    await openChat()

    await sendMessage('primera')
    await userEvent.type(screen.getByLabelText('Mensaje'), 'segunda{Enter}')
    await userEvent.type(screen.getByLabelText('Mensaje'), '{Enter}')

    expect(screen.getByRole('button', { name: 'Enviar mensaje' })).toBeDisabled()
    expect(api.sendChat).toHaveBeenCalledTimes(1)
    // The unsent draft is kept so the user can send it once the reply arrives.
    expect(screen.getByLabelText('Mensaje')).toHaveValue('segunda')

    await pending.release(chatReply({ message: 'primera' }))
    await userEvent.click(screen.getByRole('button', { name: 'Enviar mensaje' }))

    expect(api.sendChat).toHaveBeenCalledTimes(2)
  })

  it('sends the selected UI language', async () => {
    const api = renderChat()
    await openChat()

    await userEvent.click(screen.getByRole('button', { name: /Idioma/ }))
    await screen.findByRole('heading', { name: 'Olá, Constanza!' })
    await userEvent.type(screen.getByLabelText('Mensagem'), 'Olá')
    await userEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    await waitFor(() => expect(api.sendChat).toHaveBeenCalledWith(expect.objectContaining({ language: 'pt' })))
  })

  it('shows an escalation acknowledgement like any other reply', async () => {
    const api = createFakeApiClient()
    const acknowledgement =
      'He derivado tu solicitud a un asesor. El contexto de esta conversación será incluido para que no tengas que repetir la información.'
    api.sendChat.mockImplementation(async (request) =>
      chatReply(request, { reply: acknowledgement, engine: 'escalation', status: 'escalated', escalated: true, handoffId: 'HO-1' }),
    )
    renderChat(api)
    await openChat()

    await sendMessage('Quiero hablar con un asesor')

    expect(await screen.findByText(acknowledgement)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('treats unavailable data as a normal reply, not an error', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockImplementation(async (request) =>
      chatReply(request, { reply: 'Esa información todavía no está disponible.', status: 'answered' }),
    )
    renderChat(api)
    await openChat()

    await sendMessage('¿Cuáles son mis gastos?')

    expect(await screen.findByText('Esa información todavía no está disponible.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('chat errors', () => {
  it.each([
    [401, 'Tu sesión expiró. Vuelve a iniciar sesión para continuar.'],
    [403, 'Tu cuenta no está vinculada a un cliente. Contacta al administrador.'],
    [422, 'No pudimos procesar tu mensaje. Revisa que no esté vacío ni sea demasiado largo.'],
    [0, 'No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.'],
    [500, 'El asistente no pudo responder en este momento. Inténtalo de nuevo en unos minutos.'],
    [503, 'El asistente no pudo responder en este momento. Inténtalo de nuevo en unos minutos.'],
  ])('shows a friendly message for status %i, never the raw error', async (status, text) => {
    const api = createFakeApiClient()
    api.sendChat.mockRejectedValue(new ApiError(status, `POST /chat failed with ${status}: eyJhbGciOi.secret.trace`))
    renderChat(api)
    await openChat()

    await sendMessage('hola')

    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.queryByText(/eyJ|failed with|trace/)).not.toBeInTheDocument()
    // The user's message stays visible and they can try again.
    expect(conversation().getByText('hola')).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'El asistente está escribiendo...' })).not.toBeInTheDocument()
  })

  it('shows the network message in Portuguese', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockRejectedValue(new TypeError('Failed to fetch'))
    renderChat(api)
    await openChat()
    await userEvent.click(screen.getByRole('button', { name: /Idioma/ }))
    await screen.findByRole('heading', { name: 'Olá, Constanza!' })

    await userEvent.type(screen.getByLabelText('Mensagem'), 'Olá')
    await userEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Não foi possível conectar ao assistente. Verifique sua conexão e tente novamente.',
    )
  })

  it('offers to sign in again after a 401', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockRejectedValue(new ApiError(401, 'expired'))
    const { service } = createFakeAuthService({ sessionUser: testUser })
    renderChat(api, service)
    await openChat()
    await sendMessage('hola')

    await userEvent.click(await screen.findByRole('button', { name: 'Iniciar sesión de nuevo' }))

    expect(service.signOut).toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })).toBeInTheDocument()
  })

  it('clears the error when the next message succeeds', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockRejectedValueOnce(new ApiError(500, 'boom'))
    renderChat(api)
    await openChat()

    await sendMessage('hola')
    await screen.findByRole('alert')
    await sendMessage('otra vez')

    expect(await screen.findByText('Respuesta a: otra vez')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('sign-out', () => {
  it('clears the conversation and the backend session', async () => {
    const api = createFakeApiClient()
    api.sendChat.mockImplementation(async (request) => chatReply(request, { sessionId: 'first-user-session' }))
    renderChat(api)
    await openChat()
    await sendMessage('mensaje privado')
    await screen.findByText('Respuesta a: mensaje privado')

    await userEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }))
    await screen.findByRole('heading', { name: 'Bienvenido de nuevo' })
    await userEvent.type(screen.getByLabelText('Correo electrónico'), 'constanza@example.com')
    await userEvent.type(screen.getByLabelText('Contraseña'), 'Secret123')
    await userEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }))
    await openChat()

    expect(screen.queryByText('mensaje privado')).not.toBeInTheDocument()
    await sendMessage('nuevo')
    await screen.findByText('Respuesta a: nuevo')
    expect(api.sendChat.mock.lastCall?.[0].sessionId).toBeUndefined()
  })
})

// Keeps the ApiClient type honest: the fake must implement every client method.
const _typecheck: ApiClient = createFakeApiClient()
void _typecheck
