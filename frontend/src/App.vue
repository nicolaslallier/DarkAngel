<script setup lang="ts">
import { signOut } from '@/auth'
</script>

<template>
  <header>
    <nav>
      <RouterLink to="/">Home</RouterLink>
      <RouterLink to="/files">Files</RouterLink>
      <RouterLink to="/about">About</RouterLink>
      <button type="button" class="sign-out" @click="signOut">Sign out</button>
    </nav>
  </header>

  <main>
    <RouterView />
  </main>
</template>

<style scoped>
nav {
  display: flex;
  gap: 1rem;
  padding: 1rem 0;
}

.sign-out {
  margin-left: auto;
}
</style>
