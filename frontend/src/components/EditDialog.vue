<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { updateFile, type HomeFile } from '@/api/files'
import { createFolder, updateFolder, type Folder } from '@/api/folders'
import { useFilesStore } from '@/stores/files'

// One of three modes: `file` = edit it, `folder` = edit it, neither = new folder in `parentId`.
const props = defineProps<{ file?: HomeFile; folder?: Folder; parentId?: string | null }>()
const emit = defineEmits<{ saved: []; close: [] }>()

const store = useFilesStore()
const dialog = ref<HTMLDialogElement>()
const name = ref(props.file?.name ?? props.folder?.name ?? '')
const description = ref(props.file?.description ?? '')
const tags = ref(props.file?.tags.join(', ') ?? '')
const target = ref<string | null>(
  props.file ? props.file.folder_id : props.folder ? props.folder.parent_id : (props.parentId ?? null),
)
const error = ref<string | null>(null)

const title = props.file ? 'Edit file' : props.folder ? 'Edit folder' : 'New folder'
const label = (id: string) => store.pathOf(id).map((f) => f.name).join(' › ')

// BR-5: a folder cannot move into itself or anywhere below it.
const choices = computed(() =>
  store.folders.filter((f) => !props.folder || !store.pathOf(f.id).some((a) => a.id === props.folder!.id)),
)

onMounted(() => dialog.value?.showModal())

async function save() {
  error.value = null
  try {
    if (props.file) {
      await updateFile(props.file.id, {
        name: name.value,
        description: description.value,
        tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        folder_id: target.value,
      })
    } else if (props.folder) {
      await updateFolder(props.folder.id, { name: name.value, parent_id: target.value })
    } else {
      await createFolder({ name: name.value, parent_id: target.value })
    }
    emit('saved')
  } catch (e) {
    // A 409 (name taken, cycle) or 422 is the user's to fix: keep the dialog open.
    error.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <dialog ref="dialog" @close="emit('close')">
    <form @submit.prevent="save">
      <h2>{{ title }}</h2>
      <label>Name <input v-model="name" name="name" required maxlength="255" /></label>
      <template v-if="file">
        <label>Description <textarea v-model="description" maxlength="2000" /></label>
        <label>Tags <input v-model="tags" name="tags" placeholder="comma, separated" /></label>
      </template>
      <label>
        {{ file ? 'Folder' : 'Parent' }}
        <select v-model="target">
          <option :value="null">Home</option>
          <option v-for="f in choices" :key="f.id" :value="f.id">{{ label(f.id) }}</option>
        </select>
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="buttons">
        <button type="submit">Save</button>
        <button type="button" @click="dialog?.close()">Cancel</button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
form {
  display: grid;
  gap: 0.6rem;
  min-width: 20rem;
}

label {
  display: grid;
  gap: 0.2rem;
}

.buttons {
  display: flex;
  gap: 0.5rem;
}

.error {
  color: #e06c75;
}
</style>
