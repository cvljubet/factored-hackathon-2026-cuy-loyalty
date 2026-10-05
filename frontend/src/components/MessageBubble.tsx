import { CheckCheck, User as UserIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Message } from '../chat/types'
import { Logo } from './Logo'

function formatTime(date: Date, language: string): string {
  return date.toLocaleTimeString(language, { hour: 'numeric', minute: '2-digit' })
}

export function MessageBubble({ message }: { message: Message }) {
  const { i18n } = useTranslation()
  const time = formatTime(message.sentAt, i18n.resolvedLanguage ?? i18n.language)

  if (message.role === 'user') {
    return (
      <div className="flex items-start justify-end gap-3 sm:gap-5">
        <div className="flex max-w-[85%] flex-col items-end sm:max-w-[75%]">
          <p className="rounded-2xl bg-mint-300/70 px-5 py-3 text-ink-900 shadow-sm sm:px-7 sm:py-4 sm:text-lg">
            {message.text}
          </p>
          <span className="mt-2 flex items-center gap-1.5 text-sm text-slate-500">
            {time}
            <CheckCheck className="size-4 text-brand-500" />
          </span>
        </div>
        <span className="hidden size-12 shrink-0 items-center justify-center rounded-full bg-mint-200 sm:flex">
          <UserIcon className="size-6 text-ink-800" strokeWidth={1.75} />
        </span>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-3 sm:gap-5">
      <span className="hidden size-12 shrink-0 items-center justify-center rounded-full bg-white shadow-sm sm:flex">
        <Logo className="size-7" />
      </span>
      <div className="flex max-w-[85%] flex-col items-start sm:max-w-[65%]">
        <p className="rounded-2xl bg-white px-5 py-3 text-ink-900 shadow-sm sm:px-7 sm:py-4 sm:text-lg">
          {message.text}
        </p>
        <span className="mt-2 text-sm text-slate-500">{time}</span>
      </div>
    </div>
  )
}
