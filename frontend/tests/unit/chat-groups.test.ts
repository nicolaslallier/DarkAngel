import { expect, it } from 'vitest'

import type { ConversationSummary } from '@/api/chat'
import { groupByDate } from '@/components/chat/groups'

const now = new Date(2026, 9, 17, 15, 0) // Sat 17 Oct 2026, local
const item = (id: string, d: Date): ConversationSummary => ({
  id,
  title: id,
  updated_at: d.toISOString(),
  streaming: false,
})
const day = (offset: number, h = 12) => new Date(2026, 9, 17 + offset, h)

it('groups by recency with the documented labels and omits empty groups', () => {
  const g = groupByDate(
    [
      item('today', day(0, 1)),
      item('yest', day(-1, 23)),
      item('d2', day(-2)),
      item('d7', day(-7)),
      item('d8', day(-8)),
      item('d30', day(-30)),
      item('d31', day(-31)),
    ],
    now,
  )
  expect(g.map((x) => x.label)).toEqual([
    'Today',
    'Yesterday',
    'Previous 7 days',
    'Previous 30 days',
    'September 2026',
  ])
  expect(g.map((x) => x.items.map((i) => i.id))).toEqual([
    ['today'],
    ['yest'],
    ['d2', 'd7'],
    ['d8', 'd30'],
    ['d31'],
  ])
})

it('labels older items by month, newest month first', () => {
  const g = groupByDate([item('a', new Date(2026, 5, 3)), item('b', new Date(2026, 7, 9))], now)
  expect(g.map((x) => x.label)).toEqual(['August 2026', 'June 2026'])
})

it('returns nothing for no conversations', () => {
  expect(groupByDate([], now)).toEqual([])
})
