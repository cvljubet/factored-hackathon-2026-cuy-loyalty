import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import i18n, { DEFAULT_LANGUAGE } from '../i18n'

// jsdom does not implement scrolling; the chat scrolls its message list as replies arrive.
Element.prototype.scrollTo ??= () => {}

beforeEach(async () => {
  await i18n.changeLanguage(DEFAULT_LANGUAGE)
})

afterEach(() => {
  cleanup()
})
