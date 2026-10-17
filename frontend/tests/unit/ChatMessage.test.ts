import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import type { ChatMessage as Msg } from '@/api/chat'
import ChatMessage from '@/components/chat/ChatMessage.vue'

const msg = (o: Partial<Msg> = {}): Msg => ({
  id: 'm1',
  role: 'assistant',
  content: 'hello **world**',
  reasoning: null,
  model: 'llama',
  status: 'complete',
  error: null,
  created_at: '2026-10-17T10:00:00Z',
  ...o,
})
const render = (message: Msg, last = true, streaming = false) =>
  mount(ChatMessage, { props: { message, last, streaming } })

it('renders markdown and the model label', () => {
  const w = render(msg())
  expect(w.find('strong').text()).toBe('world')
  expect(w.text()).toContain('llama')
})

it('shows No answer for an empty complete reply', () => {
  expect(render(msg({ content: '' })).text()).toContain('No answer')
})

it('renders thinking open while waiting for content and collapsed once content exists', async () => {
  const w = render(msg({ content: '', reasoning: 'hmm', status: 'streaming' }), true, true)
  const d = w.find('details').element as HTMLDetailsElement
  expect(d.open).toBe(true)
  await w.setProps({ message: msg({ content: 'answer', reasoning: 'hmm', status: 'streaming' }) })
  expect((w.find('details').element as HTMLDetailsElement).open).toBe(false)
  expect(w.text()).toContain('Thinking')
})

it('shows Retry on error and emits regenerate', async () => {
  const w = render(msg({ content: '', status: 'error', error: 'boom' }))
  expect(w.text()).toContain('boom')
  await w.findAll('button').find((b) => b.text() === 'Retry')!.trigger('click')
  expect(w.emitted('regenerate')).toHaveLength(1)
})

it('keeps the error on an older failed message but offers Retry only on the last, idle', () => {
  const retry = (w: ReturnType<typeof render>) => w.findAll('button').find((b) => b.text() === 'Retry')
  const failed = msg({ content: '', status: 'error', error: 'boom' })
  const older = render(failed, false)
  expect(older.text()).toContain('boom')
  expect(retry(older)).toBeUndefined()
  expect(retry(render(failed, true, true))!.attributes('disabled')).toBeDefined()
})

it('offers Edit on user messages and emits the new content', async () => {
  const w = render(msg({ role: 'user', content: 'hi' }), false)
  await w.findAll('button').find((b) => b.text() === 'Edit')!.trigger('click')
  await w.find('textarea').setValue('changed')
  await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
  expect(w.emitted('edit')![0]).toEqual(['changed'])
})

it('offers Regenerate only on the last assistant message', () => {
  const has = (w: ReturnType<typeof render>) => w.findAll('button').some((b) => b.text() === 'Regenerate')
  expect(has(render(msg(), true))).toBe(true)
  expect(has(render(msg(), false))).toBe(false)
})

it('adds a copy button to code blocks', () => {
  const w = render(msg({ content: '```js\nlet a = 1\n```' }))
  expect(w.find('pre button').exists()).toBe(true)
})

it('shows Stopped for a stopped reply with no content', () => {
  const w = render(msg({ content: '', status: 'stopped' }))
  expect(w.text()).toContain('Stopped')
})
