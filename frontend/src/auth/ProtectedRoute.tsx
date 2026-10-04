import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { AuthLoading } from '../components/AuthLoading'
import { useAuth } from './context'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'loading') return <AuthLoading />
  if (status === 'unauthenticated') return <Navigate to="/login" replace />
  return children
}
