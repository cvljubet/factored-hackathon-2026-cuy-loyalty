import { LoaderCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

/** Shown while the stored session is being restored. */
export function AuthLoading() {
  const { t } = useTranslation()

  return (
    <div role="status" className="flex min-h-dvh items-center justify-center bg-mint-50">
      <LoaderCircle className="size-8 animate-spin text-brand-500" aria-hidden="true" />
      <span className="sr-only">{t('auth.loading')}</span>
    </div>
  )
}
