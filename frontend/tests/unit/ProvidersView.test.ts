import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming } from '@/api/costs'
import { getHousehold } from '@/api/household'
import { listInvoices, uploadInvoices, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import ProvidersView from '@/views/ProvidersView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({ listProviders: vi.fn(), createProvider: vi.fn() }))
vi.mock('@/api/invoices', () => ({ listInvoices: vi.fn(), uploadInvoices: vi.fn() }))
vi.mock('@/api/costs', () => ({ getUpcoming: vi.fn(), getMonthlyCosts: vi.fn() }))

const home = { id: 'h', name: 'Maison', role: 'owner' as const, members: [] }

function pending(id: string): Invoice {
  return {
    id, status: 'to_validate', service_id: null, has_pdf: true, total: null, due_on: null,
    issued_on: null, period_start: null, period_end: null, invoice_number: null,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [],
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ProvidersView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(ProvidersView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHousehold).mockResolvedValue(home)
  vi.mocked(listProviders).mockResolvedValue([
    {
      id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null,
      services: [
        { id: 's1', provider_id: 'p1', name: 'Internet', category: 'internet', account_number: null,
          contract_start: null, contract_end: null, renewal_reminder_days: null,
          expected_monthly_cost: null, auto_pay: false, alert_threshold_pct: 20, archived: false },
      ],
    },
  ])
  vi.mocked(listInvoices).mockResolvedValue([pending('a'), pending('b')])
  vi.mocked(getMonthlyCosts).mockResolvedValue({ months: [] })
  vi.mocked(getUpcoming).mockResolvedValue({
    invoices: [
      { invoice_id: 'i1', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-10-01', overdue: true },
      { invoice_id: 'i2', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-11-01', overdue: false },
    ],
    renewals: [
      { service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        contract_end: '2026-10-25', days_left: 15 },
    ],
  })
})

it('points a person without a household to the household page', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))

  const wrapper = await render()

  expect(wrapper.find('a[href="/household"]').exists()).toBe(true)
  expect(listProviders).not.toHaveBeenCalled()
})

it('lists the providers with a link to each service', async () => {
  const wrapper = await render()

  expect(wrapper.find('a[href="/providers/p1"]').text()).toBe('Bell')
  expect(wrapper.find('[data-test="provider-services"] a[href="/services/s1"]').text()).toBe('Internet')
})

it('shows the upcoming dues with the overdue ones marked, and the renewals', async () => {
  const wrapper = await render()

  const dues = wrapper.findAll('[data-test="due"]')
  expect(dues.map((d) => d.classes('overdue'))).toEqual([true, false])
  expect(wrapper.find('[data-test="renewal"]').text()).toContain('15 days')
})

it('counts the invoices waiting for review and links to the queue', async () => {
  const wrapper = await render()

  const link = wrapper.find('a[href="/invoices/review"]')
  expect(link.text()).toContain('2 invoices to review')
})

it('uploads the chosen PDFs', async () => {
  vi.mocked(uploadInvoices).mockResolvedValue([pending('c')])
  const wrapper = await render()
  const input = wrapper.find('input[type="file"]')
  const file = new File(['%PDF'], 'facture.pdf', { type: 'application/pdf' })

  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(uploadInvoices).toHaveBeenCalledWith([file], undefined)
})

it('hides the upload for a read-only member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...home, role: 'viewer' })

  const wrapper = await render()

  expect(wrapper.find('input[type="file"]').exists()).toBe(false)
})
