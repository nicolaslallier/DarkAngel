<script setup lang="ts">
import { onMounted } from 'vue'

import { downloadFile, type HomeFile } from '@/api/files'
import { useFilesStore } from '@/stores/files'

const store = useFilesStore()

onMounted(store.load)

async function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  await store.upload([...(input.files ?? [])])
  input.value = ''
}

// The API wants a bearer token, so a plain <a href> cannot fetch the file:
// fetch it, then hand the blob to a throwaway link.
async function download(file: HomeFile) {
  try {
    const url = URL.createObjectURL(await downloadFile(file.id))
    const link = Object.assign(document.createElement('a'), { href: url, download: file.name })
    link.click()
    setTimeout(() => URL.revokeObjectURL(url))
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

function remove(file: HomeFile) {
  if (confirm(`Delete ${file.name}?`)) store.remove(file.id)
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}
</script>

<template>
  <section>
    <h1>Files</h1>

    <label>
      Upload
      <input type="file" multiple :disabled="store.loading" @change="onPick" />
    </label>

    <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>
    <p v-if="store.loading">Working…</p>
    <p v-else-if="!store.files.length">No files yet.</p>

    <table v-if="store.files.length">
      <thead>
        <tr>
          <th>Name</th>
          <th>Size</th>
          <th>Modified</th>
          <th><span class="visually-hidden">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="file in store.files" :key="file.id">
          <td>{{ file.name }}</td>
          <td>{{ formatSize(file.size) }}</td>
          <td>{{ file.modified ? new Date(file.modified).toLocaleString() : '' }}</td>
          <td class="actions">
            <button type="button" @click="download(file)">Download</button>
            <button type="button" @click="remove(file)">Delete</button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem;
}

th,
td {
  padding: 0.4rem;
  text-align: left;
  border-bottom: 1px solid #444;
}

.actions {
  display: flex;
  gap: 0.5rem;
}

.error {
  color: #e06c75;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
