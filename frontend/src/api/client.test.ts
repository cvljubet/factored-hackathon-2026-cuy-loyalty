import { describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from './client'
import { readApiBaseUrl } from './config'

function jsonResponse(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup(options: { token?: string | null; response?: Response } = {}) {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
    options.response ??
      jsonResponse(200, {
        sub: 'abc-123',
        email: 'constanza@example.com',
        given_name: 'Constanza',
        family_name: null,
        customer_id: 'CLI-00BPQUST6X8L',
      }),
  )
  const getIdToken = vi.fn().mockResolvedValue(options.token === undefined ? 'id.token.value' : options.token)
  const client = createApiClient({ baseUrl: 'http://localhost:8000', getIdToken, fetch })
  return { client, fetch, getIdToken }
}

describe('createApiClient().getMe', () => {
  it('sends the ID token as a Bearer header to GET /me', async () => {
    const { client, fetch } = setup()

    await client.getMe()

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('http://localhost:8000/me')
    expect(init?.method ?? 'GET').toBe('GET')
    expect(init?.body).toBeUndefined()
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer id.token.value')
  })

  it('maps the response, including customer_id', async () => {
    const { client } = setup()

    expect(await client.getMe()).toEqual({
      sub: 'abc-123',
      email: 'constanza@example.com',
      givenName: 'Constanza',
      familyName: undefined,
      customerId: 'CLI-00BPQUST6X8L',
    })
  })

  it('fails with 401 without calling the backend when there is no session', async () => {
    const { client, fetch } = setup({ token: null })

    await expect(client.getMe()).rejects.toMatchObject({ name: 'ApiError', status: 401 })
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([401, 403, 500])('throws an ApiError carrying a %i status', async (status) => {
    const { client } = setup({ response: jsonResponse(status, { detail: 'nope' }) })

    const error = await client.getMe().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status })
  })
})

describe('readApiBaseUrl', () => {
  it('reads the URL and drops a trailing slash', () => {
    expect(readApiBaseUrl({ VITE_API_BASE_URL: ' http://localhost:8000/ ' })).toBe('http://localhost:8000')
  })

  it('names the missing variable', () => {
    expect(() => readApiBaseUrl({})).toThrow(/VITE_API_BASE_URL/)
  })
})

describe('createApiClient().sendChat', () => {
  const chatBody = {
    session_id: 'abc12345session',
    reply: 'Estos son los datos de tu perfil.',
    engine: 'inquiry',
    language: 'es',
    status: 'answered',
    escalated: false,
    handoff_id: null,
  }

  it('POSTs the message to /chat with the ID token as a Bearer header', async () => {
    const { client, fetch } = setup({ response: jsonResponse(200, chatBody) })

    await client.sendChat({ message: 'Muéstrame mi perfil', language: 'es' })

    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('http://localhost:8000/chat')
    expect(init?.method).toBe('POST')
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer id.token.value')
    expect(headers.get('Content-Type')).toBe('application/json')
  })

  it('sends only the fields the backend ChatRequest accepts, never a customer id', async () => {
    const { client, fetch } = setup()
    fetch.mockImplementation(async () => jsonResponse(200, chatBody))

    await client.sendChat({ message: 'hola', sessionId: 'abc12345session', language: 'pt' })
    await client.sendChat({ message: 'primera' })

    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      message: 'hola',
      session_id: 'abc12345session',
      language: 'pt',
    })
    expect(JSON.parse(fetch.mock.calls[1][1]?.body as string)).toEqual({ message: 'primera' })
    expect(String(fetch.mock.calls[0][1]?.body)).not.toMatch(/customer/i)
  })

  it('maps the backend ChatResponse', async () => {
    const { client } = setup({
      response: jsonResponse(200, { ...chatBody, engine: 'escalation', status: 'escalated', escalated: true, handoff_id: 'HO-1' }),
    })

    expect(await client.sendChat({ message: 'Quiero un asesor' })).toEqual({
      sessionId: 'abc12345session',
      reply: 'Estos son los datos de tu perfil.',
      engine: 'escalation',
      language: 'es',
      status: 'escalated',
      escalated: true,
      handoffId: 'HO-1',
    })
  })

  it('fails with 401 without calling the backend when there is no session', async () => {
    const { client, fetch } = setup({ token: null })

    await expect(client.sendChat({ message: 'hola' })).rejects.toMatchObject({ status: 401 })
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([401, 403, 422, 500])('throws an ApiError with status %i', async (status) => {
    const { client } = setup({ response: jsonResponse(status, { detail: 'internal detail' }) })

    await expect(client.sendChat({ message: 'hola' })).rejects.toMatchObject({ name: 'ApiError', status })
  })

  it('reports a network failure as status 0', async () => {
    const { client, fetch } = setup()
    fetch.mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(client.sendChat({ message: 'hola' })).rejects.toMatchObject({ name: 'ApiError', status: 0 })
  })
})
