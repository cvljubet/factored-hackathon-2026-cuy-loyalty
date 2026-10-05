import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Paperclip, Send } from 'lucide-react'
import { Trans, useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../api/context'
import { useAuth } from '../auth/context'
import { displayName, type AuthUser } from '../auth/user'
import { MAX_MESSAGE_LENGTH, useChat } from '../chat/useChat'
import { Header } from '../components/Header'
import { MessageBubble } from '../components/MessageBubble'
import { TypingIndicator } from '../components/TypingIndicator'
import { useLanguage } from '../i18n/useLanguage'

export function ChatPage() {
  const { user } = useAuth()

  // ProtectedRoute only renders this page for a signed-in user. Keying by user gives each
  // user a fresh conversation (and backend session), so nothing carries over between accounts.
  if (!user) return null
  return <Conversation key={user.email} user={user} />
}

function Conversation({ user }: { user: AuthUser }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { signOut } = useAuth()
  const { language } = useLanguage()
  const { messages, pending, error, send } = useChat(useApi(), language)
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const messageCount = useRef(messages.length)

  // Scroll only the conversation container (scrollIntoView would also scroll its ancestors),
  // and only when a message is added so the greeting stays visible on first load.
  useEffect(() => {
    if (messages.length > messageCount.current || pending) {
      const scroller = scrollRef.current
      scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
    }
    messageCount.current = messages.length
  }, [messages, pending])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (send(draft)) setDraft('')
  }

  async function handleSignOut() {
    await signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-dvh flex-col">
      <Header user={user} onSignOut={handleSignOut} />

      <main className="relative flex min-h-0 flex-1 flex-col overflow-clip bg-mint-50">
        {/* Soft decorative curves from the reference */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-1/3 -left-1/4 hidden sm:block size-[36rem] rounded-full bg-mint-100"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-1/4 hidden sm:block -bottom-1/3 size-[32rem] rounded-full bg-mint-100"
        />

        <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-4xl px-4 py-10 sm:px-8 sm:pt-24 sm:pb-16">
            <div className="sm:px-16">
              <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">{t('chat.greeting', { name: displayName(user) })}</h1>
              <p className="mt-3 max-w-2xl text-lg text-slate-500 sm:text-2xl sm:leading-snug">
                <Trans i18nKey="chat.intro" components={{ highlight: <span className="font-semibold text-slate-600" /> }} />
              </p>
            </div>

            <div className="mt-10 space-y-8 sm:mt-14" aria-live="polite">
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              {pending && <TypingIndicator />}
            </div>
          </div>
        </div>

        <div className="relative shrink-0 px-4 pb-6 pt-2 sm:px-8 sm:pb-10">
          {error && (
            <div
              role="alert"
              className="mx-auto mb-3 flex max-w-4xl flex-wrap items-center justify-between gap-3 rounded-2xl bg-white px-5 py-3 text-sm text-red-700 shadow-sm sm:text-base"
            >
              <span>{t(error)}</span>
              {error === 'chat.errors.sessionExpired' && (
                <button type="button" onClick={handleSignOut} className="font-semibold text-brand-600 hover:underline">
                  {t('chat.signInAgain')}
                </button>
              )}
            </div>
          )}
          <form
            onSubmit={handleSubmit}
            className="mx-auto flex max-w-4xl items-center gap-3 rounded-full bg-white py-2 pl-5 pr-2 shadow-lg shadow-ink-900/5 sm:gap-5 sm:pl-7"
          >
            <button type="button" className="text-slate-500 hover:text-ink-900" aria-label={t('chat.attachFile')}>
              <Paperclip className="size-6" />
            </button>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={t('chat.inputPlaceholder')}
              aria-label={t('chat.inputLabel')}
              maxLength={MAX_MESSAGE_LENGTH}
              className="min-w-0 flex-1 py-3 outline-none placeholder:text-slate-400 sm:text-lg"
            />
            <button
              type="submit"
              disabled={!draft.trim() || pending}
              className="flex size-12 shrink-0 items-center justify-center rounded-full bg-brand-500 text-white transition-colors hover:bg-brand-600 disabled:opacity-60 sm:size-14"
              aria-label={t('chat.send')}
            >
              <Send className="size-5 sm:size-6" />
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}
