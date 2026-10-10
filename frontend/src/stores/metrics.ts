import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'

import { getPanels, getRange, type PanelInfo, type RangeId, type Series } from '@/api/metrics'

const ROW_ORDER = ['host', 'containers', 'portainer'] as const

interface PanelState {
  loading: boolean
  error: string | null
  series: Series[]
}

export const useMetricsStore = defineStore('metrics', () => {
  const panels = ref<PanelInfo[]>([])
  const range = ref<RangeId>('6h')
  const refreshSeconds = ref(0)
  const state = reactive<Record<string, PanelState>>({})
  let timer: ReturnType<typeof setInterval> | undefined

  const rows = computed(() =>
    ROW_ORDER.map((row) => ({ row, panels: panels.value.filter((p) => p.row === row) })).filter(
      (r) => r.panels.length > 0,
    ),
  )

  async function loadPanel(id: string) {
    const asked = range.value
    const entry = (state[id] ??= { loading: false, error: null, series: [] })
    entry.loading = true
    try {
      const body = await getRange(id, asked)
      // The range moved on while this request was in flight: drop the answer.
      if (asked !== range.value) return
      entry.series = body.series
      entry.error = null
    } catch (e) {
      if (asked !== range.value) return
      entry.error = e instanceof Error ? e.message : String(e)
    } finally {
      if (asked === range.value) entry.loading = false
    }
  }

  function refresh() {
    return Promise.all(panels.value.map((p) => loadPanel(p.id)))
  }

  async function load() {
    try {
      panels.value = await getPanels()
    } catch {
      return // the section stays empty; the status cards above still work
    }
    await refresh()
  }

  function setRange(next: RangeId) {
    range.value = next
    void refresh()
  }

  function stop() {
    if (timer !== undefined) clearInterval(timer)
    timer = undefined
  }

  function setRefresh(seconds: number) {
    refreshSeconds.value = seconds
    stop()
    if (seconds > 0) {
      timer = setInterval(() => {
        if (!document.hidden) void refresh()
      }, seconds * 1000)
    }
  }

  return { panels, range, refreshSeconds, state, rows, load, setRange, setRefresh, stop }
})
