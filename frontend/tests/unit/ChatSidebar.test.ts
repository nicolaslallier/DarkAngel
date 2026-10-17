import { mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'

import ChatSidebar from '@/components/chat/ChatSidebar.vue'

const items = [{ id: 'c1', title: 'First', updated_at: new Date().toISOString(), streaming: false }]
const render = (open = true) => mount(ChatSidebar, { props: { items, currentId: null, open } })
const btn = (w: ReturnType<typeof render>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!

afterEach(() => vi.useRealTimers())

it('close button, backdrop and Escape emit close', async () => {
  const w = render()
  await w.find('button[aria-label="Close conversations"]').trigger('click')
  await w.find('[data-test=backdrop]').trigger('click')
  await w.find('aside').trigger('keydown', { key: 'Escape' })
  expect(w.emitted('close')).toHaveLength(3)
})

async function startRename(w: ReturnType<typeof render>) {
  await w.find('.more').trigger('click')
  await btn(w, 'Rename').trigger('click')
  const input = w.find('input[aria-label="Conversation title"]')
  await input.setValue('Renamed')
  return input
}

it('Enter renames once even though the blur follows', async () => {
  const w = render()
  const input = await startRename(w)
  await input.trigger('keydown', { key: 'Enter' })
  await input.trigger('blur')
  expect(w.emitted('rename')).toEqual([['c1', 'Renamed']])
})

it('Esc cancels the rename without saving', async () => {
  const w = render()
  const input = await startRename(w)
  await input.trigger('keydown', { key: 'Escape' })
  await input.trigger('blur')
  expect(w.emitted('rename')).toBeUndefined()
  expect(w.emitted('close')).toBeUndefined()
})

it('inline delete: Yes emits remove, No does not', async () => {
  const w = render()
  await w.find('.more').trigger('click')
  await btn(w, 'Delete').trigger('click')
  await btn(w, 'No').trigger('click')
  expect(w.emitted('remove')).toBeUndefined()
  await w.find('.more').trigger('click')
  await btn(w, 'Delete').trigger('click')
  await btn(w, 'Yes').trigger('click')
  expect(w.emitted('remove')).toEqual([['c1']])
})

it('debounces the search by 300 ms', async () => {
  vi.useFakeTimers()
  const w = render()
  const input = w.find('input[type=search]')
  await input.setValue('a')
  await input.setValue('ab')
  vi.advanceTimersByTime(299)
  expect(w.emitted('search')).toBeUndefined()
  vi.advanceTimersByTime(1)
  expect(w.emitted('search')).toEqual([['ab']])
})
