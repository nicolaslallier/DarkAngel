import { expect, it } from 'vitest'

import { align, formatValue } from '@/metrics'

it('align merges timestamps and pads gaps with null', () => {
  const out = align([
    { label: 'a', points: [[1, 10], [3, 30]] },
    { label: 'b', points: [[2, 200], [3, 300]] },
  ])
  expect(out).toEqual([[1, 2, 3], [10, null, 30], [null, 200, 300]])
})

it('align of nothing is just an empty time axis', () => {
  expect(align([])).toEqual([[]])
})

it('formatValue renders each unit', () => {
  expect(formatValue('percent', 12.345)).toBe('12.3%')
  expect(formatValue('bytes', 2048)).toBe('2.0 KB')
  expect(formatValue('bytes_per_s', 2048)).toBe('2.0 KB/s')
  expect(formatValue('count', 7)).toBe('7')
})
