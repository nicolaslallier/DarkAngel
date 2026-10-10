import { apiGet, apiRequest, apiSend } from './client'

export interface Service {
  id: string
  provider_id: string
  name: string
  category: string
  account_number: string | null
  contract_start: string | null
  contract_end: string | null
  renewal_reminder_days: number | null
  expected_monthly_cost: string | null
  auto_pay: boolean
  alert_threshold_pct: number
  archived: boolean
}

export interface Provider {
  id: string
  name: string
  website: string | null
  phone: string | null
  email: string | null
  notes: string | null
  services: Service[]
}

export type ProviderInput = Pick<Provider, 'name' | 'website' | 'phone' | 'email' | 'notes'>
export type ServiceInput = Omit<Service, 'id' | 'provider_id' | 'archived'>

export function listProviders(): Promise<Provider[]> {
  return apiGet<Provider[]>('/providers')
}

export function getProvider(id: string): Promise<Provider> {
  return apiGet<Provider>(`/providers/${id}`)
}

export function createProvider(input: ProviderInput): Promise<Provider> {
  return apiSend<Provider>('POST', '/providers', input)
}

export function updateProvider(id: string, patch: Partial<ProviderInput>): Promise<Provider> {
  return apiSend<Provider>('PATCH', `/providers/${id}`, patch)
}

export async function deleteProvider(id: string): Promise<void> {
  await apiRequest('DELETE', `/providers/${id}`)
}

export function createService(providerId: string, input: ServiceInput): Promise<Service> {
  return apiSend<Service>('POST', `/providers/${providerId}/services`, input)
}

export function getService(id: string): Promise<Service> {
  return apiGet<Service>(`/services/${id}`)
}

export function updateService(
  id: string,
  patch: Partial<ServiceInput> & { archived?: boolean },
): Promise<Service> {
  return apiSend<Service>('PATCH', `/services/${id}`, patch)
}

/** A service with invoices answers 409: archive it instead. */
export async function deleteService(id: string): Promise<void> {
  await apiRequest('DELETE', `/services/${id}`)
}
