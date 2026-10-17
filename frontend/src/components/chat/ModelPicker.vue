<script setup lang="ts">
import type { ModelInfo } from '@/api/chat'

defineProps<{ models: ModelInfo[]; model: string }>()
defineEmits<{ select: [id: string] }>()
</script>

<template>
  <ul class="models" role="listbox" aria-label="Models">
    <li v-for="m in models" :key="m.id">
      <button type="button" role="option" :aria-selected="m.id === model" @click="$emit('select', m.id)">
        <span>{{ m.id === model ? '✓ ' : '' }}{{ m.id }}</span>
        <small v-if="m.loaded !== null">{{ m.loaded ? 'loaded' : 'not loaded' }}</small>
      </button>
    </li>
  </ul>
</template>

<style scoped>
.models {
  list-style: none;
  margin: 0;
  padding: 0.25rem;
  border: 1px solid rgba(127, 127, 127, 0.4);
  border-radius: 10px;
  background: Canvas;
  min-width: 260px;
}
button {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  width: 100%;
  border: 0;
  background: none;
  color: inherit;
  padding: 0.35rem 0.5rem;
  cursor: pointer;
  text-align: left;
}
small {
  opacity: 0.6;
}
</style>
