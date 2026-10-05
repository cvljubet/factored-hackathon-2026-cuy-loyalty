import { ApiError } from '../api/client'

export type ChatErrorKey =
  | 'chat.errors.sessionExpired'
  | 'chat.errors.notLinked'
  | 'chat.errors.invalidMessage'
  | 'chat.errors.network'
  | 'chat.errors.server'

/** Maps a failed /chat call to a translated message; raw backend details are never shown. */
export function chatErrorKey(error: unknown): ChatErrorKey {
  const status = error instanceof ApiError ? error.status : 0
  if (status === 401) return 'chat.errors.sessionExpired'
  if (status === 403) return 'chat.errors.notLinked'
  if (status === 422) return 'chat.errors.invalidMessage'
  if (status === 0) return 'chat.errors.network'
  return 'chat.errors.server'
}
