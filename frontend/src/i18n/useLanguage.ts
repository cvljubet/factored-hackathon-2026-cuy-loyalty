import { useTranslation } from 'react-i18next'
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, isSupportedLanguage, type Language } from '.'

/** Current language plus a setter; the choice is persisted to localStorage by the i18n setup. */
export function useLanguage() {
  const { i18n } = useTranslation()
  const language: Language = isSupportedLanguage(i18n.resolvedLanguage) ? i18n.resolvedLanguage : DEFAULT_LANGUAGE

  function setLanguage(next: Language) {
    void i18n.changeLanguage(next)
  }

  return { language, setLanguage, languages: SUPPORTED_LANGUAGES }
}
