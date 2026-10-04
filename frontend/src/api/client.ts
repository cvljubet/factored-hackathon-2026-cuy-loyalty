/** The backend answered with a non-2xx status (or there was no session to call it with). */
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

export interface ApiClient {
  getMe(): Promise<Me>
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
  async function get<T>(path: string): Promise<T> {
    const token = await getIdToken()
    if (!token) throw new ApiError(401, 'No active session')

    const response = await fetchFn(`${baseUrl}${path}`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
    })
    if (!response.ok) throw new ApiError(response.status, `GET ${path} failed with ${response.status}`)
    return (await response.json()) as T
  }

  return {
    async getMe() {
      const body = await get<MeResponse>('/me')
      return {
        sub: body.sub,
        email: body.email ?? undefined,
        givenName: body.given_name ?? undefined,
        familyName: body.family_name ?? undefined,
        customerId: body.customer_id,
      }
    },
  }
}
