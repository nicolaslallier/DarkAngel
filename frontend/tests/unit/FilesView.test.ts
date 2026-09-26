import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { deleteFile, listFiles, uploadFile, type HomeFile } from '@/api/files'
import { deleteFolder, listFolders } from '@/api/folders'
import { PAGE_SIZE } from '@/stores/files'
import FilesView from '@/views/FilesView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
  updateFile: vi.fn(async () => ({})),
}))
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
  createFolder: vi.fn(async () => ({})),
  updateFolder: vi.fn(async () => ({})),
}))

HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.setAttribute('open', '')
}

const A = { id: 'a', name: 'A', parent_id: null }
const B = { id: 'b', name: 'B', parent_id: 'a' }

function aFile(overrides: Partial<HomeFile> = {}): HomeFile {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'a.txt',
    size: 5,
    content_type: 'text/plain',
    modified: null,
    folder_id: null,
    description: null,
    tags: [],
    ...overrides,
  }
}

async function render(path = '/files') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/files', component: FilesView }],
  })
  await router.push(path)
  const wrapper = mount(FilesView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return { wrapper, router }
}

const button = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll('button').find((b) => b.text() === text)!

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listFiles).mockResolvedValue([])
  vi.mocked(listFolders).mockResolvedValue([])
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  vi.useRealTimers()
})

it('shows an empty state before anything is uploaded', async () => {
  const { wrapper } = await render()

  expect(wrapper.text()).toContain('No files yet.')
  expect(wrapper.find('table').exists()).toBe(false)
})

it('lists what the API returns, with a human-readable size', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile({ name: 'bail été.txt', size: 2048 })])

  const { wrapper } = await render()

  const cells = wrapper.findAll('tbody td').map((c) => c.text())
  expect(cells[0]).toBe('bail été.txt')
  expect(cells[2]).toBe('2.0 KB')
})

it('browses a folder: breadcrumb, then subfolders before files', async () => {
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.mocked(listFiles).mockResolvedValue([aFile({ folder_id: 'a' })])

  const { wrapper } = await render('/files?folder=a')

  expect(listFiles).toHaveBeenCalledWith(expect.objectContaining({ folder_id: 'a', offset: 0 }))
  const crumbs = wrapper.get('nav[aria-label="Breadcrumb"]').text().replace(/\s+/g, ' ')
  expect(crumbs).toBe('Home › A')
  const rows = wrapper.findAll('tbody tr').map((r) => r.find('td').text())
  expect(rows).toEqual(['B/', 'a.txt'])
  expect(wrapper.find('th.location').exists()).toBe(false)
})

it('search mode hides folders and shows where each file lives', async () => {
  vi.mocked(listFolders).mockResolvedValue([A, B])
  vi.mocked(listFiles).mockResolvedValue([aFile({ folder_id: 'b' })])

  const { wrapper } = await render('/files?q=tax')

  expect(listFiles).toHaveBeenCalledWith(expect.objectContaining({ q: 'tax' }))
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.find('th.location').exists()).toBe(true)
  expect(wrapper.get('td.location').text()).toBe('A › B')
})

it('writes the search box to ?q after a 300 ms pause', async () => {
  const { wrapper, router } = await render()

  vi.useFakeTimers()
  await wrapper.get('input[type="search"]').setValue('  tax ')
  vi.advanceTimersByTime(299)
  expect(router.currentRoute.value.query.q).toBeUndefined()
  vi.advanceTimersByTime(1)
  vi.useRealTimers()
  await flushPromises()

  expect(router.currentRoute.value.query.q).toBe('tax')
})

it('does not write a stale search once the view has been unmounted', async () => {
  const { wrapper, router } = await render()

  vi.useFakeTimers()
  await wrapper.get('input[type="search"]').setValue('tax')
  wrapper.unmount()
  vi.advanceTimersByTime(300)
  vi.useRealTimers()
  await flushPromises()

  expect(router.currentRoute.value.query.q).toBeUndefined()
})

it('a search inside a folder keeps folder in the query, and clearing it returns to that folder', async () => {
  vi.mocked(listFolders).mockResolvedValue([A])
  const { wrapper, router } = await render('/files?folder=a')

  vi.useFakeTimers()
  await wrapper.get('input[type="search"]').setValue('tax')
  vi.advanceTimersByTime(300)
  vi.useRealTimers()
  await flushPromises()

  expect(router.currentRoute.value.query.folder).toBe('a')
  expect(router.currentRoute.value.query.q).toBe('tax')

  vi.useFakeTimers()
  await wrapper.get('input[type="search"]').setValue('')
  vi.advanceTimersByTime(300)
  vi.useRealTimers()
  await flushPromises()

  expect(router.currentRoute.value.query.folder).toBe('a')
  expect(router.currentRoute.value.query.q).toBeUndefined()
})

it('a tag chip filters by that tag, and the active tag can be removed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile({ tags: ['tax'] })])
  const { wrapper, router } = await render()

  await button(wrapper, 'tax').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.tag).toBe('tax')
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ tag: 'tax' }))

  await button(wrapper, '#tax ×').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.tag).toBeUndefined()
})

it('sorts by a header, flipping the order on a second click', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  const { wrapper } = await render()

  await button(wrapper, 'Name').trigger('click')
  await flushPromises()
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'name', order: 'asc' }))

  await button(wrapper, 'Name').trigger('click')
  await flushPromises()
  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'name', order: 'desc' }))
})

it('offers Load more after a full page', async () => {
  const page = Array.from({ length: PAGE_SIZE }, (_, i) => aFile({ id: String(i) }))
  vi.mocked(listFiles).mockResolvedValueOnce(page).mockResolvedValue([])
  const { wrapper } = await render()

  await button(wrapper, 'Load more').trigger('click')
  await flushPromises()

  expect(listFiles).toHaveBeenLastCalledWith(expect.objectContaining({ offset: PAGE_SIZE }))
})

it('uploads every picked file into the current folder and clears the input', async () => {
  vi.mocked(listFolders).mockResolvedValue([A])
  const { wrapper } = await render('/files?folder=a')

  const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
  const input = wrapper.get('input[type="file"]')
  // jsdom's FileList is read-only, so it is replaced outright.
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()

  expect(vi.mocked(uploadFile).mock.calls).toEqual([[file, 'a']])
  expect((input.element as HTMLInputElement).value).toBe('')
})

it('asks before deleting a file, and deletes when confirmed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete a.txt?')
  expect(deleteFile).toHaveBeenCalledWith('11111111-1111-1111-1111-111111111111')
})

it('does not delete a file when the confirmation is dismissed', async () => {
  vi.mocked(listFiles).mockResolvedValue([aFile()])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(deleteFile).not.toHaveBeenCalled()
})

it('asks before deleting a folder, and skips the delete when dismissed', async () => {
  vi.mocked(listFolders).mockResolvedValue([{ id: 'a', name: 'A', parent_id: null }])
  vi.stubGlobal('confirm', vi.fn(() => false))
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(confirm).toHaveBeenCalledWith('Delete A?')
  expect(deleteFolder).not.toHaveBeenCalled()
})

it('deletes a subfolder through the store', async () => {
  vi.mocked(listFolders).mockResolvedValue([A])
  const { wrapper } = await render()

  await button(wrapper, 'Delete').trigger('click')
  await flushPromises()

  expect(deleteFolder).toHaveBeenCalledWith('a', false)
})

it('New folder opens the dialog', async () => {
  const { wrapper } = await render()

  await button(wrapper, 'New folder').trigger('click')

  expect(wrapper.get('dialog').text()).toContain('New folder')
})

it('shows the store error in an alert', async () => {
  vi.mocked(listFiles).mockRejectedValue(new Error('GET /files failed with 502'))
  const { wrapper } = await render()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files failed with 502')
})
