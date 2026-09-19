<script setup lang="ts">
import { onMounted } from 'vue'

import { useHealthStore } from '@/stores/health'

const store = useHealthStore()

onMounted(() => store.load())
</script>

<template>
  <section>
    <h1>DarkAngel</h1>
    <p v-if="store.loading">Contacting the API…</p>
    <p v-else-if="store.error" class="error">Backend unreachable: {{ store.error }}</p>
    <p v-else-if="store.health">
      Backend status: <strong>{{ store.health.status }}</strong> (v{{ store.health.version }})
    </p>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}
</style>
