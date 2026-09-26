import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError } from '@/api/client'
import { deleteFile, listFiles, uploadFile, type HomeFile, type ListParams } from '@/api/files'
import { deleteFolder, listFolders, type Folder } from '@/api/folders'

export const PAGE_SIZE = 100

export const useFilesStore = defineStore('files', () => {
  const files = ref<HomeFile[]>([])
  // Flat: the whole tree is small, and pathOf() derives every path from it.
  const folders = ref<Folder[]>([])
  const hasMore = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(false)
  let params: ListParams = {}

  async function run(action: () => Promise<unknown>) {
    loading.value = true
    error.value = null

    try {
      await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  // No optimistic updates: every mutation reloads the folders and the first page.
  async function refresh() {
    const [tree, page] = await Promise.all([
      listFolders(),
      listFiles({ ...params, limit: PAGE_SIZE, offset: 0 }),
    ])
    folders.value = tree
    files.value = page
    hasMore.value = page.length === PAGE_SIZE
  }

  /** Root first, ending at `id`. Empty for the root or an unknown id. */
  function pathOf(id: string | null): Folder[] {
    const path: Folder[] = []
    let folder = folders.value.find((f) => f.id === id)
    while (folder) {
      path.unshift(folder)
      const parent = folder.parent_id
      folder = folders.value.find((f) => f.id === parent)
    }
    return path
  }

  function load(next: ListParams = {}) {
    params = next
    return run(refresh)
  }

  const loadMore = () =>
    run(async () => {
      const page = await listFiles({ ...params, limit: PAGE_SIZE, offset: files.value.length })
      files.value = [...files.value, ...page]
      hasMore.value = page.length === PAGE_SIZE
    })

  const upload = (picked: File[], folderId: string | null = null) =>
    run(async () => {
      await Promise.all(picked.map((file) => uploadFile(file, folderId)))
      await refresh()
    })

  const remove = (id: string) =>
    run(async () => {
      await deleteFile(id)
      await refresh()
    })

  // BR-7: try the plain delete; a 409 names the subtree, and only then ask.
  const removeFolder = (folder: Folder) =>
    run(async () => {
      try {
        await deleteFolder(folder.id, false)
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 409) throw e
        const counts = e.body as { folders: number; files: number }
        const question = `Delete ${folder.name} and its ${counts.folders} folders / ${counts.files} files?`
        if (!confirm(question)) return
        await deleteFolder(folder.id, true)
      }
      await refresh()
    })

  return {
    files,
    folders,
    hasMore,
    error,
    loading,
    pathOf,
    load,
    loadMore,
    upload,
    remove,
    removeFolder,
  }
})
