import { createRouter, createWebHistory } from 'vue-router'

import { accessToken, signIn } from '@/auth'
import HomeView from '@/views/HomeView.vue'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    // Lazy-loaded so it ships in its own chunk.
    { path: '/files', name: 'files', component: () => import('@/views/FilesView.vue') },
    { path: '/about', name: 'about', component: () => import('@/views/AboutView.vue') },
    {
      path: '/auth/callback',
      name: 'auth-callback',
      component: () => import('@/views/AuthCallbackView.vue'),
      meta: { public: true },
    },
  ],
})

// Every route but the callback needs a Keycloak session: without a token the
// navigation is cancelled and the browser leaves for the Keycloak login page.
router.beforeEach(async (to) => {
  if (to.meta.public || (await accessToken())) return true
  await signIn(to.fullPath)
  return false
})
