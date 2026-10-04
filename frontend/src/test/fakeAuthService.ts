import { vi } from 'vitest'
import type { AuthService } from '../auth/authService'
import type { AuthUser } from '../auth/user'

export const testUser: AuthUser = {
  email: 'constanza@example.com',
  givenName: 'Constanza',
  familyName: 'Ljubetic',
  customerId: 'C-0001',
}

/** An AuthService whose methods are spies; `endSession` simulates Amplify signalling a lost session. */
export function createFakeAuthService(options: { sessionUser?: AuthUser | null } = {}) {
  let sessionEnded: (() => void) | undefined

  const service = {
    getCurrentUser: vi.fn<AuthService['getCurrentUser']>().mockResolvedValue(options.sessionUser ?? null),
    signIn: vi.fn<AuthService['signIn']>().mockResolvedValue(testUser),
    signOut: vi.fn<AuthService['signOut']>().mockResolvedValue(undefined),
    onSessionEnded: vi.fn((callback: () => void) => {
      sessionEnded = callback
      return () => {
        sessionEnded = undefined
      }
    }),
  } satisfies AuthService

  return { service, endSession: () => sessionEnded?.() }
}

export function namedError(name: string): Error {
  const error = new Error(name)
  error.name = name
  return error
}
