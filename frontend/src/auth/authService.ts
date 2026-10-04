import { fetchUserAttributes, getCurrentUser, signIn, signOut } from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import { AuthChallengeError } from './errors'
import type { AuthUser } from './user'

/** What the app needs from an auth backend; tests substitute a fake. */
export interface AuthService {
  /** The signed-in user restored from the stored session, or null. */
  getCurrentUser(): Promise<AuthUser | null>
  signIn(email: string, password: string): Promise<AuthUser>
  signOut(): Promise<void>
  /** Calls back when the session ends outside the app's own signOut (e.g. refresh failure). */
  onSessionEnded?(callback: () => void): () => void
}

async function loadUser(): Promise<AuthUser> {
  const attributes = await fetchUserAttributes()
  return {
    email: attributes.email ?? '',
    givenName: attributes.given_name,
    familyName: attributes.family_name,
    customerId: attributes['custom:customer_id'],
  }
}

// Amplify runs the SRP exchange and keeps the tokens in its own storage, so the
// app never handles the password beyond passing it through, nor any JWT.
export const amplifyAuthService: AuthService = {
  async getCurrentUser() {
    try {
      await getCurrentUser()
    } catch {
      return null
    }
    return loadUser()
  },

  async signIn(email, password) {
    try {
      const { isSignedIn, nextStep } = await signIn({ username: email, password })
      if (!isSignedIn) throw new AuthChallengeError(nextStep.signInStep)
    } catch (error) {
      // A session already exists (e.g. signed in from another tab); reuse it.
      if (!(error instanceof Error && error.name === 'UserAlreadyAuthenticatedException')) throw error
    }
    return loadUser()
  },

  async signOut() {
    await signOut()
  },

  onSessionEnded(callback) {
    return Hub.listen('auth', ({ payload }) => {
      if (payload.event === 'signedOut' || payload.event === 'tokenRefresh_failure') callback()
    })
  },
}
