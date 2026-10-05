import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import './i18n'
import { AppRoutes } from './App'
import { createApiClient } from './api/client'
import { ApiContext } from './api/context'
import { readApiBaseUrl } from './api/config'
import { amplifyAuthService, fetchIdToken } from './auth/authService'
import { AuthProvider } from './auth/AuthProvider'
import { configureAmplify } from './auth/config'
import { ProfileProvider } from './profile/ProfileProvider'

configureAmplify()

const apiClient = createApiClient({ baseUrl: readApiBaseUrl(), getIdToken: fetchIdToken })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider service={amplifyAuthService}>
      <ApiContext.Provider value={apiClient}>
        <ProfileProvider api={apiClient}>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </ProfileProvider>
      </ApiContext.Provider>
    </AuthProvider>
  </StrictMode>,
)
