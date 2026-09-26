import { defineStore } from 'pinia'
import { ref } from 'vue'

import { deleteFile, listFiles, uploadFile, type HomeFile } from '@/api/files'

export const useFilesStore = defineStore('files', () => {
  const files = ref<HomeFile[]>([])
  const error = ref<string | null>(null)
  const loading = ref(false)

  // Runs an action, then reloads the list so it reflects what MinIO holds.
  async function run(action: () => Promise<unknown>) {
    loading.value = true
    error.value = null

    try {
      await action()
      files.value = await listFiles()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  const load = () => run(async () => {})
  const upload = (picked: File[]) => run(() => Promise.all(picked.map((file) => uploadFile(file))))
  const remove = (id: string) => run(() => deleteFile(id))

  return { files, error, loading, load, upload, remove }
})
