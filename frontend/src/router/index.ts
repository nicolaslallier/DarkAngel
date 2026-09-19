import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    // Lazy-loaded so it ships in its own chunk.
    { path: '/about', name: 'about', component: () => import('@/views/AboutView.vue') },
  ],
})
