<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'

import type { RangeId } from '@/api/metrics'
import MetricPanel from '@/components/MetricPanel.vue'
import { useMetricsStore } from '@/stores/metrics'

const store = useMetricsStore()
const RANGES: RangeId[] = ['1h', '6h', '24h', '7d']
const REFRESH = [
  { label: 'Off', seconds: 0 },
  { label: '30 s', seconds: 30 },
  { label: '1 min', seconds: 60 },
]
const TITLES = { host: 'Host', containers: 'Containers', portainer: 'Portainer' }

onMounted(() => {
  void store.load()
  store.setRefresh(store.refreshSeconds) // the store outlives the page: re-arm the saved timer
})
onBeforeUnmount(() => store.stop())
</script>

<template>
  <section class="metrics">
    <header>
      <h2>Metrics</h2>
      <div class="controls">
        <button
          v-for="r in RANGES"
          :key="r"
          data-test="range"
          :aria-pressed="store.range === r"
          :class="{ active: store.range === r }"
          @click="store.setRange(r)"
        >
          {{ r }}
        </button>
        <label>
          Refresh
          <select
            data-test="refresh"
            :value="store.refreshSeconds"
            @change="store.setRefresh(Number(($event.target as HTMLSelectElement).value))"
          >
            <option v-for="o in REFRESH" :key="o.seconds" :value="o.seconds">{{ o.label }}</option>
          </select>
        </label>
      </div>
    </header>

    <div v-for="r in store.rows" :key="r.row">
      <h3>{{ TITLES[r.row] }}</h3>
      <div class="grid">
        <MetricPanel
          v-for="p in r.panels"
          :key="p.id"
          :panel="p"
          :series="store.state[p.id]?.series ?? []"
          :loading="store.state[p.id]?.loading ?? true"
          :error="store.state[p.id]?.error ?? null"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}
button.active {
  font-weight: 700;
  text-decoration: underline;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
}
</style>
