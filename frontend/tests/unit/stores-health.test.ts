import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { fetchHealth } from '@/api/health'
import { useHealthStore } from '@/stores/health'

vi.mock('@/api/health', () => ({ fetchHealth: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('load() stores the health payload', async () => {
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok', version: '0.1.0' })
  const store = useHealthStore()

  await store.load()

  expect(store.health).toEqual({ status: 'ok', version: '0.1.0' })
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('load() records the error and leaves health untouched', async () => {
  vi.mocked(fetchHealth).mockRejectedValue(new Error('GET /health failed with 502'))
  const store = useHealthStore()

  await store.load()

  expect(store.health).toBeNull()
  expect(store.error).toBe('GET /health failed with 502')
  expect(store.loading).toBe(false)
})
