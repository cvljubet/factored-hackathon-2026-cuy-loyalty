import { fetchAuthSession } from 'aws-amplify/auth'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchIdToken } from './authService'

vi.mock('aws-amplify/auth')

const fetchAuthSessionMock = vi.mocked(fetchAuthSession)

function jwt(value: string) {
  return { toString: () => value, payload: {} }
}

describe('fetchIdToken', () => {
  beforeEach(() => {
    fetchAuthSessionMock.mockReset()
  })

  it('returns the ID token, not the access token', async () => {
    fetchAuthSessionMock.mockResolvedValue({ tokens: { idToken: jwt('id-token'), accessToken: jwt('access-token') } })

    expect(await fetchIdToken()).toBe('id-token')
  })

  it('returns null without a session', async () => {
    fetchAuthSessionMock.mockResolvedValue({})

    expect(await fetchIdToken()).toBeNull()
  })

  it('returns null when the session cannot be loaded or refreshed', async () => {
    fetchAuthSessionMock.mockRejectedValue(new Error('refresh failed'))

    expect(await fetchIdToken()).toBeNull()
  })
})
