import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import * as api from '@/api/chat'
import type { Conversation } from '@/api/chat'
import { useChatStore } from '@/stores/chat'

vi.mock('@/api/chat', () => ({
  listModels: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
  stopReply: vi.fn(),
  sendMessage: vi.fn(),
  regenerate: vi.fn(),
  attach: vi.fn(),
}))

const msg = (id: string, role: 'user' | 'assistant', content = '', status = 'complete') =>
  ({ id, role, content, reasoning: null, model: 'm', status, error: null, created_at: 'x' }) as never
const conv = (id: string, streaming = false, messages: unknown[] = []) =>
  ({ id, title: 'T', streaming, messages }) as never

async function* script(events: unknown[]) {
  for (const e of events) yield e as never
}
// A stream that yields `events` then waits for the abort signal.
function hanging(events: unknown[], signal: AbortSignal) {
  return (async function* () {
    for (const e of events) yield e as never
    await new Promise<void>((_, rej) =>
      signal.addEventListener('abort', () => rej(new DOMException('x', 'AbortError'))),
    )
  })()
}
const start = (id = 'c1') => ({
  event: 'start',
  data: { conversation: { id, title: 'New' }, user_message: msg('u1', 'user', 'hi'), assistant_message_id: 'a1' },
})
const text = (t: string) => ({ event: 'content', data: { text: t } })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  localStorage.clear()
  vi.mocked(api.listConversations).mockResolvedValue([])
})

it('send on a new chat adopts the conversation from start', async () => {
  vi.mocked(api.sendMessage).mockReturnValue(script([start()]))
  const s = useChatStore()
  s.model = 'm'
  await s.send('hi')
  expect(vi.mocked(api.sendMessage).mock.calls[0][0]).toBe('new')
  expect(s.current?.id).toBe('c1')
  expect(s.current?.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
})

it('deltas append to the streaming assistant message', async () => {
  let mid: string | undefined
  vi.mocked(api.sendMessage).mockReturnValue(
    (async function* () {
      yield start() as never
      yield text('Hel') as never
      yield { event: 'reasoning', data: { text: 'th' } } as never
      yield text('lo') as never
      mid = useChatStore().current?.messages[1].content
    })(),
  )
  const s = useChatStore()
  await s.send('hi')
  expect(mid).toBe('Hello')
  expect(s.current?.messages[1].reasoning).toBe('th')
})

it('done replaces the message and refreshes the list', async () => {
  vi.mocked(api.sendMessage).mockReturnValue(
    script([start(), text('x'), { event: 'done', data: { message: msg('a1', 'assistant', 'final') } }]),
  )
  const s = useChatStore()
  await s.send('hi')
  expect(s.current?.messages[1].content).toBe('final')
  expect(api.listConversations).toHaveBeenCalled()
  expect(s.streaming).toBe(false)
})

it('title event renames current and list entry', async () => {
  const s = useChatStore()
  s.conversations = [{ id: 'c1', title: 'New', updated_at: 'x', streaming: true }]
  vi.mocked(api.sendMessage).mockReturnValue(script([start(), { event: 'title', data: { title: 'Nice' } }]))
  await s.send('hi')
  expect(s.current?.title).toBe('Nice')
  expect(s.conversations[0].title).toBe('Nice')
})

it('open aborts the previous stream without calling stop', async () => {
  let sig!: AbortSignal
  vi.mocked(api.sendMessage).mockImplementation((_i, _b, signal) => {
    sig = signal
    return hanging([start(), text('a')], signal)
  })
  vi.mocked(api.getConversation).mockResolvedValue(conv('c2'))
  const s = useChatStore()
  const sending = s.send('hi')
  await vi.waitFor(() => expect(s.current?.messages[1]?.content).toBe('a'))
  await s.open('c2')
  await sending
  expect(sig.aborted).toBe(true)
  expect(api.stopReply).not.toHaveBeenCalled()
  expect(s.current?.id).toBe('c2')
  expect(s.error).toBeNull()
  expect(s.streaming).toBe(false)
})

it('events from a superseded stream never touch the new current', async () => {
  let release!: () => void
  vi.mocked(api.sendMessage).mockReturnValue(
    (async function* () {
      yield start() as never
      await new Promise<void>((r) => (release = r))
      yield text('LATE') as never
    })(),
  )
  vi.mocked(api.getConversation).mockResolvedValue(conv('c2', false, [msg('x', 'assistant', 'ok')]))
  const s = useChatStore()
  const sending = s.send('hi')
  await vi.waitFor(() => expect(release).toBeDefined())
  await s.open('c2')
  release()
  await sending
  expect(s.current?.messages[0].content).toBe('ok')
})

it('open on a streaming conversation attaches and applies snapshot', async () => {
  vi.mocked(api.getConversation).mockResolvedValue(
    conv('c1', true, [msg('u1', 'user', 'hi'), msg('a1', 'assistant', '', 'streaming')]),
  )
  vi.mocked(api.attach).mockImplementation((_i, signal) =>
    hanging([{ event: 'snapshot', data: { reasoning: 'r', content: 'abc' } }, text('d')], signal),
  )
  const s = useChatStore()
  await s.open('c1')
  await vi.waitFor(() => expect(s.current?.messages[1].content).toBe('abcd'))
  expect(s.current?.messages[1].reasoning).toBe('r')
  expect(s.streaming).toBe(true)
  s.leave()
  await vi.waitFor(() => expect(s.streaming).toBe(false))
  expect(s.error).toBeNull()
})

it('attach returning nothing refetches the conversation', async () => {
  vi.mocked(api.getConversation)
    .mockResolvedValueOnce(conv('c1', true, [msg('a1', 'assistant', '', 'streaming')]))
    .mockResolvedValueOnce(conv('c1', false, [msg('a1', 'assistant', 'done')]))
  vi.mocked(api.attach).mockReturnValue(script([]))
  const s = useChatStore()
  await s.open('c1')
  await vi.waitFor(() => expect(s.current?.messages[0].content).toBe('done'))
  expect(s.streaming).toBe(false)
})

it('stop calls stopReply', async () => {
  const s = useChatStore()
  s.current = conv('c1')
  await s.stop()
  expect(api.stopReply).toHaveBeenCalledWith('c1')
})

it('model defaults to localStorage then first loaded then first', async () => {
  const models = [
    { id: 'a', loaded: false, context_length: 1 },
    { id: 'b', loaded: true, context_length: 1 },
    { id: 'c', loaded: true, context_length: 1 },
  ]
  vi.mocked(api.listModels).mockResolvedValue(models)
  localStorage.setItem('darkangel.chat.model', 'c')
  let s = useChatStore()
  await s.loadModels()
  expect(s.model).toBe('c')

  setActivePinia(createPinia())
  localStorage.setItem('darkangel.chat.model', 'gone')
  s = useChatStore()
  await s.loadModels()
  expect(s.model).toBe('b')

  setActivePinia(createPinia())
  vi.mocked(api.listModels).mockResolvedValue([models[0]])
  localStorage.clear()
  s = useChatStore()
  await s.loadModels()
  expect(s.model).toBe('a')
  s.selectModel('a')
  expect(localStorage.getItem('darkangel.chat.model')).toBe('a')
})

it('edit passes replace_from and drops later messages locally', async () => {
  let seen: number | undefined
  vi.mocked(api.getConversation).mockResolvedValue(conv('c1'))
  vi.mocked(api.sendMessage).mockImplementation(() => {
    seen = useChatStore().current?.messages.length
    return script([])
  })
  const s = useChatStore()
  s.model = 'm'
  s.current = conv('c1', false, [msg('u1', 'user'), msg('a1', 'assistant'), msg('u2', 'user'), msg('a2', 'assistant')])
  await s.send('new text', 'u2')
  expect(vi.mocked(api.sendMessage).mock.calls[0][1]).toMatchObject({ content: 'new text', model: 'm', replace_from: 'u2' })
  expect(vi.mocked(api.sendMessage).mock.calls[0][0]).toBe('c1')
  expect(seen).toBe(2)
})

it('error event sets error and ends streaming', async () => {
  vi.mocked(api.sendMessage).mockReturnValue(
    script([start(), { event: 'error', data: { message: 'boom' } }, { event: 'done', data: { message: msg('a1', 'assistant', '', 'error') } }]),
  )
  const s = useChatStore()
  await s.send('hi')
  expect(s.error).toBe('boom')
  expect(s.streaming).toBe(false)
})

it('leave during a hanging attach that ends empty does not refetch', async () => {
  vi.mocked(api.getConversation).mockResolvedValue(conv('c1', true, [msg('a1', 'assistant', '', 'streaming')]))
  vi.mocked(api.attach).mockImplementation((_i, signal) => hanging([], signal))
  const s = useChatStore()
  await s.open('c1')
  const before = s.current
  s.leave()
  await new Promise((r) => setTimeout(r, 0))
  expect(api.getConversation).toHaveBeenCalledTimes(1)
  expect(s.current).toBe(before)
})

it('remove of the open conversation does not refetch or error', async () => {
  vi.mocked(api.getConversation).mockResolvedValue(conv('c1', true, [msg('a1', 'assistant', '', 'streaming')]))
  vi.mocked(api.attach).mockImplementation((_i, signal) => hanging([], signal))
  const s = useChatStore()
  await s.open('c1')
  await s.remove('c1')
  await new Promise((r) => setTimeout(r, 0))
  expect(api.getConversation).toHaveBeenCalledTimes(1)
  expect(s.current).toBeNull()
  expect(s.error).toBeNull()
})

it('open(null) while a load is pending clears loading', async () => {
  let resolve!: (c: never) => void
  vi.mocked(api.getConversation).mockReturnValue(new Promise((r) => (resolve = r)))
  const s = useChatStore()
  const opening = s.open('c1')
  expect(s.loading).toBe(true)
  await s.open(null)
  resolve(conv('c1'))
  await opening
  expect(s.loading).toBe(false)
  expect(s.current).toBeNull()
})

it('edit failing before start refetches so trimmed messages come back', async () => {
  const full = [msg('u1', 'user'), msg('a1', 'assistant')]
  vi.mocked(api.sendMessage).mockImplementation(() =>
    (async function* () {
      throw new ApiError('conflict', 409, null)
    })(),
  )
  vi.mocked(api.getConversation).mockResolvedValue(conv('c1', false, full))
  const s = useChatStore()
  s.current = conv('c1', false, [...full])
  await s.send('x', 'u1')
  expect(api.getConversation).toHaveBeenCalled()
  expect((s.current as Conversation | null)?.messages).toHaveLength(2)
  expect(s.error).toBe('conflict')
})
