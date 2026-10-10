import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { joinHousehold } from '@/api/household'
import JoinView from '@/views/JoinView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ joinHousehold: vi.fn() }))

async function render(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/household/join', component: JoinView },
      { path: '/providers', component: { template: '<p>providers</p>' } },
    ],
  })
  await router.push(path)
  const wrapper = mount(JoinView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('does not join just by opening the link', async () => {
  const { wrapper } = await render('/household/join#token=tok123')

  expect(joinHousehold).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('You have been invited to join a household.')
})

it('joins with the decoded token on click and goes to the providers', async () => {
  vi.mocked(joinHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const { wrapper, router } = await render('/household/join#token=tok%20123')
  await wrapper.find('button[data-test="join"]').trigger('click')
  await flushPromises()

  expect(joinHousehold).toHaveBeenCalledWith('tok 123')
  expect(router.currentRoute.value.path).toBe('/providers')
})

it('says so when the link is invalid or expired', async () => {
  vi.mocked(joinHousehold).mockRejectedValue(new Error('Invitation invalid or expired'))

  const { wrapper, router } = await render('/household/join#token=old')
  await wrapper.find('button[data-test="join"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('Invitation invalid or expired')
  expect(router.currentRoute.value.path).toBe('/household/join')
})

it('has no button and no API call for a link with no token', async () => {
  const { wrapper } = await render('/household/join')

  expect(joinHousehold).not.toHaveBeenCalled()
  expect(wrapper.find('button[data-test="join"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('no invitation')
})

it('ignores a leftover ?token= query', async () => {
  const { wrapper } = await render('/household/join?token=tok123')

  expect(joinHousehold).not.toHaveBeenCalled()
  expect(wrapper.find('button[data-test="join"]').exists()).toBe(false)
})

it('scrubs the token from the URL once read, and still joins with it', async () => {
  vi.mocked(joinHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const { wrapper, router } = await render('/household/join#token=tok123')

  expect(router.currentRoute.value.fullPath).toBe('/household/join')
  await wrapper.find('button[data-test="join"]').trigger('click')
  await flushPromises()
  expect(joinHousehold).toHaveBeenCalledWith('tok123')
})
