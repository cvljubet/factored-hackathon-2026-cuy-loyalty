// Placeholder conversation until the chat API is wired up.

export type MessageRole = 'user' | 'assistant'

export interface Message {
  id: string
  role: MessageRole
  text: string
  sentAt: Date
}

function todayAt(hours: number, minutes: number): Date {
  const date = new Date()
  date.setHours(hours, minutes, 0, 0)
  return date
}

export const mockMessages: Message[] = [
  {
    id: 'm1',
    role: 'user',
    text: 'Hola, me gustaría saber si hay algún descuento si compro pasajes para volar a Japón con Latam',
    sentAt: todayAt(10, 24),
  },
  {
    id: 'm2',
    role: 'assistant',
    text: 'Por supuesto, déjame revisar los últimos movimientos de tu cuenta para ver qué beneficios aplican.',
    sentAt: todayAt(10, 24),
  },
]
