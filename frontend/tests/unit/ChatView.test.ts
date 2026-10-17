import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as api from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import ChatView from '@/views/ChatView.vue'

vi.mock('@/api/chat', () => ({
  listModels: vi.fn(async () => [{ id: 'm', loaded: true, context_length: 1 }]),
  listConversations: vi.fn(async () => []),
  getConversation: vi.fn(async () => ({ id: 'abc', title: 'T', streaming: false, messages: [] })),
  sendMessage: vi.fn(),
  regenerate: vi.fn(),
  attach: vi.fn(),
  stopReply: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))

async function render(path = '/chat') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/chat/:id?', name: 'chat', component: ChatView }],
  })
  await router.push(path)
  const wrapper = mount(ChatView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => vi.clearAllMocks())

it('opens the route conversation and leaves on unmount', async () => {
  const { wrapper } = await render('/chat/abc')
  expect(api.listModels).toHaveBeenCalled()
  expect(api.listConversations).toHaveBeenCalled()
  expect(api.getConversation).toHaveBeenCalledWith('abc')
  expect(wrapper.text()).toContain('T')
  wrapper.unmount() // leave() must not throw and aborts any stream
})

it('replaces the URL after the first send', async () => {
  vi.mocked(api.sendMessage).mockImplementation(async function* () {
    yield {
      event: 'start',
      data: {
        conversation: { id: 'new-id', title: 'New chat' },
        user_message: null,
        assistant_message_id: 'a1',
      },
    }
  } as unknown as typeof api.sendMessage)
  const { wrapper, router } = await render('/chat')
  await wrapper.find('textarea').setValue('hello')
  await wrapper.find('textarea').trigger('keydown', { key: 'Enter' })
  await flushPromises()
  expect(api.sendMessage).toHaveBeenCalledWith('new', expect.anything(), expect.anything())
  expect(router.currentRoute.value.params.id).toBe('new-id')
  expect(api.getConversation).not.toHaveBeenCalled() // the replace must not re-open
})

it('returns to /chat after deleting the open conversation', async () => {
  const { wrapper, router } = await render('/chat/abc')
  wrapper.findComponent({ name: 'ChatSidebar' }).vm.$emit('remove', 'abc')
  await flushPromises()
  expect(api.deleteConversation).toHaveBeenCalledWith('abc')
  expect(router.currentRoute.value.params.id).toBeUndefined()
})

it('opens A again when navigating back while B is still loading', async () => {
  const conv = (id: string) => ({ id, title: id, streaming: false, messages: [] })
  const { router } = await render('/chat/abc')
  let release!: (c: ReturnType<typeof conv>) => void
  vi.mocked(api.getConversation).mockImplementationOnce(() => new Promise((r) => (release = r)))
  await router.push('/chat/b')
  await flushPromises()
  vi.mocked(api.getConversation).mockResolvedValueOnce(conv('abc'))
  await router.push('/chat/abc')
  await flushPromises()
  release(conv('b'))
  await flushPromises()
  expect(api.getConversation).toHaveBeenLastCalledWith('abc')
})

it('scrolls the message list to the bottom when a conversation opens', async () => {
  const spy = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(900)
  vi.mocked(api.getConversation).mockResolvedValueOnce({
    id: 'abc',
    title: 'T',
    streaming: false,
    messages: [{ id: 'u1', role: 'user', content: 'hi', reasoning: null, model: 'm', status: 'complete', error: null, created_at: 'x' }],
  })
  const { wrapper } = await render('/chat/abc')
  expect((wrapper.find('.messages').element as HTMLElement).scrollTop).toBe(900)
  spy.mockRestore()
})

it('follows streaming output only while the user stays near the bottom', async () => {
  const spy = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(900)
  const msg = { id: 'a1', role: 'assistant', content: 'a', reasoning: null, model: 'm', status: 'streaming', error: null, created_at: 'x' } as const
  vi.mocked(api.getConversation).mockResolvedValueOnce({ id: 'abc', title: 'T', streaming: false, messages: [{ ...msg }] })
  const { wrapper } = await render('/chat/abc')
  const el = wrapper.find('.messages').element as HTMLElement
  const store = useChatStore()

  el.scrollTop = 100 // the user scrolled up
  await wrapper.find('.messages').trigger('scroll')
  store.current!.messages[0].content += 'b'
  await flushPromises()
  expect(el.scrollTop).toBe(100) // not yanked

  el.scrollTop = 900 // back at the bottom
  await wrapper.find('.messages').trigger('scroll')
  spy.mockReturnValue(1200)
  store.current!.messages[0].content += 'c'
  await flushPromises()
  expect(el.scrollTop).toBe(1200)
  spy.mockRestore()
})
