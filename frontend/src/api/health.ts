import { apiGet } from './client'

export interface Health {
  status: string
  version: string
}

export function fetchHealth(): Promise<Health> {
  return apiGet<Health>('/health')
}
