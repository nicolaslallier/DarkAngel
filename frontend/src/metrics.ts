import type { Series, Unit } from '@/api/metrics'
import { bytes } from '@/format'

/** uPlot wants one shared time axis: merge the timestamps, pad gaps with null. */
export function align(series: Series[]): [number[], ...(number | null)[][]] {
  const xs = [...new Set(series.flatMap((s) => s.points.map(([t]) => t)))].sort((a, b) => a - b)
  const rows = series.map((s) => {
    const byTime = new Map(s.points)
    return xs.map((t) => byTime.get(t) ?? null)
  })
  return [xs, ...rows]
}

export function formatValue(unit: Unit, v: number): string {
  if (unit === 'percent') return `${v.toFixed(1)}%`
  if (unit === 'bytes') return bytes(v)
  if (unit === 'bytes_per_s') return `${bytes(v)}/s`
  return String(v)
}
