import { apiGet } from './client'

export type RangeId = '1h' | '6h' | '24h' | '7d'
export type Unit = 'percent' | 'bytes' | 'bytes_per_s' | 'count'

export interface PanelInfo {
  id: string
  title: string
  row: 'host' | 'containers' | 'portainer'
  unit: Unit
}

export interface Series {
  label: string
  points: [number, number][]
}

export interface RangeResult {
  panel: string
  range: RangeId
  series: Series[]
}

export function getPanels(): Promise<PanelInfo[]> {
  return apiGet<PanelInfo[]>('/metrics/panels')
}

export function getRange(panel: string, range: RangeId): Promise<RangeResult> {
  return apiGet<RangeResult>(`/metrics/range?panel=${encodeURIComponent(panel)}&range=${range}`)
}
