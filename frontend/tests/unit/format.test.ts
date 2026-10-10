import { expect, it } from 'vitest'

import { blankToNull, money } from '@/format'

it('money formats a decimal string as Canadian dollars', () => {
  expect(money('114.98').replace(/\s/g, ' ')).toContain('114,98')
  expect(money('114.98')).toContain('$')
})

it('money shows a dash for nothing', () => {
  expect(money(null)).toBe('—')
  expect(money('')).toBe('—')
  expect(money(undefined)).toBe('—')
})

it('blankToNull trims and turns empty into null, for text and numbers alike', () => {
  expect(blankToNull('  Bell ')).toBe('Bell')
  expect(blankToNull('   ')).toBeNull()
  expect(blankToNull('')).toBeNull()
  expect(blankToNull(null)).toBeNull()
  expect(blankToNull(12.5)).toBe('12.5')
})
