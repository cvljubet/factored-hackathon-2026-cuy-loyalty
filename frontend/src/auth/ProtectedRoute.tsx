import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { AccountNotLinked } from '../components/AccountNotLinked'
import { AuthLoading } from '../components/AuthLoading'
import { useProfile } from '../profile/context'
import { useAuth } from './context'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const profile = useProfile()

  if (status === 'loading') return <AuthLoading />
  if (status === 'unauthenticated') return <Navigate to="/login" replace />
  // Wait for the backend to confirm the session before showing the page.
  if (profile.status === 'idle' || profile.status === 'loading') return <AuthLoading />
  if (profile.status === 'forbidden') return <AccountNotLinked />
  // 'error' (backend unreachable) does not block the page; it does not depend on the backend yet.
  return children
}
