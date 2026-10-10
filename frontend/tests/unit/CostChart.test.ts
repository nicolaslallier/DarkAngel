import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'

import CostChart from '@/components/CostChart.vue'

it('asks for more data below two points', () => {
  const wrapper = mount(CostChart, { props: { points: [{ label: '2026-01', value: 10 }] } })

  expect(wrapper.find('svg').exists()).toBe(false)
  expect(wrapper.text()).toContain('Not enough data')
})

it('draws one dot per point and marks the flagged ones', () => {
  const wrapper = mount(CostChart, {
    props: {
      points: [
        { label: '2026-01', value: 100 },
        { label: '2026-02', value: 100 },
        { label: '2026-03', value: 250, flagged: true },
      ],
    },
  })

  const dots = wrapper.findAll('circle')
  expect(dots).toHaveLength(3)
  expect(dots.map((d) => d.classes('flagged'))).toEqual([false, false, true])
})

it('keeps the highest point inside the drawing', () => {
  const wrapper = mount(CostChart, {
    props: { points: [{ label: 'a', value: 0 }, { label: 'b', value: 500 }] },
  })

  const ys = wrapper.findAll('circle').map((d) => Number(d.attributes('cy')))
  expect(Math.min(...ys)).toBeGreaterThanOrEqual(0)
  expect(Math.max(...ys)).toBeLessThanOrEqual(100)
})

it('draws two points that share a label without a key clash', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const wrapper = mount(CostChart, {
    props: {
      points: [
        { label: '2026-01', value: 100 },
        { label: '2026-01', value: 300, flagged: true },
      ],
    },
  })

  expect(wrapper.findAll('circle').map((d) => d.classes('flagged'))).toEqual([false, true])
  expect(warn).not.toHaveBeenCalled()
  warn.mockRestore()
})
