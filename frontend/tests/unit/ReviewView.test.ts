import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getHousehold } from '@/api/household'
import { deleteInvoice, invoicePdf, listInvoices, validateInvoice, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import ReviewView from '@/views/ReviewView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/providers', () => ({ listProviders: vi.fn() }))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(),
  validateInvoice: vi.fn(),
  deleteInvoice: vi.fn(async () => {}),
  invoicePdf: vi.fn(),
}))

const readable: Invoice = {
  id: 'i1', status: 'to_validate', service_id: null, has_pdf: true, total: '114.98',
  due_on: '2026-11-01', issued_on: '2026-10-01', period_start: null, period_end: null,
  invoice_number: 'A-1', consumption_qty: '1240', consumption_unit: 'kWh', paid_at: null,
  paid: false, error: null, taxes: [],
  extraction: {
    raw: { taxes: [{ name: 'TPS', amount: '5.00' }] },
    candidates: {
      provider: { id: 'p1', name: 'Bell' },
      service: { id: 's1', name: 'Internet' },
      new_provider: null,
      new_service: null,
    },
  },
}

const failed: Invoice = {
  ...readable, id: 'i2', status: 'failed', total: null, due_on: null, issued_on: null,
  invoice_number: null, consumption_qty: null, consumption_unit: null, extraction: null,
  error: 'no readable text: a scanned PDF?',
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: ReviewView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(ReviewView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:pdf')
  URL.revokeObjectURL = vi.fn()
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'member', members: [] })
  vi.mocked(invoicePdf).mockResolvedValue(new Blob(['%PDF']))
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
  vi.mocked(listInvoices).mockResolvedValue([readable, failed])
})

it('opens the first invoice with the form filled from what the model read', async () => {
  const wrapper = await render()

  const value = (name: string) =>
    (wrapper.find(`[name="${name}"]`).element as HTMLInputElement | HTMLSelectElement).value
  expect(value('service_id')).toBe('s1')
  expect(value('total')).toBe('114.98')
  expect(value('due_on')).toBe('2026-11-01')
  expect(value('invoice_number')).toBe('A-1')
  expect(wrapper.findAll('[data-test="tax"]')).toHaveLength(1)
  expect(wrapper.find('iframe').attributes('src')).toBe('blob:pdf')
})

it('validates with the corrected values', async () => {
  vi.mocked(validateInvoice).mockResolvedValue({ ...readable, status: 'validated', service_id: 's1' })
  const wrapper = await render()

  await wrapper.find('input[name="total"]').setValue('120.00')
  await wrapper.find('form').trigger('submit')
  await flushPromises()

  // A type="number" input is cast to a number by v-model, so '120.00' becomes 120.
  expect(validateInvoice).toHaveBeenCalledWith('i1', {
    service_id: 's1',
    total: '120',
    due_on: '2026-11-01',
    issued_on: '2026-10-01',
    period_start: null,
    period_end: null,
    invoice_number: 'A-1',
    consumption_qty: '1240',
    consumption_unit: 'kWh',
    taxes: [{ name: 'TPS', amount: '5.00' }],
  })
})

it('moves on to the next invoice after validating one', async () => {
  vi.mocked(validateInvoice).mockResolvedValue({ ...readable, status: 'validated', service_id: 's1' })
  const wrapper = await render()

  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('no readable text')
})

it('lets a failed invoice be filled in by hand', async () => {
  const wrapper = await render()
  await wrapper.findAll('[data-test="queue-item"]')[1].trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('no readable text')
  expect((wrapper.find('input[name="total"]').element as HTMLInputElement).value).toBe('')
  expect(wrapper.find('form').exists()).toBe(true)
})

it('reports a duplicate number with the id it duplicates', async () => {
  vi.mocked(validateInvoice).mockRejectedValue(
    new ApiError('Conflict', 409, { detail: { message: 'Duplicate', existing_id: 'old-1' } }),
  )
  const wrapper = await render()

  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(wrapper.find('[data-test="duplicate"]').text()).toContain('old-1')
})

it('suggests creating a provider the model found but the household lacks', async () => {
  vi.mocked(listInvoices).mockResolvedValue([
    {
      ...readable,
      extraction: {
        raw: {},
        candidates: { provider: null, service: null, new_provider: 'Vidéotron', new_service: 'Internet' },
      },
    },
  ])

  const wrapper = await render()

  expect(wrapper.find('[data-test="new-provider"]').text()).toContain('Vidéotron')
})

it('shows a message when the PDF is gone', async () => {
  vi.mocked(invoicePdf).mockRejectedValue(new ApiError('The PDF is missing', 404, null))

  const wrapper = await render()

  expect(wrapper.find('iframe').exists()).toBe(false)
  expect(wrapper.text()).toContain('PDF missing')
})

it('deletes an invoice from the queue', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const wrapper = await render()

  await wrapper.find('button[data-test="delete"]').trigger('click')
  await flushPromises()

  expect(deleteInvoice).toHaveBeenCalledWith('i1')
})

it('is read-only for a viewer', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ id: 'h', name: 'M', role: 'viewer', members: [] })

  const wrapper = await render()

  expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="delete"]').exists()).toBe(false)
})

it('says so when nothing is waiting', async () => {
  vi.mocked(listInvoices).mockResolvedValue([])

  const wrapper = await render()

  expect(wrapper.text()).toContain('Nothing to review')
})
