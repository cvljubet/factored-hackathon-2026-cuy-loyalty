import { useEffect, useState, type ReactNode } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import { useAuth } from '../auth/context'
import type { AuthUser } from '../auth/user'
import { ProfileContext, type ProfileState } from './context'

const idle: ProfileState = { status: 'idle', me: null }
const loading: ProfileState = { status: 'loading', me: null }

/** Loads GET /me once a Cognito session exists, and again for each new sign-in. */
export function ProfileProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const { status, user, signOut } = useAuth()
  // Tagged with the user it was loaded for, so a result never outlives its session.
  const [result, setResult] = useState<{ user: AuthUser; state: ProfileState } | null>(null)

  useEffect(() => {
    if (status !== 'authenticated' || !user) return
    let active = true
    api.getMe().then(
      (me) => {
        if (active) setResult({ user, state: { status: 'ready', me } })
      },
      (error: unknown) => {
        if (!active) return
        const httpStatus = error instanceof ApiError ? error.status : undefined
        if (httpStatus === 401) {
          // The backend rejected the session (e.g. revoked or wrong client); start over at login.
          void signOut()
        } else if (httpStatus === 403) {
          setResult({ user, state: { status: 'forbidden', me: null } })
        } else {
          console.warn('Could not load the profile from the backend.', error)
          setResult({ user, state: { status: 'error', me: null } })
        }
      },
    )
    return () => {
      active = false
    }
  }, [api, status, user, signOut])

  const value = status !== 'authenticated' ? idle : result?.user === user ? result.state : loading

  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>
}
