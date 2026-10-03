import { ChevronDown, Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { User } from '../data/mock'
import { useLanguage } from '../i18n/useLanguage'
import { Logo } from './Logo'

interface HeaderProps {
  user?: User
}

export function Header({ user }: HeaderProps) {
  const { t } = useTranslation()
  const { language, setLanguage, languages } = useLanguage()

  // Placeholder switcher: cycles through the supported languages until a proper selector exists.
  function cycleLanguage() {
    const next = languages[(languages.indexOf(language) + 1) % languages.length]
    setLanguage(next)
  }

  return (
    <header className="shrink-0 bg-ink-900 text-white">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:h-20 sm:px-8">
        <div className="flex items-center gap-3">
          <Logo className="size-8 sm:size-10" />
          <span className="text-lg font-semibold sm:text-2xl">{t('app.name')}</span>
        </div>

        <div className="flex items-center gap-4 sm:gap-8">
          <button
            type="button"
            onClick={cycleLanguage}
            className="flex items-center gap-2 text-sm text-white/90 hover:text-white sm:text-base"
            aria-label={t('language.label', { language: t(`language.names.${language}`) })}
          >
            <Globe className="size-5" />
            <span>{language.toUpperCase()}</span>
            <ChevronDown className="size-4" />
          </button>

          {user && (
            <div className="flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-full bg-mint-200 font-semibold text-ink-900 sm:size-11">
                {user.firstName[0]}
                {user.lastName[0]}
              </span>
              <span className="hidden text-white/90 sm:inline">{user.firstName}</span>
              <ChevronDown className="hidden size-4 text-white/90 sm:block" />
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
