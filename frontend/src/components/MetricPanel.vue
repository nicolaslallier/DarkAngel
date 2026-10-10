<script setup lang="ts">
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { PanelInfo, Series } from '@/api/metrics'
import { align, formatValue } from '@/metrics'

const props = defineProps<{
  panel: PanelInfo
  series: Series[]
  loading: boolean
  error: string | null
}>()

const COLORS = ['#61afef', '#98c379', '#e5c07b', '#c678dd', '#56b6c2']

const host = ref<HTMLElement>()
let plot: uPlot | undefined
let observer: ResizeObserver | undefined

function build() {
  if (!host.value || props.series.length === 0) return
  const text = getComputedStyle(host.value).color
  plot = new uPlot(
    {
      width: host.value.clientWidth || 300,
      height: 160,
      series: [
        {},
        ...props.series.map((s, i) => ({
          label: s.label,
          stroke: COLORS[i % COLORS.length],
          width: 2,
          points: { show: false },
          value: (_: uPlot, v: number | null) => (v === null ? '—' : formatValue(props.panel.unit, v)),
        })),
      ],
      axes: [
        { stroke: text, grid: { stroke: 'rgba(128,128,128,.2)' } },
        {
          stroke: text,
          grid: { stroke: 'rgba(128,128,128,.2)' },
          size: 70,
          values: (_: uPlot, ticks: number[]) => ticks.map((t) => formatValue(props.panel.unit, t)),
        },
      ],
    },
    align(props.series),
    host.value,
  )
}

function destroy() {
  plot?.destroy()
  plot = undefined
}

onMounted(() => {
  build()
  if (typeof ResizeObserver !== 'undefined' && host.value) {
    observer = new ResizeObserver(() => {
      if (plot && host.value) plot.setSize({ width: host.value.clientWidth, height: 160 })
    })
    observer.observe(host.value)
  }
})

watch(
  () => props.series,
  (next, prev) => {
    const sameShape = plot && next.length === prev.length && next.every((s, i) => s.label === prev[i].label)
    if (sameShape) plot!.setData(align(next))
    else {
      destroy()
      build()
    }
  },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  destroy()
})
</script>

<template>
  <article class="panel">
    <h4>{{ panel.title }}</h4>
    <p v-if="error" class="error" data-test="panel-error">{{ error }}</p>
    <p v-else-if="!series.length && !loading" data-test="no-data">No data</p>
    <p v-else-if="!series.length">Loading…</p>
    <div ref="host" class="plot"></div>
  </article>
</template>

<style scoped>
.panel {
  min-width: 0;
}
h4 {
  margin: 0 0 0.25rem;
}
.error {
  color: #e06c75;
}
</style>
