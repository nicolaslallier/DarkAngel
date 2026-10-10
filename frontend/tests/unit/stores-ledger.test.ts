import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { getHousehold, joinHousehold } from '@/api/household'
import { listInvoices, validateInvoice, type Invoice } from '@/api/invoices'
import { listProviders } from '@/api/providers'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({
  getHousehold: vi.fn(),
  createHousehold: vi.fn(),
  deleteHousehold: vi.fn(),
  createInvitation: vi.fn(),
  joinHousehold: vi.fn(),
  setMemberRole: vi.fn(),
  removeMember: vi.fn(),
  leaveHousehold: vi.fn(),
}))
vi.mock('@/api/providers', () => ({
  listProviders: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  createService: vi.fn(),
  updateService: vi.fn(),
  deleteService: vi.fn(),
}))
vi.mock('@/api/invoices', () => ({
  listInvoices: vi.fn(async () => []),
  uploadInvoices: vi.fn(),
  validateInvoice: vi.fn(),
  createManualInvoice: vi.fn(),
  setPaid: vi.fn(),
  deleteInvoice: vi.fn(),
}))
vi.mock('@/api/costs', () => ({
  getUpcoming: vi.fn(async () => ({ invoices: [], renewals: [] })),
  getMonthlyCosts: vi.fn(async () => ({ months: [] })),
}))

const home = { id: 'h1', name: 'Maison', role: 'owner' as const, members: [] }

function invoice(overrides: Partial<Invoice>): Invoice {
  return {
    id: 'i1', status: 'to_validate', service_id: null, has_pdf: true, total: null, due_on: null,
    issued_on: null, period_start: null, period_end: null, invoice_number: null,
    consumption_qty: null, consumption_unit: null, paid_at: null, paid: false, error: null,
    extraction: null, taxes: [], ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('a person with no household is not an error', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))
  const store = useHouseholdStore()

  await store.load()

  expect(store.household).toBeNull()
  expect(store.error).toBeNull()
  expect(store.loaded).toBe(true)
})

it('any other failure is recorded', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('boom', 500, null))
  const store = useHouseholdStore()

  await store.load()

  expect(store.error).toBe('boom')
})

it('roles decide who can write', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...home, role: 'viewer' })
  const store = useHouseholdStore()

  await store.load()

  expect(store.canWrite).toBe(false)
  expect(store.isOwner).toBe(false)
})

it('joining stores the household it lands in', async () => {
  vi.mocked(joinHousehold).mockResolvedValue({ ...home, role: 'member' })
  const store = useHouseholdStore()

  await store.join('tok')

  expect(store.household?.role).toBe('member')
})

it('serviceOptions lists live services as "Provider — Service"', async () => {
  const service = (id: string, name: string, archived = false) => ({
    id, provider_id: 'p1', name, category: 'x', account_number: null, contract_start: null,
    contract_end: null, renewal_reminder_days: null, expected_monthly_cost: null, auto_pay: false,
    alert_threshold_pct: 20, archived,
  })
  vi.mocked(listProviders).mockResolvedValue([
    { id: 'p1', name: 'Bell', website: null, phone: null, email: null, notes: null,
      services: [service('s1', 'Internet'), service('s2', 'Old', true)] },
  ])
  const store = useProvidersStore()

  await store.load()

  expect(store.serviceOptions).toEqual([{ id: 's1', label: 'Bell — Internet' }])
})

it('toReview keeps everything that is not validated yet', async () => {
  vi.mocked(listInvoices).mockResolvedValue([
    invoice({ id: 'a', status: 'to_validate' }),
    invoice({ id: 'b', status: 'validated' }),
    invoice({ id: 'c', status: 'failed' }),
  ])
  const store = useInvoicesStore()

  await store.loadAll()

  expect(store.toReview.map((i) => i.id)).toEqual(['a', 'c'])
})

it('validating replaces the invoice in the list', async () => {
  vi.mocked(listInvoices).mockResolvedValue([invoice({ id: 'a' })])
  vi.mocked(validateInvoice).mockResolvedValue(invoice({ id: 'a', status: 'validated' }))
  const store = useInvoicesStore()
  await store.loadAll()

  await store.validate('a', {} as never)

  expect(store.toReview).toEqual([])
  expect(store.invoices[0].status).toBe('validated')
})

it('a duplicate is reported with the id of the invoice it duplicates', async () => {
  vi.mocked(listInvoices).mockResolvedValue([invoice({ id: 'a' })])
  vi.mocked(validateInvoice).mockRejectedValue(
    new ApiError('Conflict', 409, { detail: { message: 'Duplicate', existing_id: 'other' } }),
  )
  const store = useInvoicesStore()
  await store.loadAll()

  const result = await store.validate('a', {} as never)

  expect(result).toBeUndefined()
  expect(store.duplicateOf).toBe('other')
  expect(store.toReview).toHaveLength(1)
})
