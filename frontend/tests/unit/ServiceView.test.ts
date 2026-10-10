import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { getServiceCosts } from '@/api/costs'
import { getHousehold } from '@/api/household'
import { createManualInvoice, listInvoices, setPaid, type Invoice } from '@/api/invoices'
import { getService } from '@/api/providers'
import ServiceView from '@/views/ServiceView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({
  getService: vi.fn(),
  listProviders: vi.fn(async () => []),
  updateService: vi.fn(),
  deleteService: vi.fn(),
}))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(),
  createManualInvoice: vi.fn(),
  setPaid: vi.fn(),
  deleteInvoice: vi.fn(),
  uploadInvoices: vi.fn(),
}))
vi.mock('@/api/costs', () => ({ getServiceCosts: vi.fn() }))

const service = {
  id: 's1', provider_id: 'p1', name: 'Internet', category: 'internet', account_number: null,
  contract_start: null, contract_end: null, renewal_reminder_days: null,
  expected_monthly_cost: '79.99', auto_pay: false, alert_threshold_pct: 20, archived: false,
}

function invoice(id: string, overrides: Partial<Invoice> = {}): Invoice {
  return {
    id, status: 'validated', service_id: 's1', has_pdf: false, total: '100.00', due_on: '2026-11-01',
    issued_on: '2026-10-01', period_start: null, period_end: null, invoice_number: id,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [], ...overrides,
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/services/:id', component: ServiceView }],
  })
  await router.push('/services/s1')
  const wrapper = mount(ServiceView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'member', members: [] })
  vi.mocked(getService).mockResolvedValue(service)
  vi.mocked(listInvoices).mockResolvedValue([invoice('A-1'), invoice('A-2', { paid: true })])
  vi.mocked(getServiceCosts).mockResolvedValue({
    expected_monthly_cost: '79.99',
    threshold_pct: 20,
    points: [
      { invoice_id: 'A-1', month: '2026-09', total: '100.00', reference: '79.99', flagged: true },
      { invoice_id: 'A-2', month: '2026-10', total: '80.00', reference: '90.00', flagged: false },
    ],
  })
})

it('lists the invoices of the service and marks the ones above the alert threshold', async () => {
  const wrapper = await render()

  const rows = wrapper.findAll('tr[data-test="invoice"]')
  expect(rows).toHaveLength(2)
  expect(rows[0].find('[data-test="flag"]').exists()).toBe(true)
  expect(rows[1].find('[data-test="flag"]').exists()).toBe(false)
})

it('marks an unpaid invoice paid', async () => {
  vi.mocked(setPaid).mockResolvedValue(invoice('A-1', { paid: true }))
  const wrapper = await render()

  await wrapper.find('button[data-test="toggle-paid"]').trigger('click')
  await flushPromises()

  expect(setPaid).toHaveBeenCalledWith('A-1', true)
})

it('adds an invoice by hand for this service', async () => {
  vi.mocked(createManualInvoice).mockResolvedValue(invoice('A-3'))
  const wrapper = await render()

  await wrapper.find('input[name="manual-total"]').setValue('55.10')
  await wrapper.find('input[name="manual-due"]').setValue('2026-12-01')
  await wrapper.find('form[data-test="manual"]').trigger('submit')
  await flushPromises()

  expect(createManualInvoice).toHaveBeenCalledWith(
    expect.objectContaining({ service_id: 's1', total: '55.1', due_on: '2026-12-01', taxes: [] }),
  )
})

it('hides every write control from a read-only member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const wrapper = await render()

  expect(wrapper.find('form[data-test="manual"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="toggle-paid"]').exists()).toBe(false)
  expect(wrapper.find('input[type="file"]').exists()).toBe(false)
})
