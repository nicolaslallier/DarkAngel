<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { downloadFile, type HomeFile, type Sort } from '@/api/files'
import type { Folder } from '@/api/folders'
import EditDialog from '@/components/EditDialog.vue'
import { useFilesStore } from '@/stores/files'

const store = useFilesStore()
const route = useRoute()
const router = useRouter()

// Where you are and what you searched live in the URL, so refresh, back and links work.
const param = (value: unknown) => (typeof value === 'string' && value ? value : undefined)
const folderId = computed(() => param(route.query.folder) ?? null)
const q = computed(() => param(route.query.q))
const tag = computed(() => param(route.query.tag))
const searching = computed(() => Boolean(q.value || tag.value))

// How you look at it is a preference, not a location: local state, not the URL.
const sort = ref<Sort>('updated_at')
const order = ref<'asc' | 'desc'>('desc')

function reload() {
  return store.load({
    folder_id: folderId.value ?? undefined,
    q: q.value,
    tag: tag.value,
    sort: sort.value,
    order: order.value,
  })
}
watch([folderId, q, tag], reload, { immediate: true })

// Discrete clicks (a tag chip, a breadcrumb/folder link) push a new history
// entry; the debounced search write below replaces the current one instead,
// so a typing session doesn't fill history with one entry per pause.
function setQuery(patch: Record<string, string | undefined>, replace = false) {
  const query = { ...route.query, ...patch }
  return replace ? router.replace({ query }) : router.push({ query })
}

const searchText = ref(q.value ?? '')
watch(q, (value) => (searchText.value = value ?? ''))
let debounce: ReturnType<typeof setTimeout> | undefined
function onSearch() {
  clearTimeout(debounce)
  debounce = setTimeout(() => setQuery({ q: searchText.value.trim() || undefined }, true), 300)
}
// A pending debounce firing after unmount would push the stale search into
// whatever route the user navigated to next.
onUnmounted(() => clearTimeout(debounce))

function sortBy(key: Sort) {
  if (sort.value === key) {
    order.value = order.value === 'asc' ? 'desc' : 'asc'
  } else {
    sort.value = key
    order.value = key === 'name' ? 'asc' : 'desc'
  }
  reload()
}

const breadcrumb = computed(() => store.pathOf(folderId.value))
const subfolders = computed(() =>
  searching.value ? [] : store.folders.filter((f) => f.parent_id === folderId.value),
)
const location = (file: HomeFile) =>
  store.pathOf(file.folder_id).map((f) => f.name).join(' › ') || 'Home'
const at = (id: string | null) => ({ query: id ? { folder: id } : {} })

// null = closed; {} = a new folder inside the current one.
const editing = ref<{ file?: HomeFile; folder?: Folder } | null>(null)
function onSaved() {
  editing.value = null
  reload()
}

async function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  await store.upload([...(input.files ?? [])], folderId.value)
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

// Same one-line ask as a file; a non-empty folder gets a second, counted one from the store.
function removeFolder(folder: Folder) {
  if (confirm(`Delete ${folder.name}?`)) store.removeFolder(folder)
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

    <nav aria-label="Breadcrumb" class="breadcrumb">
      <RouterLink :to="at(null)">Home</RouterLink>
      <template v-for="folder in breadcrumb" :key="folder.id">
        › <RouterLink :to="at(folder.id)">{{ folder.name }}</RouterLink>
      </template>
    </nav>

    <div class="toolbar">
      <input
        v-model="searchText"
        type="search"
        placeholder="Search name, description, tags"
        aria-label="Search"
        @input="onSearch"
      />
      <button v-if="tag" type="button" class="chip" @click="setQuery({ tag: undefined })">
        #{{ tag }} ×
      </button>
      <button type="button" @click="editing = {}">New folder</button>
      <label>
        Upload
        <input type="file" multiple :disabled="store.loading" @change="onPick" />
      </label>
    </div>

    <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>
    <p v-if="store.loading">Working…</p>
    <p v-else-if="!store.files.length && !subfolders.length">
      {{ searching ? 'No matches.' : 'No files yet.' }}
    </p>

    <table v-if="store.files.length || subfolders.length">
      <thead>
        <tr>
          <th><button type="button" class="sort" @click="sortBy('name')">Name</button></th>
          <th>Tags</th>
          <th><button type="button" class="sort" @click="sortBy('size')">Size</button></th>
          <th><button type="button" class="sort" @click="sortBy('updated_at')">Modified</button></th>
          <th v-if="searching" class="location">Location</th>
          <th><span class="visually-hidden">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="folder in subfolders" :key="folder.id">
          <td><RouterLink :to="at(folder.id)">{{ folder.name }}/</RouterLink></td>
          <td></td>
          <td></td>
          <td></td>
          <td class="actions">
            <button type="button" @click="editing = { folder }">Edit</button>
            <button type="button" @click="removeFolder(folder)">Delete</button>
          </td>
        </tr>
        <tr v-for="file in store.files" :key="file.id">
          <td>{{ file.name }}</td>
          <td>
            <button
              v-for="t in file.tags"
              :key="t"
              type="button"
              class="chip"
              @click="setQuery({ tag: t })"
            >{{ t }}</button>
          </td>
          <td>{{ formatSize(file.size) }}</td>
          <td>{{ file.modified ? new Date(file.modified).toLocaleString() : '' }}</td>
          <td v-if="searching" class="location">
            <RouterLink :to="at(file.folder_id)">{{ location(file) }}</RouterLink>
          </td>
          <td class="actions">
            <button type="button" @click="download(file)">Download</button>
            <button type="button" @click="editing = { file }">Edit</button>
            <button type="button" @click="remove(file)">Delete</button>
          </td>
        </tr>
      </tbody>
    </table>

    <button v-if="store.hasMore" type="button" :disabled="store.loading" @click="store.loadMore()">
      Load more
    </button>

    <EditDialog
      v-if="editing"
      :file="editing.file"
      :folder="editing.folder"
      :parent-id="folderId"
      @saved="onSaved"
      @close="editing = null"
    />
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

.toolbar,
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.breadcrumb {
  margin-bottom: 0.75rem;
}

.chip {
  border-radius: 1rem;
  padding: 0.1rem 0.6rem;
  margin-right: 0.25rem;
}

.sort {
  background: none;
  border: none;
  font: inherit;
  font-weight: bold;
  cursor: pointer;
  padding: 0;
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
