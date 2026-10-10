<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import CostChart from '@/components/CostChart.vue'
import { money } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()
const newName = ref('')

const chart = computed(() =>
  invoices.monthly.map((m) => ({ label: m.month, value: Number(m.total) })),
)

onMounted(async () => {
  await household.load()
  if (household.household) await Promise.all([providers.load(), invoices.loadDashboard()])
})

async function addProvider() {
  const name = newName.value.trim()
  if (!name) return
  const created = await providers.createProvider({
    name, website: null, phone: null, email: null, notes: null,
  })
  if (created) newName.value = ''
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) await invoices.upload(Array.from(input.files), undefined)
  input.value = ''
}
</script>

<template>
  <section>
    <h1>Providers</h1>
    <p v-if="household.error" role="alert" class="error">{{ household.error }}</p>

    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to start tracking providers.
    </p>

    <template v-else-if="household.household">
      <p v-if="providers.error || invoices.error" class="error" role="alert">
        {{ providers.error ?? invoices.error }}
      </p>

      <p v-if="invoices.toReview.length">
        <RouterLink to="/invoices/review">
          {{ invoices.toReview.length }} invoice{{ invoices.toReview.length > 1 ? 's' : '' }} to review
        </RouterLink>
      </p>

      <label v-if="household.canWrite">
        Add invoice PDFs
        <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
      </label>

      <h2>Upcoming</h2>
      <p v-if="!invoices.upcoming.invoices.length && !invoices.upcoming.renewals.length">
        Nothing due.
      </p>
      <ul>
        <li
          v-for="due in invoices.upcoming.invoices"
          :key="due.invoice_id"
          data-test="due"
          :class="{ overdue: due.overdue }"
        >
          <RouterLink :to="`/services/${due.service_id}`">
            {{ due.provider_name }} — {{ due.service_name }}
          </RouterLink>
          {{ money(due.total) }} due {{ due.due_on }}<template v-if="due.overdue"> (overdue)</template>
        </li>
        <li v-for="r in invoices.upcoming.renewals" :key="r.service_id" data-test="renewal">
          <RouterLink :to="`/services/${r.service_id}`">
            {{ r.provider_name }} — {{ r.service_name }}
          </RouterLink>
          contract ends {{ r.contract_end }} ({{ r.days_left }} days)
        </li>
      </ul>

      <h2>Household spending by month</h2>
      <CostChart :points="chart" />

      <h2>All providers</h2>
      <ul>
        <li v-for="p in providers.providers" :key="p.id">
          <RouterLink :to="`/providers/${p.id}`">{{ p.name }}</RouterLink>
          <ul data-test="provider-services">
            <li v-for="s in p.services.filter((x) => !x.archived)" :key="s.id">
              <RouterLink :to="`/services/${s.id}`">{{ s.name }}</RouterLink>
            </li>
          </ul>
        </li>
      </ul>

      <form v-if="household.canWrite" @submit.prevent="addProvider">
        <input v-model="newName" aria-label="New provider" placeholder="New provider (e.g. Hydro-Québec)" maxlength="200" />
        <button type="submit">Add provider</button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.overdue {
  color: #e06c75;
  font-weight: 600;
}
</style>
