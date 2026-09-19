<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { completeSignIn } from '@/auth'

const router = useRouter()
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    await router.replace(await completeSignIn())
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})
</script>

<template>
  <p v-if="error" role="alert">
    Sign-in failed: {{ error }}. <RouterLink to="/">Try again</RouterLink>
  </p>
  <p v-else>Signing in…</p>
</template>
