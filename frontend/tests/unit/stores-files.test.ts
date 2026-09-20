import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { deleteFile, listFiles, uploadFile } from '@/api/files'
import { useFilesStore } from '@/stores/files'

vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
}))

const aFile = { name: 'a.txt', size: 5, modified: null }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  vi.mocked(listFiles).mockResolvedValue([aFile])
})

it('load() fills the list and clears loading', async () => {
  const store = useFilesStore()

  const pending = store.load()
  expect(store.loading).toBe(true)
  await pending

  expect(store.files).toEqual([aFile])
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('upload() sends every picked file, then reloads', async () => {
  const one = new File(['a'], 'one.txt')
  const two = new File(['b'], 'two.txt')

  await useFilesStore().upload([one, two])

  // `picked.map(uploadFile)` passes map's (item, index, array) to each call,
  // so only the leading argument -- the file itself -- is part of the contract.
  const uploadedFiles = vi.mocked(uploadFile).mock.calls.map(([file]) => file)
  expect(uploadedFiles).toEqual([one, two])
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('remove() deletes, then reloads', async () => {
  await useFilesStore().remove('a.txt')

  expect(deleteFile).toHaveBeenCalledWith('a.txt')
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('captures the message of a failed action and stops loading', async () => {
  vi.mocked(deleteFile).mockRejectedValue(new Error('DELETE /files/a.txt failed with 404'))
  const store = useFilesStore()

  await store.remove('a.txt')

  expect(store.error).toBe('DELETE /files/a.txt failed with 404')
  expect(store.loading).toBe(false)
})

it('clears a previous error when the next action succeeds', async () => {
  const store = useFilesStore()
  store.error = 'stale'

  await store.load()

  expect(store.error).toBeNull()
})
