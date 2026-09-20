import { apiGet, apiRequest } from './client'

export interface HomeFile {
  name: string
  size: number
  modified: string | null
}

const filePath = (name: string) => `/files/${encodeURIComponent(name)}`

export function listFiles(): Promise<HomeFile[]> {
  return apiGet<HomeFile[]>('/files')
}

export async function uploadFile(file: File): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  await apiRequest('POST', '/files', form)
}

export async function downloadFile(name: string): Promise<Blob> {
  return (await apiRequest('GET', filePath(name))).blob()
}

export async function deleteFile(name: string): Promise<void> {
  await apiRequest('DELETE', filePath(name))
}
