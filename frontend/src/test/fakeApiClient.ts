import { vi } from 'vitest'
import { ApiError, type ApiClient, type ChatReply, type ChatRequest, type Me } from '../api/client'

export const testMe: Me = {
  sub: '8b7c1d2e-0000-4000-8000-000000000001',
  email: 'constanza@example.com',
  givenName: 'Constanza',
  familyName: 'Ljubetic',
  customerId: 'C-0001',
}

/** A backend chat reply echoing the request's session (or a new one) and language. */
export function chatReply(request: ChatRequest, overrides: Partial<ChatReply> = {}): ChatReply {
  return {
    sessionId: request.sessionId ?? 'session-from-backend-1',
    reply: `Respuesta a: ${request.message}`,
    engine: 'inquiry',
    language: request.language ?? 'es',
    status: 'answered',
    escalated: false,
    ...overrides,
  }
}

/** An ApiClient whose methods are spies; GET /me and POST /chat succeed unless told otherwise. */
export function createFakeApiClient() {
  return {
    getMe: vi.fn<ApiClient['getMe']>().mockResolvedValue(testMe),
    sendChat: vi.fn<ApiClient['sendChat']>(async (request) => chatReply(request)),
  } satisfies ApiClient
}

export function apiError(status: number): ApiError {
  return new ApiError(status, `HTTP ${status}`)
}
