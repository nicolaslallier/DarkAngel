<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getServiceCosts, type ServiceCosts } from '@/api/costs'
import { listInvoices, type Invoice } from '@/api/invoices'
import { getService, type Service, type ServiceInput } from '@/api/providers'
import CostChart from '@/components/CostChart.vue'
import ServiceForm from '@/components/ServiceForm.vue'
import { blankToNull, money } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const route = useRoute()
const router = useRouter()
const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const id = computed(() => String(route.params.id))
const service = ref<Service | null>(null)
const costs = ref<ServiceCosts | null>(null)
const rows = ref<Invoice[]>([])
const manual = reactive({ total: '' as string | number, due_on: '', issued_on: '', invoice_number: '' })
const missing = ref(false)
const loadError = ref<string | null>(null)
const errorText = computed(() => loadError.value ?? invoices.error ?? providers.error)

const flagged = computed(() => new Set(costs.value?.points.filter((p) => p.flagged).map((p) => p.invoice_id)))
const chart = computed(() =>
  (costs.value?.points ?? []).map((p) => ({ label: p.month, value: Number(p.total), flagged: p.flagged })),
)

async function refresh() {
  ;[rows.value, costs.value] = await Promise.all([
    listInvoices({ service_id: id.value }),
    getServiceCosts(id.value),
  ])
}

// Refresh after a write that already succeeded: a failure is shown, never thrown.
async function safeRefresh() {
  loadError.value = null
  try {
    await refresh()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(async () => {
  await household.load()
  if (!household.household) return
  try {
    service.value = await getService(id.value)
    await Promise.all([refresh(), providers.load()])
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) missing.value = true
    else loadError.value = e instanceof Error ? e.message : String(e)
  }
})

async function save(input: ServiceInput) {
  const updated = await providers.updateService(id.value, input)
  if (updated) {
    service.value = updated
    await safeRefresh()
  }
}

async function archive() {
  const updated = await providers.updateService(id.value, { archived: !service.value?.archived })
  if (updated) {
    service.value = updated
    await safeRefresh()
  }
}

async function remove() {
  if (!confirm('Delete this service?')) return
  const removed = await providers.deleteService(id.value)
  if (removed) await router.replace('/providers')
}

async function addManual() {
  const total = String(manual.total).trim()
  if (!total || !manual.due_on) return
  const created = await invoices.createManual({
    service_id: id.value,
    total,
    due_on: manual.due_on,
    issued_on: blankToNull(manual.issued_on),
    period_start: null,
    period_end: null,
    invoice_number: blankToNull(manual.invoice_number),
    consumption_qty: null,
    consumption_unit: null,
    taxes: [],
  })
  if (created) {
    Object.assign(manual, { total: '', due_on: '', issued_on: '', invoice_number: '' })
    await safeRefresh()
  }
}

async function togglePaid(invoice: Invoice) {
  if (await invoices.markPaid(invoice.id, !invoice.paid)) await safeRefresh()
}

async function removeInvoice(invoice: Invoice) {
  if (confirm('Delete this invoice? Its PDF stays in Files.') && (await invoices.remove(invoice.id))) {
    await safeRefresh()
  }
}

async function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length && (await invoices.upload(Array.from(input.files), { service_id: id.value }))) {
    await safeRefresh()
  }
  input.value = ''
}
</script>

<template>
  <section>
    <p><RouterLink to="/providers">← Providers</RouterLink></p>
    <p v-if="missing" class="error" role="alert">No such service.</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to start tracking providers.
    </p>
    <p v-if="errorText" class="error" role="alert">{{ errorText }}</p>

    <template v-if="service">
      <h1>{{ service.name }}<small v-if="service.archived"> (archived)</small></h1>
      <p>
        <RouterLink :to="`/providers/${service.provider_id}`">Provider</RouterLink> ·
        {{ service.category }}
        <template v-if="service.auto_pay"> · paid automatically</template>
      </p>

      <h2>Cost history</h2>
      <CostChart :points="chart" />
      <p v-if="costs">
        Expected {{ money(costs.expected_monthly_cost) }} a month; alert above usual by
        {{ costs.threshold_pct }}%.
      </p>

      <h2>Invoices</h2>
      <table v-if="rows.length">
        <thead>
          <tr><th>Number</th><th>Issued</th><th>Due</th><th>Total</th><th>Status</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" data-test="invoice">
            <td>{{ row.invoice_number ?? '—' }}</td>
            <td>{{ row.issued_on ?? '—' }}</td>
            <td>{{ row.due_on ?? '—' }}</td>
            <td>
              {{ money(row.total) }}
              <span v-if="flagged.has(row.id)" data-test="flag" class="flag" title="Above the usual" aria-label="Above the usual">▲</span>
            </td>
            <td>{{ row.status === 'validated' ? (row.paid ? 'Paid' : 'Unpaid') : row.status }}</td>
            <td v-if="household.canWrite">
              <button
                v-if="row.status === 'validated'"
                type="button"
                data-test="toggle-paid"
                @click="togglePaid(row)"
              >
                {{ row.paid ? 'Mark unpaid' : 'Mark paid' }}
              </button>
              <button type="button" @click="removeInvoice(row)">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else>No invoices yet.</p>

      <template v-if="household.canWrite">
        <label>
          Add invoice PDFs for this service
          <input type="file" accept="application/pdf,.pdf" multiple @change="onFiles" />
        </label>

        <h3>Add an invoice by hand</h3>
        <form data-test="manual" @submit.prevent="addManual">
          <input v-model="manual.total" name="manual-total" aria-label="Total" type="number" step="0.01" min="0" placeholder="Total" required />
          <input v-model="manual.due_on" name="manual-due" aria-label="Due date" type="date" required />
          <input v-model="manual.issued_on" name="manual-issued" aria-label="Issue date" type="date" />
          <input v-model="manual.invoice_number" name="manual-number" aria-label="Invoice number" placeholder="Invoice number" />
          <button type="submit">Add invoice</button>
        </form>

        <h2>Settings</h2>
        <ServiceForm :initial="service" submit-label="Save" @submit="save" />
        <p>
          <button type="button" @click="archive">{{ service.archived ? 'Unarchive' : 'Archive' }}</button>
          <button type="button" @click="remove">Delete</button>
          <small> A service with invoices cannot be deleted: archive it.</small>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.flag {
  color: #e06c75;
}
</style>
