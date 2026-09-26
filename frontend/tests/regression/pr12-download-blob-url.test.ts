import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { downloadFile, type HomeFile } from '@/api/files'
import FilesView from '@/views/FilesView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
// The store's refresh() also loads the folder tree.
vi.mock('@/api/folders', () => ({
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(async () => {}),
}))

/**
 * Regression — PR #12, MinIO home files.
 *
 * The API needs a bearer token, so a download is a fetch into a Blob handed to
 * a throwaway <a>. Revoking the object URL in the same tick as `link.click()`
 * killed the download before the browser had taken it; the fix defers the
 * revoke to the next macrotask. This pins that the revoke is still deferred.
 */

const file: HomeFile = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'a.txt',
  size: 5,
  content_type: 'text/plain',
  modified: null,
  folder_id: null,
  description: null,
  tags: [],
}

vi.mock('@/api/files', () => ({
  listFiles: vi.fn(async () => [
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
  ]),
  uploadFile: vi.fn(async () => {}),
  deleteFile: vi.fn(async () => {}),
  downloadFile: vi.fn(async () => new Blob(['hello'])),
  updateFile: vi.fn(),
}))

// jsdom implements neither, and a real <a download> click would try to navigate.
const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')

beforeEach(() => {
  vi.clearAllMocks()
  click.mockImplementation(() => {})
  URL.createObjectURL = vi.fn(() => 'blob:darkangel/1')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.useRealTimers()
})

// flushPromises is a macrotask, so it would run the very timer under test.
// Draining microtasks by hand is the only way to look at the tick in between.
const microtasks = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

async function mountFiles() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/files', component: FilesView }],
  })
  await router.push('/files')
  return mount(FilesView, { global: { plugins: [createPinia(), router] } })
}

it('revokes the blob URL only after the click, never in the same tick', async () => {
  const wrapper = await mountFiles()
  await flushPromises()

  vi.useFakeTimers()
  await wrapper.findAll('tbody button').find((b) => b.text() === 'Download')!.trigger('click')
  await microtasks()

  expect(downloadFile).toHaveBeenCalledWith(file.id)
  expect(URL.createObjectURL).toHaveBeenCalled()
  expect(click).toHaveBeenCalled()
  // The bug: this was already called by now, and the download never started.
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()

  vi.runAllTimers()

  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:darkangel/1')
})

it('reports a failed download in the store error instead of throwing', async () => {
  vi.mocked(downloadFile).mockRejectedValue(new Error('GET /files/a.txt failed with 404'))
  const wrapper = await mountFiles()
  await flushPromises()

  await wrapper.findAll('tbody button').find((b) => b.text() === 'Download')!.trigger('click')
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('GET /files/a.txt failed with 404')
})
