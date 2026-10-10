<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { ServiceInput } from '@/api/providers'
import { blankToNull } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'
import ServiceForm from '@/components/ServiceForm.vue'

const route = useRoute()
const router = useRouter()
const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const id = computed(() => String(route.params.id))
const provider = computed(() => providers.providers.find((p) => p.id === id.value) ?? null)
const form = reactive({ name: '', website: '', phone: '', email: '', notes: '' })

watch(
  provider,
  (p) => {
    form.name = p?.name ?? ''
    form.website = p?.website ?? ''
    form.phone = p?.phone ?? ''
    form.email = p?.email ?? ''
    form.notes = p?.notes ?? ''
  },
  { immediate: true },
)

const settled = ref(false)

onMounted(async () => {
  await household.load()
  if (household.household) await providers.load()
  settled.value = true
})

async function save() {
  if (!form.name.trim()) return
  await providers.updateProvider(id.value, {
    name: form.name.trim(),
    website: blankToNull(form.website),
    phone: blankToNull(form.phone),
    email: blankToNull(form.email),
    notes: blankToNull(form.notes),
  })
}

async function addService(input: ServiceInput) {
  await providers.createService(id.value, input)
}

async function remove() {
  if (!confirm('Delete this provider?')) return
  const removed = await providers.deleteProvider(id.value)
  if (removed) await router.replace('/providers')
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await invoices.upload(Array.from(input.files), { provider_id: id.value })
  input.value = ''
}
</script>

<template>
  <section>
    <p><RouterLink to="/providers">← Providers</RouterLink></p>
    <p v-if="household.error" role="alert" class="error">{{ household.error }}</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to start tracking providers.
    </p>
    <p v-if="providers.error || invoices.error" class="error" role="alert">
      {{ providers.error ?? invoices.error }}
    </p>
    <p v-else-if="!provider && settled && household.household">No such provider.</p>

    <template v-if="provider">
      <h1>{{ provider.name }}</h1>

      <form v-if="household.canWrite" @submit.prevent="save">
        <label>Name <input v-model="form.name" required maxlength="200" /></label>
        <label>Website <input v-model="form.website" maxlength="500" /></label>
        <label>Phone <input v-model="form.phone" maxlength="50" /></label>
        <label>Email <input v-model="form.email" maxlength="254" /></label>
        <label>Notes <textarea v-model="form.notes" maxlength="4000" /></label>
        <button type="submit">Save</button>
      </form>
      <dl v-else>
        <dt>Website</dt><dd>{{ provider.website ?? '—' }}</dd>
        <dt>Phone</dt><dd>{{ provider.phone ?? '—' }}</dd>
        <dt>Email</dt><dd>{{ provider.email ?? '—' }}</dd>
        <dt>Notes</dt><dd>{{ provider.notes ?? '—' }}</dd>
      </dl>

      <h2>Services</h2>
      <ul>
        <li v-for="s in provider.services" :key="s.id">
          <RouterLink :to="`/services/${s.id}`">{{ s.name }}</RouterLink>
          <small v-if="s.archived"> (archived)</small>
        </li>
      </ul>

      <template v-if="household.canWrite">
        <label>
          Add invoice PDFs for this provider
          <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
        </label>
        <h3>Add a service</h3>
        <ServiceForm submit-label="Add service" @submit="addService" />
        <p>
          <button type="button" :disabled="provider.services.length > 0" @click="remove">
            Delete provider
          </button>
          <small v-if="provider.services.length"> Remove or archive its services first.</small>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

form {
  display: grid;
  gap: 0.5rem;
  max-width: 28rem;
}

label {
  display: grid;
  gap: 0.2rem;
}
</style>
