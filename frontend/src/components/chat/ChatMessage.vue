<script setup lang="ts">
import { computed, onMounted, onUpdated, ref } from 'vue'

import type { ChatMessage } from '@/api/chat'
import { renderMarkdown } from '@/markdown'

const props = defineProps<{ message: ChatMessage; last: boolean; streaming: boolean }>()
const emit = defineEmits<{ edit: [content: string]; regenerate: [] }>()

const body = ref<HTMLElement | null>(null)
const editing = ref(false)
const draft = ref('')

const isUser = computed(() => props.message.role === 'user')
const html = computed(() => renderMarkdown(props.message.content))
const thinkingOpen = computed(() => props.message.status === 'streaming' && !props.message.content)
const noAnswer = computed(() => !isUser.value && props.message.status === 'complete' && !props.message.content)
const stopped = computed(() => props.message.status === 'stopped' && !props.message.content)
const failed = computed(() => props.message.status === 'error')

const copy = (t: string) => navigator.clipboard?.writeText(t)

function startEdit() {
  draft.value = props.message.content
  editing.value = true
}
function saveEdit() {
  const t = draft.value.trim()
  if (!t) return
  editing.value = false
  emit('edit', t)
}

// v-html replaces the code blocks on every update, so the copy buttons are re-added after each patch.
function decorate() {
  body.value?.querySelectorAll('pre').forEach((pre) => {
    if (pre.querySelector('.copy-code')) return
    const b = document.createElement('button')
    b.type = 'button'
    b.className = 'copy-code'
    b.textContent = 'Copy'
    b.setAttribute('aria-label', 'Copy code')
    b.addEventListener('click', () => copy(pre.querySelector('code')?.textContent ?? ''))
    pre.appendChild(b)
  })
}
onMounted(decorate)
onUpdated(decorate)
</script>

<template>
  <article :class="['msg', isUser ? 'user' : 'assistant']">
    <template v-if="isUser">
      <template v-if="editing">
        <textarea v-model="draft" rows="3" aria-label="Edit message" />
        <div class="actions">
          <button type="button" @click="saveEdit">Save</button>
          <button type="button" @click="editing = false">Cancel</button>
        </div>
      </template>
      <template v-else>
        <p class="plain">{{ message.content }}</p>
        <div class="actions">
          <button type="button" :disabled="streaming" @click="startEdit">Edit</button>
          <button type="button" @click="copy(message.content)">Copy</button>
        </div>
      </template>
    </template>
    <template v-else>
      <div class="model">{{ message.model }}</div>
      <details v-if="message.reasoning" class="thinking" :open="thinkingOpen">
        <summary>Thinking…</summary>
        <p class="plain">{{ message.reasoning }}</p>
      </details>
      <!-- the only v-html in the app: renderMarkdown output is DOMPurify-sanitised -->
      <div ref="body" class="md" v-html="html" />
      <p v-if="noAnswer" class="note">No answer</p>
      <p v-if="stopped" class="note">Stopped</p>
      <p v-if="failed" class="note error" role="alert">
        {{ message.error || 'Something went wrong.' }}
        <button type="button" @click="emit('regenerate')">Retry</button>
      </p>
      <div v-if="message.status !== 'streaming'" class="actions">
        <button type="button" @click="copy(message.content)">Copy</button>
        <button v-if="last && !failed" type="button" :disabled="streaming" @click="emit('regenerate')">
          Regenerate
        </button>
      </div>
    </template>
  </article>
</template>

<style scoped>
.msg.user {
  align-self: flex-end;
  max-width: 75%;
  background: rgba(127, 127, 127, 0.18);
  padding: 0.6rem 0.9rem;
  border-radius: 14px;
}
.plain {
  margin: 0;
  white-space: pre-wrap;
}
.model {
  font-size: 0.75rem;
  opacity: 0.6;
  margin-bottom: 0.25rem;
}
.thinking {
  opacity: 0.75;
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
  font-size: 0.75rem;
  margin-top: 0.25rem;
}
.actions button,
.note button {
  background: none;
  border: 0;
  color: inherit;
  opacity: 0.7;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
}
.note {
  opacity: 0.7;
  font-style: italic;
}
.note.error {
  color: #d33;
  opacity: 1;
}
.md :deep(pre) {
  position: relative;
  background: rgba(0, 0, 0, 0.35);
  padding: 0.5rem;
  border-radius: 6px;
  overflow-x: auto;
}
.md :deep(.copy-code) {
  position: absolute;
  top: 0.25rem;
  right: 0.25rem;
  font-size: 0.7rem;
}
</style>
