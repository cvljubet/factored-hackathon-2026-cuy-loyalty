import { createContext, useContext } from 'react'
import type { Me } from '../api/client'

/**
 * The backend's view of the signed-in user (GET /me):
 * - idle: no Cognito session
 * - loading: /me is in flight (a rejected session is signed out from here)
 * - ready: the user is linked to a customer
 * - forbidden: signed in, but not linked to a customer (403)
 * - error: the backend could not be reached or failed
 */
export type ProfileState =
  | { status: 'idle' | 'loading' | 'forbidden' | 'error'; me: null }
  | { status: 'ready'; me: Me }

export const ProfileContext = createContext<ProfileState | null>(null)

export function useProfile(): ProfileState {
  const value = useContext(ProfileContext)
  if (!value) throw new Error('useProfile must be used inside <ProfileProvider>')
  return value
}
