import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { deleteFile, listFiles, uploadFile } from '@/api/files'
import FilesView from '@/views/FilesView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
}))
// The store's refresh() also loads the folder tree.
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
}))

function render() {
  return mount(FilesView, { global: { plugins: [createPinia()] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listFiles).mockResolvedValue([])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

it('shows an empty state before anything is uploaded', async () => {
  const wrapper = render()
  await flushPromises()

  expect(wrapper.text()).toContain('No files yet.')
  expect(wrapper.find('table').exists()).toBe(false)
})

it('lists what the API returns, with a human-readable size', async () => {
  vi.mocked(listFiles).mockResolvedValue([
    {
      id: '11111111-1111-1111-1111-111111111111',
      name: 'bail été.txt',
      size: 2048,
      content_type: 'text/plain',
      modified: null,
      folder_id: null,
      description: null,
      tags: [],
    },
  ])

  const wrapper = render()
  await flushPromises()

  const cells = wrapper.findAll('tbody td').map((c) => c.text())
  expect(cells[0]).toBe('bail été.txt')
  expect(cells[1]).toBe('2.0 KB')
})

it('uploads every picked file and clears the input', async () => {
  const wrapper = render()
  await flushPromises()

  const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
  const input = wrapper.get('input[type="file"]')
  // jsdom's FileList is read-only, so it is replaced outright.
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  // The store calls `picked.map(uploadFile)`, which passes map's (item, index,
  // array) to each call -- only the leading argument is part of the contract
  // (see tests/unit/stores-files.test.ts for the same trap).
  const uploadedFiles = vi.mocked(uploadFile).mock.calls.map(([f]) => f)
  expect(uploadedFiles).toEqual([file])
  expect((input.element as HTMLInputElement).value).toBe('')
})

it('asks before deleting, and deletes when confirmed', async () => {
  vi.mocked(listFiles).mockResolvedValue([
    {
      id: '11111111-1111-1111-1111-111111111111',
      name: 'a.txt',
      size: 5,
      content_type: 'text/plain',
      modified: null,
      folder_id: null,
      description: null,
      tags: [],
    },
  ])
  const wrapper = render()
  await flushPromises()

  await wrapper.findAll('tbody button')[1].trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete a.txt?')
  expect(deleteFile).toHaveBeenCalledWith('11111111-1111-1111-1111-111111111111')
})

it('does not delete when the confirmation is dismissed', async () => {
  vi.mocked(listFiles).mockResolvedValue([
    {
      id: '11111111-1111-1111-1111-111111111111',
      name: 'a.txt',
      size: 5,
      content_type: 'text/plain',
      modified: null,
      folder_id: null,
      description: null,
      tags: [],
    },
  ])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const wrapper = render()
  await flushPromises()

  await wrapper.findAll('tbody button')[1].trigger('click')
  await flushPromises()

  expect(deleteFile).not.toHaveBeenCalled()
})

it('shows the store error in an alert', async () => {
  vi.mocked(listFiles).mockRejectedValue(new Error('GET /files failed with 502'))
  const wrapper = render()
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files failed with 502')
})
