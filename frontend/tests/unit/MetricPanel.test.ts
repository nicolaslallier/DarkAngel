import { mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'

import MetricPanel from '@/components/MetricPanel.vue'

const ctor = vi.fn()
const destroy = vi.fn()
const setData = vi.fn()
vi.mock('uplot', () => ({
  default: class {
    constructor(...args: unknown[]) {
      ctor(...args)
    }
    destroy = destroy
    setData = setData
    setSize = vi.fn()
  },
}))
vi.mock('uplot/dist/uPlot.min.css', () => ({}))

const panel = { id: 'host-cpu', title: 'CPU', row: 'host', unit: 'percent' } as const
const series = [{ label: 'CPU', points: [[1, 2], [2, 3]] as [number, number][] }]

beforeEach(() => vi.clearAllMocks())

it('draws a graph when there is data', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series, loading: false, error: null } })

  expect(wrapper.text()).toContain('CPU')
  expect(ctor).toHaveBeenCalledOnce()
  expect(wrapper.find('[data-test="no-data"]').exists()).toBe(false)
})

it('says "No data" for an empty series list and draws nothing', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series: [], loading: false, error: null } })

  expect(wrapper.find('[data-test="no-data"]').exists()).toBe(true)
  expect(ctor).not.toHaveBeenCalled()
})

it('shows the error inline', () => {
  const wrapper = mount(MetricPanel, { props: { panel, series: [], loading: false, error: 'boom' } })

  expect(wrapper.find('[data-test="panel-error"]').text()).toBe('boom')
})

it('updates in place when data changes and destroys on unmount', async () => {
  const wrapper = mount(MetricPanel, { props: { panel, series, loading: false, error: null } })

  await wrapper.setProps({ series: [{ label: 'CPU', points: [[3, 4]] }] })
  expect(setData).toHaveBeenCalled()

  wrapper.unmount()
  expect(destroy).toHaveBeenCalled()
})
