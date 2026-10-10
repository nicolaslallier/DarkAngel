import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getHousehold } from '@/api/household'
import { uploadInvoices } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import ProviderView from '@/views/ProviderView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({ listProviders: vi.fn() }))
vi.mock('@/api/invoices', () => ({ uploadInvoices: vi.fn() }))

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/providers/:id', component: ProviderView },
      { path: '/:rest(.*)', component: { template: '<p/>' } },
    ],
  })
  await router.push('/providers/p1')
  const wrapper = mount(ProviderView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'member', members: [] })
  vi.mocked(listProviders).mockResolvedValue([
    { id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null, services: [] },
  ])
})

it('shows why the household could not be loaded', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new Error('household boom'))
  const wrapper = await render()

  expect(wrapper.find('[role="alert"]').text()).toContain('household boom')
})

it('shows an upload error', async () => {
  vi.mocked(uploadInvoices).mockRejectedValue(new Error('upload failed'))
  const wrapper = await render()
  const input = wrapper.find('input[type="file"]')
  const file = new File(['%PDF'], 'f.pdf', { type: 'application/pdf' })

  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(wrapper.find('[role="alert"]').text()).toContain('upload failed')
})

it('points a person without a household to the household page', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))

  const wrapper = await render()

  expect(wrapper.find('a[href="/household"]').exists()).toBe(true)
  expect(wrapper.text()).not.toContain('No such provider.')
})

it('says there is no such provider once loaded', async () => {
  vi.mocked(listProviders).mockResolvedValue([])

  expect((await render()).text()).toContain('No such provider.')
})
