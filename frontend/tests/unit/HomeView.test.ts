import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming } from '@/api/costs'
import { fetchHealth } from '@/api/health'
import { getHousehold } from '@/api/household'
import { getInfra } from '@/api/infra'
import { listInvoices } from '@/api/invoices'
import { fetchMe } from '@/api/me'
import HomeView from '@/views/HomeView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/health', () => ({ fetchHealth: vi.fn() }))
vi.mock('@/api/me', () => ({ fetchMe: vi.fn() }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/infra', () => ({ getInfra: vi.fn() }))
vi.mock('@/api/invoices', () => ({ listInvoices: vi.fn() }))
vi.mock('@/api/costs', () => ({ getUpcoming: vi.fn(), getMonthlyCosts: vi.fn() }))

const NOW = new Date('2026-10-10T12:00:00Z')
const home = { id: 'h', name: 'Maison', role: 'owner' as const, members: [] }
const fresh = '2026-10-10T11:58:00Z'

function infraBody() {
  return {
    instances: [
      { name: 'heaven', reachable: true, version: '2.21.0', environments: 2, stacks: 7, checked_at: fresh, error: null },
      { name: 'infra', reachable: false, version: null, environments: null, stacks: null, checked_at: fresh, error: 'ConnectError: refused' },
    ],
    backups: [
      { instance: 'heaven', last_backup_at: '2026-10-10T07:00:00Z', size_bytes: 1536, age_hours: 5, stale: false, checked_at: fresh },
      { instance: 'infra', last_backup_at: null, size_bytes: null, age_hours: null, stale: true, checked_at: fresh },
    ],
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: HomeView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(HomeView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok', version: '0.1.0' })
  vi.mocked(fetchMe).mockResolvedValue({ sub: 'u1', username: 'nicolas' } as never)
  vi.mocked(getHousehold).mockResolvedValue(home)
  vi.mocked(getInfra).mockResolvedValue(infraBody())
  vi.mocked(listInvoices).mockResolvedValue([])
  vi.mocked(getMonthlyCosts).mockResolvedValue({ months: [] })
  vi.mocked(getUpcoming).mockResolvedValue({
    invoices: [
      { invoice_id: 'i1', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-10-01', overdue: true },
    ],
    renewals: [],
  })
})
afterEach(() => vi.useRealTimers())

it('shows a row per instance and per backup, flagging what is wrong', async () => {
  const wrapper = await render()

  const instances = wrapper.findAll('[data-test="instance"]')
  expect(instances.map((i) => i.classes('bad'))).toEqual([false, true])
  expect(instances[0].text()).toContain('2.21.0')
  expect(instances[1].text()).toContain('ConnectError')

  const backups = wrapper.findAll('[data-test="backup"]')
  expect(backups.map((b) => b.classes('bad'))).toEqual([false, true])
  expect(backups[0].text()).toContain('1.5 KB')
  expect(backups[0].text()).not.toContain('Stale')
  expect(backups[1].text()).toContain('No backup')
})

it('shows the overdue invoice', async () => {
  const wrapper = await render()

  expect(wrapper.find('[data-test="due"]').classes('bad')).toBe(true)
  expect(wrapper.find('[data-test="due"]').text()).toContain('Overdue')
})

it('points a person without a household to the household page and asks nothing else', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))

  const wrapper = await render()

  expect(wrapper.find('a[href="/household"]').exists()).toBe(true)
  expect(getInfra).not.toHaveBeenCalled()
  expect(getUpcoming).not.toHaveBeenCalled()
})

it('keeps the other cards when /api/infra fails', async () => {
  vi.mocked(getInfra).mockRejectedValue(new Error('GET /infra failed with 500'))

  const wrapper = await render()

  expect(wrapper.find('[data-test="infra-error"]').text()).toContain('500')
  expect(wrapper.find('[data-test="due"]').exists()).toBe(true)
})

it('warns when the collector stopped', async () => {
  const old = infraBody()
  old.instances[0].checked_at = '2026-10-10T11:00:00Z'
  old.instances[1].checked_at = '2026-10-10T11:00:00Z'
  old.backups.forEach((b) => (b.checked_at = '2026-10-10T11:00:00Z'))
  vi.mocked(getInfra).mockResolvedValue(old)

  const wrapper = await render()

  expect(wrapper.findAll('[data-test="collector-stale"]')).toHaveLength(1)
  expect(wrapper.find('.grid [data-test="collector-stale"]').exists()).toBe(false)
})

it('labels a stale backup that has a date', async () => {
  const body = infraBody()
  body.backups[0].stale = true
  vi.mocked(getInfra).mockResolvedValue(body)

  const wrapper = await render()

  expect(wrapper.findAll('[data-test="backup"]')[0].text()).toContain('Stale')
})

it('shows Loading… and no empty message while requests are pending', async () => {
  vi.mocked(getInfra).mockReturnValue(new Promise(() => {}))
  vi.mocked(getUpcoming).mockReturnValue(new Promise(() => {}))

  const wrapper = await render()

  expect(wrapper.text()).toContain('Loading…')
  expect(wrapper.text()).not.toContain('Nothing collected yet')
  expect(wrapper.text()).not.toContain('Nothing due')
})

it('shows no empty message next to an error', async () => {
  vi.mocked(getInfra).mockRejectedValue(new Error('boom'))
  vi.mocked(getUpcoming).mockRejectedValue(new Error('bang'))

  const wrapper = await render()

  expect(wrapper.text()).toContain('boom')
  expect(wrapper.text()).toContain('bang')
  expect(wrapper.text()).not.toContain('Nothing collected yet')
  expect(wrapper.text()).not.toContain('Nothing due')
})

it('says nothing was collected yet when both lists are empty', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [], backups: [] })

  const wrapper = await render()

  expect(wrapper.text()).toContain('Nothing collected yet')
})
