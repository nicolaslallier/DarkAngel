import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as api from '@/api/chat'
import type { Conversation, ConversationSummary, ModelInfo, StreamEvent } from '@/api/chat'

const MODEL_KEY = 'darkangel.chat.model'

function readStoredModel(): string | null {
  try {
    return localStorage.getItem(MODEL_KEY)
  } catch {
    return null
  }
}

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError'
const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<ConversationSummary[]>([])
  const current = ref<Conversation | null>(null)
  const models = ref<ModelInfo[]>([])
  const model = ref('')
  const streaming = ref(false)
  const progress = ref<{ processed: number; total: number } | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // Identity of the stream being followed: events from any other controller are dropped.
  let ctrl: AbortController | null = null
  let openSeq = 0

  async function loadConversations(q?: string) {
    try {
      conversations.value = await api.listConversations(q)
    } catch (e) {
      error.value = message(e)
    }
  }

  async function loadModels() {
    try {
      models.value = await api.listModels()
    } catch (e) {
      error.value = message(e)
      return
    }
    const stored = readStoredModel()
    model.value =
      models.value.find((m) => m.id === stored)?.id ??
      models.value.find((m) => m.loaded === true)?.id ??
      models.value[0]?.id ??
      ''
  }

  function selectModel(id: string) {
    model.value = id
    try {
      localStorage.setItem(MODEL_KEY, id)
    } catch {
      // storage unavailable: the choice just lasts for this page load
    }
  }

  function abortStream() {
    openSeq++ // invalidates any in-flight open()/reattach() refetch
    loading.value = false
    ctrl?.abort()
    ctrl = null
    streaming.value = false
    progress.value = null
  }

  // The streaming assistant message is always the last one.
  const assistant = () => {
    const last = current.value?.messages.at(-1)
    return last?.role === 'assistant' ? last : undefined
  }

  function apply(ev: StreamEvent) {
    switch (ev.event) {
      case 'start': {
        const { conversation, user_message, assistant_message_id } = ev.data
        if (current.value?.id !== conversation.id) {
          current.value = { id: conversation.id, title: conversation.title, streaming: true, messages: [] }
        }
        const c = current.value
        if (user_message) c.messages.push(user_message)
        c.messages.push({
          id: assistant_message_id,
          role: 'assistant',
          content: '',
          reasoning: null,
          model: model.value,
          status: 'streaming',
          error: null,
          created_at: new Date().toISOString(),
        })
        break
      }
      case 'snapshot': {
        const m = assistant()
        if (m) {
          m.content = ev.data.content
          m.reasoning = ev.data.reasoning || null
        }
        break
      }
      case 'progress':
        progress.value = ev.data
        break
      case 'reasoning': {
        const m = assistant()
        if (m) m.reasoning = (m.reasoning ?? '') + ev.data.text
        break
      }
      case 'content': {
        const m = assistant()
        if (m) m.content += ev.data.text
        break
      }
      case 'title': {
        if (current.value) {
          current.value.title = ev.data.title
          const entry = conversations.value.find((c) => c.id === current.value?.id)
          if (entry) entry.title = ev.data.title
        }
        break
      }
      case 'error':
        error.value = ev.data.message
        streaming.value = false
        break
      case 'done': {
        const msgs = current.value?.messages
        if (msgs) {
          const i = msgs.findIndex((m) => m.id === ev.data.message.id)
          if (i >= 0) msgs[i] = ev.data.message
          else msgs.push(ev.data.message)
        }
        streaming.value = false
        progress.value = null
        void loadConversations()
        break
      }
    }
  }

  // Drains `gen`; returns how many events were applied (0 = nothing to follow).
  async function follow(gen: AsyncGenerator<StreamEvent>, mine: AbortController) {
    let n = 0
    try {
      for await (const ev of gen) {
        if (ctrl !== mine) return n
        n++
        apply(ev)
      }
    } catch (e) {
      if (ctrl === mine && !isAbort(e)) error.value = message(e)
    } finally {
      if (ctrl === mine) {
        ctrl = null
        streaming.value = false
        progress.value = null
      }
    }
    return n
  }

  function begin() {
    abortStream()
    error.value = null
    streaming.value = true
    ctrl = new AbortController()
    return ctrl
  }

  async function reattach(id: string) {
    const mine = begin() // bumps openSeq, so read it after
    const seq = openSeq
    const n = await follow(api.attach(id, mine.signal), mine)
    // Nothing to follow: the reply finished between the fetch and the attach.
    if (n === 0 && seq === openSeq && ctrl === null && !error.value) {
      try {
        const fresh = await api.getConversation(id)
        if (seq === openSeq) current.value = fresh
      } catch (e) {
        if (seq === openSeq) error.value = message(e)
      }
    }
  }

  async function open(id: string | null) {
    abortStream()
    error.value = null
    const seq = ++openSeq
    if (id === null) {
      current.value = null
      return
    }
    loading.value = true
    try {
      const c = await api.getConversation(id)
      if (seq !== openSeq) return
      current.value = c
      if (c.streaming) void reattach(id) // not awaited: open() returns once the page is usable
    } catch (e) {
      if (seq === openSeq) error.value = message(e)
    } finally {
      if (seq === openSeq) loading.value = false
    }
  }

  // A request that failed before `start` after we trimmed messages locally: match the server again.
  async function resync(id: string, n: number, mine: AbortController) {
    if (n > 0 || mine.signal.aborted || ctrl !== null) return
    const seq = openSeq
    try {
      const fresh = await api.getConversation(id)
      if (seq === openSeq) current.value = fresh
    } catch {
      // keep the original error
    }
  }

  async function send(content: string, replaceFrom?: string) {
    const mine = begin()
    const c = current.value
    if (c && replaceFrom) {
      const i = c.messages.findIndex((m) => m.id === replaceFrom)
      if (i >= 0) c.messages.splice(i)
    }
    const body = { content, model: model.value, replace_from: replaceFrom }
    const n = await follow(api.sendMessage(c?.id ?? 'new', body, mine.signal), mine)
    if (c) await resync(c.id, n, mine)
  }

  async function regenerate() {
    if (!current.value) return
    const mine = begin()
    const c = current.value
    if (c.messages.at(-1)?.role === 'assistant') c.messages.pop()
    const n = await follow(api.regenerate(c.id, model.value, mine.signal), mine)
    await resync(c.id, n, mine)
  }

  async function stop() {
    if (!current.value) return
    try {
      await api.stopReply(current.value.id) // the stream stays open and ends with `done`
    } catch (e) {
      error.value = message(e)
    }
  }

  async function rename(id: string, title: string) {
    try {
      const s = await api.renameConversation(id, title)
      const entry = conversations.value.find((c) => c.id === id)
      if (entry) entry.title = s.title
      if (current.value?.id === id) current.value.title = s.title
    } catch (e) {
      error.value = message(e)
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteConversation(id)
    } catch (e) {
      error.value = message(e)
      return
    }
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (current.value?.id === id) {
      abortStream()
      current.value = null
    }
  }

  function leave() {
    abortStream()
  }

  return {
    conversations, current, models, model, streaming, progress, loading, error,
    loadConversations, loadModels, open, send, regenerate, stop, rename, remove, leave, selectModel,
  }
})
