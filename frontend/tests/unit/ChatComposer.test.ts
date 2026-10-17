import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import ChatComposer from '@/components/chat/ChatComposer.vue'

const models = [
  { id: 'a', loaded: true, context_length: 1 },
  { id: 'b', loaded: false, context_length: 1 },
]
const render = (streaming = false) =>
  mount(ChatComposer, { props: { streaming, models, model: 'a' } })

it('sends on Enter, not on Shift+Enter, not when blank', async () => {
  const w = render()
  const ta = w.find('textarea')
  await ta.setValue('   ')
  await ta.trigger('keydown', { key: 'Enter' })
  expect(w.emitted('send')).toBeUndefined()

  await ta.setValue('hi')
  await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
  expect(w.emitted('send')).toBeUndefined()

  await ta.trigger('keydown', { key: 'Enter' })
  expect(w.emitted('send')![0]).toEqual(['hi'])
  expect((ta.element as HTMLTextAreaElement).value).toBe('')
})

it('shows Stop while streaming and emits stop', async () => {
  const w = render(true)
  const stop = w.findAll('button').find((b) => b.text().includes('Stop'))!
  await stop.trigger('click')
  expect(w.emitted('stop')).toHaveLength(1)
  expect(w.findAll('button').some((b) => b.text() === 'Send')).toBe(false)
})

it('lists models with loaded badges and emits select', async () => {
  const w = render()
  await w.find('.model-toggle').trigger('click')
  expect(w.text()).toContain('not loaded')
  await w.findAll('li button').find((b) => b.text().startsWith('b'))!.trigger('click')
  expect(w.emitted('select')![0]).toEqual(['b'])
})

it('does not send on Enter while streaming, and keeps the text', async () => {
  const w = render(true)
  const ta = w.find('textarea')
  await ta.setValue('next')
  await ta.trigger('keydown', { key: 'Enter' })
  expect(w.emitted('send')).toBeUndefined()
  expect((ta.element as HTMLTextAreaElement).value).toBe('next')
})
