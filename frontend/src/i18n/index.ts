import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import es from './locales/es.json'
import pt from './locales/pt.json'

export const SUPPORTED_LANGUAGES = ['es', 'pt'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]
export const DEFAULT_LANGUAGE: Language = 'es'

const STORAGE_KEY = 'cuy-loyalty.language'

export const resources = {
  es: { translation: es },
  pt: { translation: pt },
} as const

export function isSupportedLanguage(value: unknown): value is Language {
  return SUPPORTED_LANGUAGES.includes(value as Language)
}

function readStoredLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return isSupportedLanguage(stored) ? stored : DEFAULT_LANGUAGE
  } catch {
    return DEFAULT_LANGUAGE
  }
}

function applyLanguage(language: string) {
  try {
    localStorage.setItem(STORAGE_KEY, language)
  } catch {
    // Storage can be unavailable (e.g. private mode); the language still applies for this session.
  }
  document.documentElement.lang = language
  document.title = i18n.t('app.title')
}

i18n.on('languageChanged', applyLanguage)

void i18n.use(initReactI18next).init({
  resources,
  lng: readStoredLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: SUPPORTED_LANGUAGES,
  interpolation: { escapeValue: false },
})

export default i18n
