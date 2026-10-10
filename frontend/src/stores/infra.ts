import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { getInfra, type BackupInfo, type InstanceStatus } from '@/api/infra'

// The collector runs every 5 minutes: a reading older than this means it stopped.
const STALE_AFTER_MS = 15 * 60 * 1000

export const useInfraStore = defineStore('infra', () => {
  const instances = ref<InstanceStatus[]>([])
  const backups = ref<BackupInfo[]>([])
  const error = ref<string | null>(null)
  const loading = ref(false)

  const collectorStale = computed(() => {
    const times = [...instances.value, ...backups.value].map((r) => Date.parse(r.checked_at))
    return times.length > 0 && Date.now() - Math.max(...times) > STALE_AFTER_MS
  })

  async function load() {
    loading.value = true
    error.value = null
    try {
      const body = await getInfra()
      instances.value = body.instances
      backups.value = body.backups
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { instances, backups, error, loading, collectorStale, load }
})
