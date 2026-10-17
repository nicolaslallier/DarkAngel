import { apiGet, apiRequest, apiSend, apiStream } from './client'

export type MessageStatus = 'streaming' | 'complete' | 'stopped' | 'error'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  reasoning: string | null
  model: string | null
  status: MessageStatus
  error: string | null
  created_at: string
}

export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
  streaming: boolean
}

export interface Conversation {
  id: string
  title: string
  streaming: boolean
  messages: ChatMessage[]
}

export interface ModelInfo {
  id: string
  loaded: boolean | null
  context_length: number
}

export type StreamEvent =
  | {
      event: 'start'
      data: {
        conversation: { id: string; title: string }
        user_message: ChatMessage | null
        assistant_message_id: string
      }
    }
  | { event: 'snapshot'; data: { reasoning: string; content: string } }
  | { event: 'progress'; data: { processed: number; total: number } }
  | { event: 'reasoning'; data: { text: string } }
  | { event: 'content'; data: { text: string } }
  | { event: 'error'; data: { message: string } }
  | { event: 'done'; data: { message: ChatMessage } }

const base = (id: string) => `/chat/conversations/${encodeURIComponent(id)}`

export function listModels(): Promise<ModelInfo[]> {
  return apiGet<ModelInfo[]>('/chat/models')
}

export function listConversations(q?: string): Promise<ConversationSummary[]> {
  return apiGet<ConversationSummary[]>(
    q ? `/chat/conversations?q=${encodeURIComponent(q)}` : '/chat/conversations',
  )
}

export function getConversation(id: string): Promise<Conversation> {
  return apiGet<Conversation>(base(id))
}

export function renameConversation(id: string, title: string): Promise<ConversationSummary> {
  return apiSend<ConversationSummary>('PATCH', base(id), { title })
}

export async function deleteConversation(id: string): Promise<void> {
  await apiRequest('DELETE', base(id))
}

export async function stopReply(id: string): Promise<void> {
  await apiRequest('POST', `${base(id)}/stop`)
}

// The server only emits the documented events, so the cast is the typing.
export function sendMessage(
  id: string | 'new',
  body: { content: string; model: string; replace_from?: string },
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  return apiStream('POST', `${base(id)}/messages`, body, signal) as AsyncGenerator<StreamEvent>
}

export function regenerate(
  id: string,
  model: string,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  return apiStream('POST', `${base(id)}/regenerate`, { model }, signal) as AsyncGenerator<StreamEvent>
}

export function attach(id: string, signal: AbortSignal): AsyncGenerator<StreamEvent> {
  return apiStream('GET', `${base(id)}/stream`, undefined, signal) as AsyncGenerator<StreamEvent>
}
