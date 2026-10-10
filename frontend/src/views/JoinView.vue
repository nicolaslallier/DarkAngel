<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

import { useHouseholdStore } from '@/stores/household'

const route = useRoute()
const router = useRouter()
const store = useHouseholdStore()

// The token rides in the URL fragment (never sent to servers or logs). Keep it in
// memory only and scrub it from the address bar and history entry.
const token = new URLSearchParams(route.hash.slice(1)).get('token') ?? ''
if (route.hash) void router.replace({ path: route.path })

async function join() {
  if (await store.join(token)) await router.replace('/providers')
}
</script>

<template>
  <section>
    <h1>Join a household</h1>
    <p v-if="!token" class="error">This link has no invitation token.</p>
    <template v-else>
      <p>You have been invited to join a household.</p>
      <p v-if="store.error" class="error">{{ store.error }}</p>
      <button type="button" data-test="join" :disabled="store.loading" @click="join">
        Join household
      </button>
      <RouterLink to="/providers">Cancel</RouterLink>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}
</style>
