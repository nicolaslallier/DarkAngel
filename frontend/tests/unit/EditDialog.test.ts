import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { updateFile, type HomeFile } from '@/api/files'
import { createFolder, updateFolder, type Folder } from '@/api/folders'
import EditDialog from '@/components/EditDialog.vue'
import { useFilesStore } from '@/stores/files'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/files', () => ({
  updateFile: vi.fn(async () => ({})),
  listFiles: vi.fn(async () => []),
  uploadFile: vi.fn(),
  deleteFile: vi.fn(),
}))
vi.mock('@/api/folders', () => ({
  createFolder: vi.fn(async () => ({})),
  updateFolder: vi.fn(async () => ({})),
  listFolders: vi.fn(async () => []),
  deleteFolder: vi.fn(),
}))

// jsdom has <dialog> but implements neither showModal() nor close().
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.setAttribute('open', '')
}
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
  this.removeAttribute('open')
  this.dispatchEvent(new Event('close'))
}

const A: Folder = { id: 'a', name: 'A', parent_id: null }
const B: Folder = { id: 'b', name: 'B', parent_id: 'a' }
const C: Folder = { id: 'c', name: 'C', parent_id: null }
const file: HomeFile = {
  id: 'f1',
  name: 'a.txt',
  size: 1,
  content_type: 'text/plain',
  modified: null,
  folder_id: 'a',
  description: null,
  tags: ['tax', '2026'],
}

function render(props: { file?: HomeFile; folder?: Folder; parentId?: string | null }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useFilesStore().folders = [A, B, C]
  return mount(EditDialog, { props, global: { plugins: [pinia] } })
}

const options = (wrapper: ReturnType<typeof render>) =>
  wrapper.findAll('select option').map((o) => o.text())

beforeEach(() => {
  vi.clearAllMocks()
})

it('opens as a modal when mounted', () => {
  const wrapper = render({ file })

  expect(wrapper.get('dialog').attributes('open')).toBeDefined()
})

it('prefills a file and saves it with one PATCH', async () => {
  const wrapper = render({ file })
  expect((wrapper.get('input[name="tags"]').element as HTMLInputElement).value).toBe('tax, 2026')
  expect(options(wrapper)).toEqual(['Home', 'A', 'A › B', 'C'])

  await wrapper.get('input[name="name"]').setValue('b.txt')
  await wrapper.get('input[name="tags"]').setValue('Tax, , receipts ')
  await wrapper.get('select').setValue('c')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(updateFile).toHaveBeenCalledWith('f1', {
    name: 'b.txt',
    description: '',
    tags: ['Tax', 'receipts'],
    folder_id: 'c',
  })
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

it('shows a 409 inside the dialog and stays open', async () => {
  vi.mocked(updateFile).mockRejectedValue(
    new ApiError('A file named b.txt already exists here', 409, null),
  )
  const wrapper = render({ file })

  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(wrapper.get('[role="alert"]').text()).toBe('A file named b.txt already exists here')
  expect(wrapper.emitted('saved')).toBeUndefined()
  expect(wrapper.get('dialog').attributes('open')).toBeDefined()
})

it('offers a folder every parent except itself and its descendants', async () => {
  const wrapper = render({ folder: A })

  expect(options(wrapper)).toEqual(['Home', 'C'])

  await wrapper.get('input[name="name"]').setValue('Bills')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(updateFolder).toHaveBeenCalledWith('a', { name: 'Bills', parent_id: null })
})

it('creates a folder inside the current one', async () => {
  const wrapper = render({ parentId: 'a' })

  await wrapper.get('input[name="name"]').setValue('New')
  await wrapper.get('form').trigger('submit')
  await flushPromises()

  expect(createFolder).toHaveBeenCalledWith({ name: 'New', parent_id: 'a' })
  expect(wrapper.find('textarea').exists()).toBe(false)
})

it('emits close on Cancel', async () => {
  const wrapper = render({ file })

  await wrapper.findAll('button').find((b) => b.text() === 'Cancel')!.trigger('click')

  expect(wrapper.emitted('close')).toHaveLength(1)
})
