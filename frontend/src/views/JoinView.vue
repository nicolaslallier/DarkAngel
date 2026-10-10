<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useHouseholdStore } from '@/stores/household'

const route = useRoute()
const router = useRouter()
const store = useHouseholdStore()
const missing = ref(false)

onMounted(async () => {
  const token = String(route.query.token ?? '')
  if (!token) {
    missing.value = true
    return
  }
  if (await store.join(token)) await router.replace('/providers')
})
</script>

<template>
  <section>
    <h1>Join a household</h1>
    <p v-if="missing" class="error">This link has no invitation token.</p>
    <p v-else-if="store.error" class="error">{{ store.error }}</p>
    <p v-else>Joining…</p>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}
</style>
