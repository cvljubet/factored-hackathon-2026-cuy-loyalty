import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { AuthService } from './authService'
import { AuthContext, type AuthContextValue, type AuthStatus } from './context'
import type { AuthUser } from './user'

interface AuthState {
  status: AuthStatus
  user: AuthUser | null
}

const signedOut: AuthState = { status: 'unauthenticated', user: null }

export function AuthProvider({ service, children }: { service: AuthService; children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading', user: null })

  // Restore the stored session on load (and after a page refresh).
  useEffect(() => {
    let active = true
    service.getCurrentUser().then(
      (user) => {
        if (active) setState(user ? { status: 'authenticated', user } : signedOut)
      },
      () => {
        if (active) setState(signedOut)
      },
    )
    const unsubscribe = service.onSessionEnded?.(() => setState(signedOut))
    return () => {
      active = false
      unsubscribe?.()
    }
  }, [service])

  const signIn = useCallback(
    async (email: string, password: string) => {
      const user = await service.signIn(email, password)
      setState({ status: 'authenticated', user })
    },
    [service],
  )

  // Always ends the local session; a failed remote sign-out (e.g. offline) is only logged.
  const signOut = useCallback(async () => {
    try {
      await service.signOut()
    } catch (error) {
      console.warn('Remote sign-out failed; local session cleared anyway.', error)
    }
    setState(signedOut)
  }, [service])

  const value = useMemo<AuthContextValue>(() => ({ ...state, signIn, signOut }), [state, signIn, signOut])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
