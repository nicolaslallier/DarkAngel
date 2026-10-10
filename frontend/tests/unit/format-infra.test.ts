import { expect, it } from 'vitest'

import { age, bytes } from '@/format'

it('formats sizes', () => {
  expect(bytes(null)).toBe('—')
  expect(bytes(512)).toBe('512 B')
  expect(bytes(1536)).toBe('1.5 KB')
  expect(bytes(5 * 1024 ** 3)).toBe('5.0 GB')
})

it('formats ages in hours then days', () => {
  expect(age(null)).toBe('—')
  expect(age(0.4)).toBe('< 1 h')
  expect(age(5.2)).toBe('5 h')
  expect(age(49)).toBe('2 d')
})
