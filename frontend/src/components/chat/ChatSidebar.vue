<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import type { ConversationSummary } from '@/api/chat'
import { groupByDate } from '@/components/chat/groups'

const props = defineProps<{ items: ConversationSummary[]; currentId: string | null; open: boolean }>()
const emit = defineEmits<{
  new: []
  search: [q: string]
  select: [id: string]
  rename: [id: string, title: string]
  remove: [id: string]
  close: []
}>()

const groups = computed(() => groupByDate(props.items, new Date()))
const menu = ref<string | null>(null)
const renaming = ref<string | null>(null)
const title = ref('')
const confirming = ref<string | null>(null)

let timer: ReturnType<typeof setTimeout> | undefined
function onSearch(e: Event) {
  const q = (e.target as HTMLInputElement).value.trim()
  clearTimeout(timer)
  timer = setTimeout(() => emit('search', q), 300)
}
onBeforeUnmount(() => clearTimeout(timer))

function startRename(c: ConversationSummary) {
  renaming.value = c.id
  title.value = c.title
  menu.value = null
}
function saveRename(id: string) {
  const t = title.value.trim()
  renaming.value = null
  if (t) emit('rename', id, t)
}
function startDelete(id: string) {
  confirming.value = id
  menu.value = null
}
</script>

<template>
  <aside :class="['sidebar', { open }]" aria-label="Conversations">
    <button type="button" class="new" @click="emit('new')">+ New chat</button>
    <label for="chat-search" class="sr-only">Search chats</label>
    <input id="chat-search" type="search" placeholder="Search chats…" @input="onSearch" />
    <nav>
      <section v-for="g in groups" :key="g.label">
        <h3>{{ g.label }}</h3>
        <div v-for="c in g.items" :key="c.id" :class="['item', { active: c.id === currentId }]">
          <input
            v-if="renaming === c.id"
            v-model="title"
            aria-label="Conversation title"
            @keydown.enter="saveRename(c.id)"
            @keydown.esc="renaming = null"
            @blur="saveRename(c.id)"
          />
          <span v-else-if="confirming === c.id" class="confirm">
            Delete?
            <button type="button" @click="emit('remove', c.id); confirming = null">Yes</button>
            <button type="button" @click="confirming = null">No</button>
          </span>
          <template v-else>
            <button type="button" class="title" @click="emit('select', c.id)">
              {{ c.title }}
              <span v-if="c.streaming" class="spinner" role="status" aria-label="Replying">⟳</span>
            </button>
            <button
              type="button"
              class="more"
              :aria-label="`Actions for ${c.title}`"
              aria-haspopup="menu"
              :aria-expanded="menu === c.id"
              @click="menu = menu === c.id ? null : c.id"
            >
              ⋯
            </button>
            <div v-if="menu === c.id" class="menu" role="menu">
              <button type="button" role="menuitem" @click="startRename(c)">Rename</button>
              <button type="button" role="menuitem" @click="startDelete(c.id)">Delete</button>
            </div>
          </template>
        </div>
      </section>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding-right: 0.75rem;
  overflow-y: auto;
}
nav {
  display: block;
  padding: 0;
}
h3 {
  font-size: 0.75rem;
  opacity: 0.6;
  margin: 0.75rem 0 0.25rem;
  font-weight: normal;
}
.item {
  position: relative;
  display: flex;
  align-items: center;
  border-radius: 6px;
}
.item.active {
  background: rgba(127, 127, 127, 0.18);
}
.item button {
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  padding: 0.4rem 0.4rem;
}
.title {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spinner {
  display: inline-block;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.menu {
  position: absolute;
  right: 0;
  top: 100%;
  z-index: 5;
  display: flex;
  flex-direction: column;
  background: Canvas;
  border: 1px solid rgba(127, 127, 127, 0.4);
  border-radius: 8px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
@media (max-width: 767px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 10;
    padding: 1rem;
    background: Canvas;
    transform: translateX(-100%);
    transition: transform 0.2s;
  }
  .sidebar.open {
    transform: none;
  }
}
</style>
