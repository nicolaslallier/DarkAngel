import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  test: {
    // Components and the api client both touch the DOM and fetch.
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    // The default reporter collapses a green run to "8 passed (8)" — which
    // files and which cases ran is exactly what you want when reading CI.
    reporters: ['verbose'],
    coverage: {
      // No threshold yet: the first CI run is the baseline, and docs/testing.md
      // says to raise it to that number and never below it.
      reporter: ['text', 'lcov'],
      include: ['src/**'],
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Forwarded to the FastAPI backend during development.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
