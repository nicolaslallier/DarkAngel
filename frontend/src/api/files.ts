import { apiGet, apiRequest } from './client'

export interface HomeFile {
  id: string
  name: string
  size: number
  content_type: string
  modified: string | null
  folder_id: string | null
  description: string | null
  tags: string[]
}

export type Sort = 'name' | 'size' | 'updated_at'

/** No folder_id = the root. q or tag = search every folder instead. */
export interface ListParams {
  folder_id?: string
  q?: string
  tag?: string
  sort?: Sort
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

/** Omitted = unchanged; `folder_id: null` = move to the root. */
export interface FilePatch {
  name?: string
  description?: string | null
  tags?: string[]
  folder_id?: string | null
}

export function listFiles(params: ListParams = {}): Promise<HomeFile[]> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value))
  }
  const search = query.toString()
  return apiGet<HomeFile[]>(search ? `/files?${search}` : '/files')
}

export async function uploadFile(file: File, folderId: string | null = null): Promise<HomeFile> {
  const form = new FormData()
  form.append('file', file)
  if (folderId) form.append('folder_id', folderId)
  return (await apiRequest('POST', '/files', form)).json()
}

export async function updateFile(id: string, patch: FilePatch): Promise<HomeFile> {
  return (await apiRequest('PATCH', `/files/${id}`, JSON.stringify(patch), 'application/json')).json()
}

export async function downloadFile(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/files/${id}/content`)).blob()
}

export async function deleteFile(id: string): Promise<void> {
  await apiRequest('DELETE', `/files/${id}`)
}
