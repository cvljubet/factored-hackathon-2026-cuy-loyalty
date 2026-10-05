import type { Language } from '../i18n'

/** The backend answered with a non-2xx status (or there was no session to call it with). Status 0 means no response. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The signed-in user as the backend sees them, from their verified ID token. */
export interface Me {
  sub: string
  email?: string
  givenName?: string
  familyName?: string
  customerId: string
}

interface MeResponse {
  sub: string
  email: string | null
  given_name: string | null
  family_name: string | null
  customer_id: string
}

/** One chat turn to send. Mirrors the backend ChatRequest, which rejects unknown fields. */
export interface ChatRequest {
  message: string
  /** The session_id from the previous reply, to continue the same conversation. */
  sessionId?: string
  /** The UI language; the backend may still answer in the language the customer writes in. */
  language?: Language
}

export type ChatEngine = 'inquiry' | 'recommendation' | 'escalation' | 'out_of_scope'
export type ChatStatus = 'answered' | 'unavailable' | 'declined' | 'blocked' | 'failed' | 'escalated' | 'out_of_scope'

/** The assistant's reply (backend ChatResponse). */
export interface ChatReply {
  sessionId: string
  reply: string
  engine: ChatEngine
  language: Language
  status: ChatStatus
  escalated: boolean
  handoffId?: string
}

interface ChatResponseBody {
  session_id: string
  reply: string
  engine: ChatEngine
  language: Language
  status: ChatStatus
  escalated: boolean
  handoff_id: string | null
}

export interface ApiClient {
  getMe(): Promise<Me>
  sendChat(request: ChatRequest): Promise<ChatReply>
}

export interface ApiClientOptions {
  baseUrl: string
  /** The current Cognito ID token, or null when there is no session. */
  getIdToken(): Promise<string | null>
  fetch?: typeof fetch
}

// Identity comes only from the token: the backend reads customer_id from it,
// so the client never sends one.
export function createApiClient({ baseUrl, getIdToken, fetch: fetchFn = fetch }: ApiClientOptions): ApiClient {
  async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const token = await getIdToken()
    if (!token) throw new ApiError(401, 'No active session')

    const headers: Record<string, string> = { Accept: 'application/json', Authorization: `Bearer ${token}` }
    if (body !== undefined) headers['Content-Type'] = 'application/json'

    let response: Response
    try {
      response = await fetchFn(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ApiError(0, `${method} ${path} got no response`)
    }
    if (!response.ok) throw new ApiError(response.status, `${method} ${path} failed with ${response.status}`)
    return (await response.json()) as T
  }

  return {
    async getMe() {
      const body = await request<MeResponse>('GET', '/me')
      return {
        sub: body.sub,
        email: body.email ?? undefined,
        givenName: body.given_name ?? undefined,
        familyName: body.family_name ?? undefined,
        customerId: body.customer_id,
      }
    },

    async sendChat({ message, sessionId, language }) {
      // Only fields the backend accepts; never a customer identifier.
      const payload: Record<string, string> = { message }
      if (sessionId) payload.session_id = sessionId
      if (language) payload.language = language

      const body = await request<ChatResponseBody>('POST', '/chat', payload)
      return {
        sessionId: body.session_id,
        reply: body.reply,
        engine: body.engine,
        language: body.language,
        status: body.status,
        escalated: body.escalated,
        handoffId: body.handoff_id ?? undefined,
      }
    },
  }
}
