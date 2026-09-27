import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { deleteFile, listFiles, uploadFile, type HomeFile } from '@/api/files'
import { deleteFolder, listFolders } from '@/api/folders'
import { PAGE_SIZE, useFilesStore } from '@/stores/files'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
}))
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
}))

const aFile: HomeFile = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'a.txt',
  size: 5,
  content_type: 'text/plain',
  modified: null,
  folder_id: null,
  description: null,
  tags: [],
}
const A = { id: 'a', name: 'A', parent_id: null }
const B = { id: 'b', name: 'B', parent_id: 'a' }
const FIRST_PAGE = { limit: PAGE_SIZE, offset: 0 }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  vi.mocked(listFiles).mockResolvedValue([aFile])
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

it('load() fills files and folders and clears loading', async () => {
  const store = useFilesStore()

  const pending = store.load()
  expect(store.loading).toBe(true)
  await pending

  expect(store.files).toEqual([aFile])
  expect(store.folders).toEqual([A, B])
  expect(store.error).toBeNull()
  expect(store.loading).toBe(false)
})

it('load(params) asks for the first page with those params', async () => {
  await useFilesStore().load({ folder_id: 'a', sort: 'name' })

  expect(listFiles).toHaveBeenCalledWith({ folder_id: 'a', sort: 'name', ...FIRST_PAGE })
})

it('offers more only after a full page, and loadMore() appends the next one', async () => {
  const page = Array.from({ length: PAGE_SIZE }, (_, i) => ({ ...aFile, id: String(i) }))
  vi.mocked(listFiles).mockResolvedValueOnce(page).mockResolvedValueOnce([aFile])
  const store = useFilesStore()

  await store.load({ q: 'tax' })
  expect(store.hasMore).toBe(true)

  await store.loadMore()

  expect(listFiles).toHaveBeenLastCalledWith({ q: 'tax', limit: PAGE_SIZE, offset: PAGE_SIZE })
  expect(store.files).toHaveLength(PAGE_SIZE + 1)
  expect(store.hasMore).toBe(false)
})

it('pathOf walks parent_id up to the root', async () => {
  const store = useFilesStore()
  await store.load()

  expect(store.pathOf('b')).toEqual([A, B])
  expect(store.pathOf(null)).toEqual([])
  expect(store.pathOf('gone')).toEqual([])
})

it('pathOf terminates instead of looping when folders form a cycle', async () => {
  // A concurrent pair of opposite moves can leave this on the server.
  const X = { id: 'x', name: 'X', parent_id: 'y' }
  const Y = { id: 'y', name: 'Y', parent_id: 'x' }
  vi.mocked(listFolders).mockResolvedValue([X, Y])
  const store = useFilesStore()
  await store.load()

  expect(store.pathOf('x')).toEqual([Y, X])
})

it('a slower response for an older load() does not overwrite a newer one', async () => {
  let resolveOld!: (files: HomeFile[]) => void
  let resolveNew!: (files: HomeFile[]) => void
  const older = new Promise<HomeFile[]>((resolve) => {
    resolveOld = resolve
  })
  const newer = new Promise<HomeFile[]>((resolve) => {
    resolveNew = resolve
  })
  vi.mocked(listFiles).mockReturnValueOnce(older).mockReturnValueOnce(newer)
  const store = useFilesStore()

  const first = store.load({ q: 'old' })
  const second = store.load({ q: 'new' })

  // The newer request's response lands first ...
  resolveNew([aFile])
  await second
  // ... then the older, slower request's response finally arrives.
  resolveOld([])
  await first

  expect(store.files).toEqual([aFile])
})

it('a slower loadMore() response is not appended once a newer one already landed', async () => {
  const firstPage = Array.from({ length: PAGE_SIZE }, (_, i) => ({ ...aFile, id: `p0-${i}` }))
  vi.mocked(listFiles).mockResolvedValueOnce(firstPage)
  const store = useFilesStore()
  await store.load()
  expect(store.hasMore).toBe(true)

  let resolveOld!: (files: HomeFile[]) => void
  let resolveNew!: (files: HomeFile[]) => void
  const older = new Promise<HomeFile[]>((resolve) => {
    resolveOld = resolve
  })
  const newer = new Promise<HomeFile[]>((resolve) => {
    resolveNew = resolve
  })
  vi.mocked(listFiles).mockReturnValueOnce(older).mockReturnValueOnce(newer)

  const first = store.loadMore()
  const second = store.loadMore()

  // The newer of the two loadMore() calls lands first ...
  const secondPage = Array.from({ length: PAGE_SIZE }, (_, i) => ({ ...aFile, id: `p1-${i}` }))
  resolveNew(secondPage)
  await second
  expect(store.files).toHaveLength(PAGE_SIZE * 2)

  // ... then the older, slower one finally arrives and must not be appended.
  resolveOld([{ ...aFile, id: 'stale' }])
  await first

  expect(store.files).toHaveLength(PAGE_SIZE * 2)
})

it('upload() sends every picked file into the folder, then reloads', async () => {
  const one = new File(['a'], 'one.txt')
  const two = new File(['b'], 'two.txt')

  await useFilesStore().upload([one, two], 'a')

  expect(vi.mocked(uploadFile).mock.calls).toEqual([
    [one, 'a'],
    [two, 'a'],
  ])
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('remove() deletes by id, then reloads', async () => {
  await useFilesStore().remove(aFile.id)

  expect(deleteFile).toHaveBeenCalledWith(aFile.id)
  expect(listFiles).toHaveBeenCalledTimes(1)
})

it('removeFolder() deletes an empty folder without a second ask', async () => {
  await useFilesStore().removeFolder(A)

  expect(deleteFolder).toHaveBeenCalledWith('a', false)
  expect(confirm).not.toHaveBeenCalled()
  expect(listFolders).toHaveBeenCalledTimes(1)
})

it('removeFolder() confirms a 409 with its counts, then deletes recursively', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(
    new ApiError('A is not empty', 409, { detail: 'A is not empty', folders: 1, files: 2 }),
  )

  await useFilesStore().removeFolder(A)

  expect(confirm).toHaveBeenCalledWith('Delete A and its 1 folders / 2 files?')
  expect(vi.mocked(deleteFolder).mock.calls).toEqual([
    ['a', false],
    ['a', true],
  ])
})

it('removeFolder() stops when the confirmation is dismissed', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(
    new ApiError('A is not empty', 409, { detail: 'A is not empty', folders: 0, files: 1 }),
  )
  vi.stubGlobal('confirm', vi.fn(() => false))
  const store = useFilesStore()

  await store.removeFolder(A)

  expect(deleteFolder).toHaveBeenCalledTimes(1)
  expect(store.error).toBeNull()
})

it('removeFolder() reports any other failure', async () => {
  vi.mocked(deleteFolder).mockRejectedValueOnce(new ApiError('No such folder', 404, null))
  const store = useFilesStore()

  await store.removeFolder(A)

  expect(store.error).toBe('No such folder')
  expect(confirm).not.toHaveBeenCalled()
})

it('captures the message of a failed action and stops loading', async () => {
  vi.mocked(deleteFile).mockRejectedValue(new Error('DELETE /files/a.txt failed with 404'))
  const store = useFilesStore()

  await store.remove(aFile.id)

  expect(store.error).toBe('DELETE /files/a.txt failed with 404')
  expect(store.loading).toBe(false)
})

it('clears a previous error when the next action succeeds', async () => {
  const store = useFilesStore()
  store.error = 'stale'

  await store.load()

  expect(store.error).toBeNull()
})
