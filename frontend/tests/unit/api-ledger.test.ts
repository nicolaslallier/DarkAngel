import { beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiRequest, apiSend } from '@/api/client'
import { getUpcoming, getServiceCosts } from '@/api/costs'
import { createInvitation, deleteHousehold, setMemberRole } from '@/api/household'
import {
  createManualInvoice,
  invoicePdf,
  listInvoices,
  setPaid,
  uploadInvoices,
  validateInvoice,
} from '@/api/invoices'
import { createService, updateProvider } from '@/api/providers'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(async () => ({})),
  apiRequest: vi.fn(async () => ({ json: async () => ({}), blob: async () => new Blob(['pdf']) })),
  apiSend: vi.fn(async () => ({})),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

it('household calls send JSON and encode the member sub', async () => {
  await createInvitation('viewer')
  await setMemberRole('a/b', 'member')
  await deleteHousehold()

  expect(apiSend).toHaveBeenCalledWith('POST', '/household/invitations', { role: 'viewer' })
  expect(apiSend).toHaveBeenCalledWith('PATCH', '/household/members/a%2Fb', { role: 'member' })
  expect(apiRequest).toHaveBeenCalledWith('DELETE', '/household')
})

it('provider calls PATCH and POST JSON', async () => {
  await updateProvider('p1', { phone: '1' })
  await createService('p1', {
    name: 'Internet',
    category: 'internet',
    account_number: null,
    contract_start: null,
    contract_end: null,
    renewal_reminder_days: null,
    expected_monthly_cost: '79.99',
    auto_pay: true,
    alert_threshold_pct: 20,
  })

  expect(apiSend).toHaveBeenCalledWith('PATCH', '/providers/p1', { phone: '1' })
  expect(apiSend).toHaveBeenCalledWith(
    'POST',
    '/providers/p1/services',
    expect.objectContaining({ name: 'Internet', auto_pay: true }),
  )
})

it('uploadInvoices sends every file and only the targets that are set', async () => {
  const a = new File(['a'], 'a.pdf')
  const b = new File(['b'], 'b.pdf')

  await uploadInvoices([a, b], { service_id: 's1' })
  await uploadInvoices([a])

  const [first, second] = vi.mocked(apiRequest).mock.calls.map((call) => call[2] as FormData)
  expect(first.getAll('files')).toHaveLength(2)
  expect(first.get('service_id')).toBe('s1')
  expect(first.has('provider_id')).toBe(false)
  expect(second.has('service_id')).toBe(false)
  expect(vi.mocked(apiRequest).mock.calls[0].slice(0, 2)).toEqual(['POST', '/invoices'])
})

it('listInvoices puts only the params that are set in the query string', async () => {
  await listInvoices({ status: 'to_validate', service_id: undefined, unpaid: true })
  await listInvoices()

  expect(apiGet).toHaveBeenNthCalledWith(1, '/invoices?status=to_validate&unpaid=true')
  expect(apiGet).toHaveBeenNthCalledWith(2, '/invoices')
})

it('validate, manual entry and paid state post JSON', async () => {
  const fields = { service_id: 's1', total: '10.00', due_on: '2026-11-01', taxes: [] }
  await validateInvoice('i1', { ...fields, issued_on: null, period_start: null, period_end: null,
    invoice_number: null, consumption_qty: null, consumption_unit: null })
  await createManualInvoice({ ...fields, issued_on: null, period_start: null, period_end: null,
    invoice_number: null, consumption_qty: null, consumption_unit: null })
  await setPaid('i1', true)

  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/i1/validate', expect.any(Object))
  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/manual', expect.any(Object))
  expect(apiSend).toHaveBeenCalledWith('POST', '/invoices/i1/paid', { paid: true })
})

it('invoicePdf downloads the bytes through the authenticated client', async () => {
  const blob = await invoicePdf('i1')

  expect(apiRequest).toHaveBeenCalledWith('GET', '/invoices/i1/pdf')
  expect(blob).toBeInstanceOf(Blob)
})

it('cost calls hit the three read endpoints', async () => {
  await getUpcoming()
  await getServiceCosts('s1')

  expect(apiGet).toHaveBeenCalledWith('/upcoming')
  expect(apiGet).toHaveBeenCalledWith('/services/s1/costs')
})
