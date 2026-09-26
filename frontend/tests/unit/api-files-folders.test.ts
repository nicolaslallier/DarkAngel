import { beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiRequest } from '@/api/client'
import { listFiles, updateFile, uploadFile } from '@/api/files'
import { createFolder, deleteFolder, listFolders, updateFolder } from '@/api/folders'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(async () => []),
  apiRequest: vi.fn(async () => ({ json: async () => ({}) })),
}))

const JSON_TYPE = 'application/json'

beforeEach(() => {
  vi.clearAllMocks()
})

it('listFiles with no params asks for the root', async () => {
  await listFiles()

  expect(apiGet).toHaveBeenCalledWith('/files')
})

it('listFiles puts only the params that are set in the query string', async () => {
  await listFiles({ folder_id: 'f1', q: 'tax', tag: undefined, sort: 'name', limit: 100, offset: 0 })

  expect(apiGet).toHaveBeenCalledWith('/files?folder_id=f1&q=tax&sort=name&limit=100&offset=0')
})

it('uploadFile sends folder_id only when there is one', async () => {
  await uploadFile(new File(['x'], 'a.txt'), 'f1')
  await uploadFile(new File(['x'], 'b.txt'))

  const [first, second] = vi.mocked(apiRequest).mock.calls.map((call) => call[2] as FormData)
  expect(first.get('folder_id')).toBe('f1')
  expect(second.has('folder_id')).toBe(false)
})

it('updateFile PATCHes JSON and keeps an explicit null folder', async () => {
  await updateFile('id1', { folder_id: null })

  expect(apiRequest).toHaveBeenCalledWith('PATCH', '/files/id1', '{"folder_id":null}', JSON_TYPE)
})

it('talks to /folders for list, create, update and delete', async () => {
  await listFolders()
  await createFolder({ name: 'A', parent_id: null })
  await updateFolder('f1', { name: 'B' })
  await deleteFolder('f1', false)
  await deleteFolder('f1', true)

  expect(apiGet).toHaveBeenCalledWith('/folders')
  expect(vi.mocked(apiRequest).mock.calls).toEqual([
    ['POST', '/folders', '{"name":"A","parent_id":null}', JSON_TYPE],
    ['PATCH', '/folders/f1', '{"name":"B"}', JSON_TYPE],
    ['DELETE', '/folders/f1'],
    ['DELETE', '/folders/f1?recursive=true'],
  ])
})
