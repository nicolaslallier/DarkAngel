import { defineStore } from 'pinia'
import { ref } from 'vue'

import { fetchHealth, type Health } from '@/api/health'

export const useHealthStore = defineStore('health', () => {
  const health = ref<Health | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)

  async function load() {
    loading.value = true
    error.value = null

    try {
      health.value = await fetchHealth()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { health, error, loading, load }
})
