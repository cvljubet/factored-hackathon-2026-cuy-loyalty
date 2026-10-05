import { useTranslation } from 'react-i18next'
import { Logo } from './Logo'

/** An assistant bubble with animated dots while the backend is answering. */
export function TypingIndicator() {
  const { t } = useTranslation()

  return (
    <div className="flex items-start gap-3 sm:gap-5" role="status" aria-label={t('chat.typing')}>
      <span className="hidden size-12 shrink-0 items-center justify-center rounded-full bg-white shadow-sm sm:flex">
        <Logo className="size-7" />
      </span>
      <p className="flex items-center gap-1.5 rounded-2xl bg-white px-5 py-4 shadow-sm sm:px-7 sm:py-5" aria-hidden="true">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-2 animate-bounce rounded-full bg-slate-400"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </p>
    </div>
  )
}
