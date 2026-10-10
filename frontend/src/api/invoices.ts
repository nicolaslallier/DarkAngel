import { apiGet, apiRequest, apiSend } from './client'

export type InvoiceStatus = 'queued' | 'extracting' | 'to_validate' | 'validated' | 'failed'

export interface Tax {
  name: string
  amount: string
}

export interface Candidates {
  provider: { id: string; name: string } | null
  service: { id: string; name: string } | null
  new_provider: string | null
  new_service: string | null
}

export interface Invoice {
  id: string
  status: InvoiceStatus
  service_id: string | null
  has_pdf: boolean
  total: string | null
  due_on: string | null
  issued_on: string | null
  period_start: string | null
  period_end: string | null
  invoice_number: string | null
  consumption_qty: string | null
  consumption_unit: string | null
  paid_at: string | null
  paid: boolean
  error: string | null
  extraction: {
    raw?: { taxes?: Tax[] }
    candidates?: Candidates
    provider_id?: string | null
  } | null
  taxes: Tax[]
}

export interface InvoiceFields {
  service_id: string
  total: string
  due_on: string
  issued_on: string | null
  period_start: string | null
  period_end: string | null
  invoice_number: string | null
  consumption_qty: string | null
  consumption_unit: string | null
  taxes: Tax[]
}

export interface UploadTarget {
  service_id?: string
  provider_id?: string
}

export async function uploadInvoices(files: File[], target: UploadTarget = {}): Promise<Invoice[]> {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  if (target.service_id) form.append('service_id', target.service_id)
  if (target.provider_id) form.append('provider_id', target.provider_id)
  return (await apiRequest('POST', '/invoices', form)).json()
}

export function listInvoices(
  params: { status?: InvoiceStatus; service_id?: string; unpaid?: boolean } = {},
): Promise<Invoice[]> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value))
  }
  const text = query.toString()
  return apiGet<Invoice[]>(text ? `/invoices?${text}` : '/invoices')
}

export function getInvoice(id: string): Promise<Invoice> {
  return apiGet<Invoice>(`/invoices/${id}`)
}

export function validateInvoice(id: string, fields: InvoiceFields): Promise<Invoice> {
  return apiSend<Invoice>('POST', `/invoices/${id}/validate`, fields)
}

export function createManualInvoice(fields: InvoiceFields): Promise<Invoice> {
  return apiSend<Invoice>('POST', '/invoices/manual', fields)
}

export function setPaid(id: string, paid: boolean): Promise<Invoice> {
  return apiSend<Invoice>('POST', `/invoices/${id}/paid`, { paid })
}

export async function deleteInvoice(id: string): Promise<void> {
  await apiRequest('DELETE', `/invoices/${id}`)
}

/** The PDF goes through the authenticated client: an <iframe src> to the API
 *  would carry no bearer token. The caller turns the blob into an object URL. */
export async function invoicePdf(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/invoices/${id}/pdf`)).blob()
}
