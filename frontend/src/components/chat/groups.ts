import type { ConversationSummary } from '@/api/chat'

const DAY = 86_400_000

export function groupByDate(items: ConversationSummary[], now: Date) {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const groups = new Map<string, ConversationSummary[]>()
  const sorted = [...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  for (const it of sorted) {
    const t = new Date(it.updated_at)
    // calendar-day difference in local time (DST-safe via rounding)
    const days = Math.round(
      (startOfToday - new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()) / DAY,
    )
    const label =
      days <= 0
        ? 'Today'
        : days === 1
          ? 'Yesterday'
          : days <= 7
            ? 'Previous 7 days'
            : days <= 30
              ? 'Previous 30 days'
              : t.toLocaleString('en-US', { month: 'long', year: 'numeric' })
    groups.set(label, [...(groups.get(label) ?? []), it])
  }
  return [...groups].map(([label, items]) => ({ label, items }))
}
