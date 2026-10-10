import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getInfra } from '@/api/infra'
import { useInfraStore } from '@/stores/infra'

vi.mock('@/api/infra', () => ({ getInfra: vi.fn() }))

const NOW = new Date('2026-10-10T12:00:00Z')
const reading = (checked_at: string) => ({
  name: 'heaven', reachable: true, version: '2.21.0', environments: 1, stacks: 2,
  checked_at, error: null,
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => vi.useRealTimers())

it('load() stores both lists', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:58:00Z')], backups: [] })
  const store = useInfraStore()

  await store.load()

  expect(store.instances).toHaveLength(1)
  expect(store.error).toBeNull()
})

it('load() records the error', async () => {
  vi.mocked(getInfra).mockRejectedValue(new Error('GET /infra failed with 500'))
  const store = useInfraStore()

  await store.load()

  expect(store.error).toBe('GET /infra failed with 500')
  expect(store.loading).toBe(false)
})

it('collectorStale is false when nothing was collected yet', () => {
  expect(useInfraStore().collectorStale).toBe(false)
})

it('collectorStale is true when the newest reading is older than 15 minutes', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:40:00Z')], backups: [] })
  const store = useInfraStore()
  await store.load()

  expect(store.collectorStale).toBe(true)
})

it('collectorStale is false for a reading from two minutes ago', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:58:00Z')], backups: [] })
  const store = useInfraStore()
  await store.load()

  expect(store.collectorStale).toBe(false)
})
