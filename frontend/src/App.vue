<script setup lang="ts">
import { signOut } from '@/auth'
</script>

<template>
  <header class="topbar">
    <nav>
      <RouterLink to="/" class="brand">DarkAngel</RouterLink>
      <RouterLink to="/" exact-active-class="active">Home</RouterLink>
      <RouterLink to="/files">Files</RouterLink>
      <RouterLink to="/providers">Providers</RouterLink>
      <RouterLink to="/household">Household</RouterLink>
      <RouterLink to="/chat">Chat</RouterLink>
      <RouterLink to="/about">About</RouterLink>
      <button type="button" class="sign-out" @click="signOut">Sign out</button>
    </nav>
  </header>

  <main>
    <RouterView />
  </main>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  height: var(--header-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

nav {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  height: 100%;
  max-width: 76rem;
  margin: 0 auto;
  padding: 0 var(--page-pad);
  overflow-x: auto;
}

nav a {
  padding: 0.35rem 0.75rem;
  border-radius: 8px;
  color: var(--muted);
  font-weight: 500;
  white-space: nowrap;
}
nav a:hover {
  color: var(--text-h);
  background: var(--surface-2);
  text-decoration: none;
}
nav a.router-link-active,
nav a.active {
  color: var(--accent);
  background: var(--accent-bg);
}
/* "/" prefixes every route, so only the exact match counts as active. */
nav a[href='/']:not(.active):not(.brand) {
  color: var(--muted);
  background: none;
}

.brand {
  margin-right: 0.75rem;
  color: var(--text-h);
  font-weight: 700;
  letter-spacing: -0.02em;
}
nav a.brand.router-link-active {
  color: var(--text-h);
  background: none;
}

.sign-out {
  margin-left: auto;
  flex-shrink: 0;
  box-shadow: none;
}

main {
  max-width: 76rem;
  margin: 0 auto;
  padding: var(--page-pad);
}
</style>
