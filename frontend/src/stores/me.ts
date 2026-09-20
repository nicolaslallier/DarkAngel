import { defineStore } from 'pinia'
import { ref } from 'vue'

import { fetchMe, type Me } from '@/api/me'

export const useMeStore = defineStore('me', () => {
  const me = ref<Me | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)

  async function load() {
    loading.value = true
    error.value = null

    try {
      me.value = await fetchMe()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { me, error, loading, load }
})
