import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/context'
import { Header } from './Header'

/** Shown when the backend answers 403: signed in, but not linked to a customer record. */
export function AccountNotLinked() {
  const { t } = useTranslation()
  const { user, signOut } = useAuth()

  return (
    <div className="flex min-h-dvh flex-col">
      <Header user={user ?? undefined} onSignOut={signOut} />
      <main className="flex flex-1 items-center justify-center bg-mint-50 px-4">
        <div role="alert" className="max-w-md rounded-3xl bg-white p-8 text-center shadow-lg shadow-ink-900/5">
          <h1 className="text-2xl font-bold">{t('profile.notLinked.title')}</h1>
          <p className="mt-3 text-slate-500">{t('profile.notLinked.description')}</p>
          <button
            type="button"
            onClick={signOut}
            className="mt-6 rounded-full bg-brand-500 px-6 py-3 font-semibold text-white transition-colors hover:bg-brand-600"
          >
            {t('auth.signOut')}
          </button>
        </div>
      </main>
    </div>
  )
}
