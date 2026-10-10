<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ points: { label: string; value: number; flagged?: boolean }[] }>()

const WIDTH = 300
const HEIGHT = 100
const PAD = 8

// Always drawn from zero so a small rise does not look like a spike.
const dots = computed(() => {
  const max = Math.max(...props.points.map((p) => p.value), 1)
  const step = props.points.length > 1 ? (WIDTH - 2 * PAD) / (props.points.length - 1) : 0
  return props.points.map((p, i) => ({
    ...p,
    x: PAD + i * step,
    y: HEIGHT - PAD - (p.value / max) * (HEIGHT - 2 * PAD),
  }))
})

const line = computed(() => dots.value.map((d) => `${d.x},${d.y}`).join(' '))
</script>

<template>
  <p v-if="points.length < 2" class="empty">Not enough data for a chart.</p>
  <svg v-else :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" role="img" aria-label="Cost history">
    <polyline :points="line" fill="none" stroke="currentColor" stroke-width="2" />
    <circle
      v-for="(d, i) in dots"
      :key="i"
      :cx="d.x"
      :cy="d.y"
      r="3.5"
      :class="{ flagged: d.flagged }"
    >
      <title>{{ d.label }}: {{ d.value }}</title>
    </circle>
  </svg>
</template>

<style scoped>
svg {
  width: 100%;
  max-width: 40rem;
}

circle {
  fill: currentColor;
}

circle.flagged {
  fill: #e06c75;
}
</style>
