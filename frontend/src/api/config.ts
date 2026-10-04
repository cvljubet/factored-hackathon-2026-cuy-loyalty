type ApiEnv = Pick<ImportMetaEnv, 'VITE_API_BASE_URL'>

/** The backend's base URL, without a trailing slash. */
export function readApiBaseUrl(env: ApiEnv = import.meta.env): string {
  const value = env.VITE_API_BASE_URL?.trim()
  if (!value) {
    throw new Error('Missing API configuration: VITE_API_BASE_URL. Copy frontend/.env.example to .env.local.')
  }
  return value.replace(/\/+$/, '')
}
