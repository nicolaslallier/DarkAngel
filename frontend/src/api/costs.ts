import { apiGet } from './client'

export interface UpcomingInvoice {
  invoice_id: string
  service_id: string
  provider_name: string
  service_name: string
  total: string
  due_on: string
  overdue: boolean
}

export interface Renewal {
  service_id: string
  provider_name: string
  service_name: string
  contract_end: string
  days_left: number
}

export interface Upcoming {
  invoices: UpcomingInvoice[]
  renewals: Renewal[]
}

export interface CostPoint {
  invoice_id: string
  month: string
  total: string
  reference: string | null
  flagged: boolean
}

export interface ServiceCosts {
  expected_monthly_cost: string | null
  threshold_pct: number
  points: CostPoint[]
}

export interface MonthlyCosts {
  months: { month: string; total: string }[]
}

export function getUpcoming(): Promise<Upcoming> {
  return apiGet<Upcoming>('/upcoming')
}

export function getServiceCosts(serviceId: string): Promise<ServiceCosts> {
  return apiGet<ServiceCosts>(`/services/${encodeURIComponent(serviceId)}/costs`)
}

export function getMonthlyCosts(): Promise<MonthlyCosts> {
  return apiGet<MonthlyCosts>('/costs/monthly')
}
