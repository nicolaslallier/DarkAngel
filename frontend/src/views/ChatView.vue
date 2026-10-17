<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import ChatSidebar from '@/components/chat/ChatSidebar.vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()
const route = useRoute()
const router = useRouter()
const drawer = ref(false)

const routeId = () => (route.params.id as string | undefined) ?? null
const messages = computed(() => store.current?.messages ?? [])
const waiting = computed(() => {
  const last = messages.value.at(-1)
  return store.streaming && last?.role === 'assistant' && !last.content && !last.reasoning
})

// "Waiting for the model… Ns" ticks while the first token is pending.
const seconds = ref(0)
let tick: ReturnType<typeof setInterval> | undefined
watch(waiting, (w) => {
  clearInterval(tick)
  seconds.value = 0
  if (w) tick = setInterval(() => seconds.value++, 1000)
})

onMounted(() => {
  void store.loadModels()
  void store.loadConversations()
  void store.open(routeId())
})
watch(
  () => route.params.id,
  () => {
    // The replace after a first send lands on the conversation already open: do not abort its stream.
    if (store.loading || routeId() !== (store.current?.id ?? null)) void store.open(routeId())
  },
)
// A first message creates the conversation: reflect its id in the URL.
watch(
  () => store.current?.id,
  (id) => {
    if (id && id !== routeId()) void router.replace({ name: 'chat', params: { id } })
  },
)
// Follow the bottom on open, on send, and while streaming unless the user scrolled up.
const list = ref<HTMLElement | null>(null)
let pinned = true
function onScroll() {
  const el = list.value
  if (el) pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
async function toBottom() {
  pinned = true
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
}
watch(() => store.current?.id, toBottom, { flush: 'post' })
watch(
  () => {
    const last = messages.value.at(-1)
    return `${messages.value.length}:${last?.content.length}:${last?.reasoning?.length}`
  },
  () => {
    if (pinned) void toBottom()
  },
  { flush: 'post' },
)
function send(content: string) {
  void toBottom()
  void store.send(content)
}

onBeforeUnmount(() => {
  clearInterval(tick)
  store.leave()
})

function go(id: string | null) {
  drawer.value = false
  void router.push(id ? { name: 'chat', params: { id } } : { name: 'chat' })
}
async function remove(id: string) {
  const wasOpen = store.current?.id === id
  await store.remove(id)
  if (wasOpen && !store.current) void router.replace({ name: 'chat' })
}
const edit = (id: string, content: string) => store.send(content, id)
</script>

<template>
  <div class="chat">
    <ChatSidebar
      :items="store.conversations"
      :current-id="store.current?.id ?? null"
      :open="drawer"
      @new="go(null)"
      @select="go"
      @search="store.loadConversations"
      @rename="store.rename"
      @remove="remove"
      @close="drawer = false"
    />
    <section class="conversation">
      <header>
        <button type="button" class="burger" aria-label="Conversations" @click="drawer = !drawer">☰</button>
        <b>{{ store.current?.title ?? 'New chat' }}</b>
      </header>
      <p v-if="store.loading" role="status">Loading…</p>
      <div ref="list" class="messages" @scroll.passive="onScroll">
        <ChatMessage
          v-for="(m, i) in messages"
          :key="m.id"
          :message="m"
          :last="i === messages.length - 1"
          :streaming="store.streaming"
          @edit="(c) => edit(m.id, c)"
          @regenerate="store.regenerate()"
        />
        <p v-if="waiting" class="waiting" role="status">
          {{
            store.progress
              ? `Reading your message… ${store.progress.processed}/${store.progress.total}`
              : `Waiting for the model… ${seconds}s`
          }}
        </p>
        <p v-if="store.error" class="error" role="alert">{{ store.error }}</p>
      </div>
      <ChatComposer
        :streaming="store.streaming"
        :models="store.models"
        :model="store.model"
        @send="send"
        @stop="store.stop()"
        @select="store.selectModel"
      />
    </section>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: calc(100vh - 6rem);
}
.conversation {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
header {
  border-bottom: 1px solid rgba(127, 127, 127, 0.3);
  padding-bottom: 0.5rem;
}
.burger {
  display: none;
}
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 720px;
  width: 100%;
  margin: 0 auto;
  padding: 1rem 0;
}
.waiting {
  opacity: 0.6;
}
.error {
  color: #d33;
}
@media (max-width: 767px) {
  .burger {
    display: inline-block;
  }
}
</style>
