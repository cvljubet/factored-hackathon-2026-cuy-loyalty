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
