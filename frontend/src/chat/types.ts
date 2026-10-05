export type MessageRole = 'user' | 'assistant'

export interface Message {
  id: string
  role: MessageRole
  text: string
  sentAt: Date
}
