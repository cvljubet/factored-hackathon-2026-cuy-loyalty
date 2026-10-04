import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import i18n, { DEFAULT_LANGUAGE } from '../i18n'

beforeEach(async () => {
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

afterEach(() => {
  cleanup()
})
