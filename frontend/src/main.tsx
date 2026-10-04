import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import './i18n'
import { AppRoutes } from './App'
import { amplifyAuthService } from './auth/authService'
import { AuthProvider } from './auth/AuthProvider'
import { configureAmplify } from './auth/config'

configureAmplify()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider service={amplifyAuthService}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
)
