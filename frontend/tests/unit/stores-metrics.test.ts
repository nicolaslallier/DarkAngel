import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getPanels, getRange } from '@/api/metrics'
import { useMetricsStore } from '@/stores/metrics'

vi.mock('@/api/metrics', () => ({ getPanels: vi.fn(), getRange: vi.fn() }))

const PANELS = [
  { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' },
  { id: 'portainer-up', title: 'Up', row: 'portainer', unit: 'count' },
]
const result = (panel: string, range = '6h') => ({ panel, range, series: [{ label: panel, points: [[1, 2]] }] })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.mocked(getPanels).mockResolvedValue(PANELS as never)
  vi.mocked(getRange).mockImplementation(async (panel, range) => result(panel, range) as never)
})
afterEach(() => vi.useRealTimers())

it('load() fetches the catalog then every panel for the default range', async () => {
  const store = useMetricsStore()
  await store.load()

  expect(store.rows.map((r) => r.row)).toEqual(['host', 'portainer'])
  expect(getRange).toHaveBeenCalledWith('host-cpu', '6h')
  expect(store.state['host-cpu'].series).toHaveLength(1)
})

it('one failing panel does not touch the others', async () => {
  vi.mocked(getRange).mockImplementation(async (panel, range) => {
    if (panel === 'host-cpu') throw new Error('GET /metrics/range failed with 502')
    return result(panel, range) as never
  })
  const store = useMetricsStore()
  await store.load()

  expect(store.state['host-cpu'].error).toBe('GET /metrics/range failed with 502')
  expect(store.state['portainer-up'].error).toBeNull()
  expect(store.state['portainer-up'].series).toHaveLength(1)
})

it('setRange refetches every panel for the new range', async () => {
  const store = useMetricsStore()
  await store.load()
  vi.mocked(getRange).mockClear()

  store.setRange('24h')
  await vi.waitFor(() => expect(getRange).toHaveBeenCalledTimes(2))

  expect(getRange).toHaveBeenCalledWith('host-cpu', '24h')
})

it('a slow answer for an old range never overwrites the new range', async () => {
  let release!: (v: unknown) => void
  vi.mocked(getRange).mockImplementationOnce(() => new Promise((r) => (release = r)) as never)
  const store = useMetricsStore()
  const loading = store.load()
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))

  store.setRange('1h')
  await vi.waitFor(() => expect(store.state['host-cpu'].series).toHaveLength(1))
  release({ panel: 'host-cpu', range: '6h', series: [{ label: 'stale', points: [[9, 9]] }] })
  await loading

  expect(store.state['host-cpu'].series[0].label).toBe('host-cpu')
})

it('auto-refresh refetches on the interval, skips hidden tabs and stops on stop()', async () => {
  const store = useMetricsStore()
  await store.load()
  vi.mocked(getRange).mockClear()

  store.setRefresh(30)
  await vi.advanceTimersByTimeAsync(30_000)
  expect(getRange).toHaveBeenCalledTimes(2)

  Object.defineProperty(document, 'hidden', { value: true, configurable: true })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(getRange).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: false, configurable: true })

  store.stop()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(getRange).toHaveBeenCalledTimes(2)
})
