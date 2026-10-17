<script setup lang="ts">
import { ref } from 'vue'

import type { ModelInfo } from '@/api/chat'
import ModelPicker from '@/components/chat/ModelPicker.vue'

const props = defineProps<{ streaming: boolean; models: ModelInfo[]; model: string }>()
const emit = defineEmits<{ send: [text: string]; stop: []; select: [id: string] }>()

const text = ref('')
const picking = ref(false)

function submit() {
  if (props.streaming) return
  const t = text.value.trim()
  if (!t) return
  emit('send', t)
  text.value = ''
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <form class="composer" @submit.prevent="submit">
    <label for="chat-input" class="sr-only">Message</label>
    <textarea
      id="chat-input"
      v-model="text"
      rows="2"
      placeholder="Reply…  (Enter to send, Shift+Enter for new line)"
      @keydown="onKey"
    />
    <div class="row">
      <div class="picker">
        <button type="button" class="model-toggle" aria-haspopup="listbox" :aria-expanded="picking" @click="picking = !picking">
          {{ model || 'Model' }} ▾
        </button>
        <ModelPicker
          v-if="picking"
          class="pop"
          :models="models"
          :model="model"
          @select="(id) => { $emit('select', id); picking = false }"
        />
      </div>
      <button v-if="streaming" type="button" @click="$emit('stop')">■ Stop</button>
      <button v-else type="submit">Send</button>
    </div>
  </form>
</template>

<style scoped>
.composer {
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
  border: 1px solid rgba(127, 127, 127, 0.4);
  border-radius: 14px;
  padding: 0.6rem;
  box-sizing: border-box;
}
textarea {
  width: 100%;
  box-sizing: border-box;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: inherit;
  font: inherit;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.picker {
  position: relative;
}
.pop {
  position: absolute;
  bottom: 100%;
  left: 0;
  margin-bottom: 0.25rem;
  z-index: 5;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
