import 'i18next'
import type es from './locales/es.json'

// Type-checks translation keys against the Spanish (default) file.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    resources: { translation: typeof es }
  }
}
