<script setup lang="ts">
import { onMounted } from 'vue'

import { useHealthStore } from '@/stores/health'
import { useMeStore } from '@/stores/me'

const store = useHealthStore()
const meStore = useMeStore()

onMounted(() => Promise.all([store.load(), meStore.load()]))
</script>

<template>
  <section>
    <h1>DarkAngel</h1>
    <p v-if="store.loading">Contacting the API…</p>
    <p v-else-if="store.error" class="error">Backend unreachable: {{ store.error }}</p>
    <p v-else-if="store.health">
      Backend status: <strong>{{ store.health.status }}</strong> (v{{ store.health.version }})
    </p>

    <p v-if="meStore.error" class="error">API rejected the session: {{ meStore.error }}</p>
    <p v-else-if="meStore.me">
      Signed in as <strong>{{ meStore.me.username ?? meStore.me.sub }}</strong>
    </p>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}
</style>
