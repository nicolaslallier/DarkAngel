import { apiGet, apiRequest } from './client'

export interface HomeFile {
  id: string
  name: string
  size: number
  content_type: string
  modified: string | null
}

export function listFiles(): Promise<HomeFile[]> {
  return apiGet<HomeFile[]>('/files')
}

export async function uploadFile(file: File): Promise<HomeFile> {
  const form = new FormData()
  form.append('file', file)
  return (await apiRequest('POST', '/files', form)).json()
}

export async function downloadFile(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/files/${id}/content`)).blob()
}

export async function deleteFile(id: string): Promise<void> {
  await apiRequest('DELETE', `/files/${id}`)
}
