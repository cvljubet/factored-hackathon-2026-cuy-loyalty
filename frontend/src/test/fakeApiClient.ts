import { vi } from 'vitest'
import { ApiError, type ApiClient, type Me } from '../api/client'

export const testMe: Me = {
  sub: '8b7c1d2e-0000-4000-8000-000000000001',
  email: 'constanza@example.com',
  givenName: 'Constanza',
  familyName: 'Ljubetic',
  customerId: 'C-0001',
}

/** An ApiClient whose methods are spies; GET /me succeeds unless told otherwise. */
export function createFakeApiClient() {
  return {
    getMe: vi.fn<ApiClient['getMe']>().mockResolvedValue(testMe),
  } satisfies ApiClient
}

export function apiError(status: number): ApiError {
  return new ApiError(status, `HTTP ${status}`)
}
