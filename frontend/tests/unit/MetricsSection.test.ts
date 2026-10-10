import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { getPanels, getRange } from '@/api/metrics'
import MetricsSection from '@/components/MetricsSection.vue'
import { useMetricsStore } from '@/stores/metrics'

vi.mock('@/api/metrics', () => ({ getPanels: vi.fn(), getRange: vi.fn() }))
vi.mock('@/components/MetricPanel.vue', () => ({
  default: { props: ['panel'], template: '<div data-test="panel">{{ panel.title }}</div>' },
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(getPanels).mockResolvedValue([
    { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' },
    { id: 'portainer-up', title: 'Up', row: 'portainer', unit: 'count' },
  ])
  vi.mocked(getRange).mockResolvedValue({ panel: 'x', range: '6h', series: [] })
})

it('renders one block per row with a panel per catalog entry', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()

  expect(wrapper.findAll('[data-test="panel"]').map((p) => p.text())).toEqual(['CPU', 'Up'])
  expect(wrapper.findAll('h3').map((h) => h.text())).toEqual(['Host', 'Portainer'])
})

it('the range buttons and refresh select drive the store', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()
  const store = useMetricsStore()

  await wrapper.findAll('[data-test="range"]')[2].trigger('click') // 24h
  expect(store.range).toBe('24h')

  await wrapper.find('[data-test="refresh"]').setValue('30')
  expect(store.refreshSeconds).toBe(30)
})

it('stops the refresh timer when it unmounts', async () => {
  const wrapper = mount(MetricsSection)
  await flushPromises()
  const stop = vi.spyOn(useMetricsStore(), 'stop')

  wrapper.unmount()

  expect(stop).toHaveBeenCalled()
})

it('re-arms the refresh timer when it mounts again with a saved interval', async () => {
  vi.useFakeTimers()
  try {
    const first = mount(MetricsSection)
    await flushPromises()
    useMetricsStore().setRefresh(30)
    first.unmount()
    mount(MetricsSection)
    await flushPromises()
    vi.mocked(getRange).mockClear()

    await vi.advanceTimersByTimeAsync(30_000)

    expect(getRange).toHaveBeenCalled()
  } finally {
    vi.useRealTimers()
  }
})
