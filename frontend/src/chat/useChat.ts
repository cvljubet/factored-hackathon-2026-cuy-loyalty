import { useCallback, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { Language } from '../i18n'
import { chatErrorKey, type ChatErrorKey } from './errors'
import type { Message } from './types'

export const MAX_MESSAGE_LENGTH = 2000

/**
 * The conversation with the backend assistant.
 *
 * State lives only in this hook's component, which is mounted per signed-in user
 * (see ChatPage), so the messages and the backend session_id are dropped on sign-out
 * and never carried over to another user.
 */
export function useChat(api: Pick<ApiClient, 'sendChat'>, language: Language) {
  const [messages, setMessages] = useState<Message[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<ChatErrorKey | null>(null)
  const sessionId = useRef<string | undefined>(undefined)
  // A ref, not state, so a second submit in the same tick is also refused.
  const inFlight = useRef(false)

  /** Sends a message; returns false (and sends nothing) if it is empty, too long or a reply is pending. */
  const send = useCallback(
    (input: string): boolean => {
      const text = input.trim()
      if (!text || text.length > MAX_MESSAGE_LENGTH || inFlight.current) return false

      inFlight.current = true
      setPending(true)
      setError(null)
      // Shown right away, before the backend answers.
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', text, sentAt: new Date() }])

      api
        .sendChat({ message: text, sessionId: sessionId.current, language })
        .then((reply) => {
          sessionId.current = reply.sessionId
          // Every status (including unavailable data and escalation acknowledgements) is a normal reply.
          setMessages((current) => [
            ...current,
            { id: crypto.randomUUID(), role: 'assistant', text: reply.reply, sentAt: new Date() },
          ])
        })
        .catch((failure: unknown) => setError(chatErrorKey(failure)))
        .finally(() => {
          inFlight.current = false
          setPending(false)
        })
      return true
    },
    [api, language],
  )

  return { messages, pending, error, send }
}
