import { apiGet, apiRequest } from './client'

export interface Folder {
  id: string
  name: string
  parent_id: string | null
}

const JSON_TYPE = 'application/json'

export function listFolders(): Promise<Folder[]> {
  return apiGet<Folder[]>('/folders')
}

export async function createFolder(folder: { name: string; parent_id: string | null }): Promise<Folder> {
  return (await apiRequest('POST', '/folders', JSON.stringify(folder), JSON_TYPE)).json()
}

/** Omitted = unchanged; `parent_id: null` = move to the root. */
export async function updateFolder(
  id: string,
  patch: { name?: string; parent_id?: string | null },
): Promise<Folder> {
  return (await apiRequest('PATCH', `/folders/${id}`, JSON.stringify(patch), JSON_TYPE)).json()
}

/** A non-empty folder without `recursive` rejects with an ApiError 409 whose
 *  body is `{detail, folders, files}`. */
export async function deleteFolder(id: string, recursive: boolean): Promise<void> {
  await apiRequest('DELETE', recursive ? `/folders/${id}?recursive=true` : `/folders/${id}`)
}
