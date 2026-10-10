<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { invoicePdf, type Invoice, type InvoiceStatus } from '@/api/invoices'
import { blankToNull } from '@/format'
import { useHouseholdStore } from '@/stores/household'
import { useInvoicesStore } from '@/stores/invoices'
import { useProvidersStore } from '@/stores/providers'

const household = useHouseholdStore()
const providers = useProvidersStore()
const invoices = useInvoicesStore()

const selectedId = ref<string | null>(null)
const pdfUrl = ref<string | null>(null)
const pdfMissing = ref(false)
const form = reactive({
  service_id: '',
  total: '' as string | number,
  due_on: '',
  issued_on: '',
  period_start: '',
  period_end: '',
  invoice_number: '',
  consumption_qty: '' as string | number,
  consumption_unit: '',
  taxes: [] as { name: string; amount: string | number }[],
})

const current = computed(() => invoices.toReview.find((i) => i.id === selectedId.value) ?? null)
const reading = computed(() => current.value?.status === 'queued' || current.value?.status === 'extracting')
const candidates = computed(() => current.value?.extraction?.candidates ?? null)

function fill(invoice: Invoice | null) {
  const raw = invoice?.extraction?.raw
  form.service_id = invoice?.service_id ?? invoice?.extraction?.candidates?.service?.id ?? ''
  form.total = invoice?.total ?? ''
  form.due_on = invoice?.due_on ?? ''
  form.issued_on = invoice?.issued_on ?? ''
  form.period_start = invoice?.period_start ?? ''
  form.period_end = invoice?.period_end ?? ''
  form.invoice_number = invoice?.invoice_number ?? ''
  form.consumption_qty = invoice?.consumption_qty ?? ''
  form.consumption_unit = invoice?.consumption_unit ?? ''
  form.taxes = (raw?.taxes ?? []).map((t) => ({ name: t.name, amount: t.amount }))
}

async function showPdf(invoice: Invoice | null) {
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
  pdfUrl.value = null
  pdfMissing.value = false
  if (!invoice) return
  if (!invoice.has_pdf) {
    pdfMissing.value = true
    return
  }
  try {
    pdfUrl.value = URL.createObjectURL(await invoicePdf(invoice.id))
  } catch {
    pdfMissing.value = true
  }
}

// Only a change of selection refills the form: reloading the list must not
// wipe what the person is typing.
watch(selectedId, () => {
  fill(current.value)
  showPdf(current.value)
})

onMounted(async () => {
  await household.load()
  if (!household.household) return
  await Promise.all([providers.load(), invoices.loadAll()])
  selectedId.value = invoices.toReview[0]?.id ?? null
})

onBeforeUnmount(() => {
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
})

function selectNext() {
  selectedId.value = invoices.toReview[0]?.id ?? null
}

async function validate() {
  if (!current.value) return
  const taxes = form.taxes
    .filter((t) => t.name.trim() && blankToNull(t.amount) !== null)
    .map((t) => ({ name: t.name.trim(), amount: String(t.amount).trim() }))
  const done = await invoices.validate(current.value.id, {
    service_id: form.service_id,
    total: String(form.total).trim(),
    due_on: form.due_on,
    issued_on: blankToNull(form.issued_on),
    period_start: blankToNull(form.period_start),
    period_end: blankToNull(form.period_end),
    invoice_number: blankToNull(form.invoice_number),
    consumption_qty: blankToNull(form.consumption_qty),
    consumption_unit: blankToNull(form.consumption_unit),
    taxes,
  })
  if (done) selectNext()
}

async function remove() {
  if (!current.value || !confirm('Delete this invoice? Its PDF stays in Files.')) return
  if (await invoices.remove(current.value.id)) selectNext()
}

function label(invoice: Invoice): string {
  const states: Partial<Record<InvoiceStatus, string>> = {
    queued: 'waiting',
    extracting: 'reading…',
    failed: 'could not read',
  }
  return `${invoice.invoice_number ?? 'Invoice'} — ${states[invoice.status] ?? 'to review'}`
}
</script>

<template>
  <section>
    <h1>Invoices to review</h1>
    <p v-if="invoices.error && !invoices.duplicateOf" class="error" role="alert">{{ invoices.error }}</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> first.
    </p>

    <p v-else-if="household.household && !invoices.toReview.length && !invoices.loading">
      Nothing to review. <RouterLink to="/providers">Back to providers</RouterLink>
    </p>

    <div v-if="invoices.toReview.length" class="layout">
      <ul class="queue">
        <li v-for="i in invoices.toReview" :key="i.id">
          <button
            type="button"
            data-test="queue-item"
            :class="{ active: i.id === selectedId }"
            @click="selectedId = i.id"
          >
            {{ label(i) }}
          </button>
        </li>
      </ul>

      <div v-if="current">
        <p v-if="reading">
          Still being read.
          <button type="button" @click="invoices.loadAll()">Refresh</button>
        </p>
        <p v-else-if="current.status === 'failed'" class="error">
          Could not read this invoice ({{ current.error }}). Fill it in by hand.
        </p>

        <p v-if="candidates?.new_provider" data-test="new-provider">
          This looks like a provider you do not have yet: <strong>{{ candidates.new_provider }}</strong>
          <template v-if="candidates.new_service"> ({{ candidates.new_service }})</template>.
          <RouterLink to="/providers">Create it</RouterLink>, then reload this page.
        </p>

        <p v-if="pdfMissing" data-test="pdf-missing">PDF missing: the file was deleted.</p>
        <iframe v-else-if="pdfUrl" :src="pdfUrl" title="Invoice PDF" />

        <p v-if="invoices.duplicateOf" data-test="duplicate" class="error">
          This number is already recorded for that service (invoice {{ invoices.duplicateOf }}).
        </p>

        <form v-if="household.canWrite" @submit.prevent="validate">
          <label>
            Service
            <select v-model="form.service_id" name="service_id" required>
              <option value="" disabled>Choose a service</option>
              <option v-for="o in providers.serviceOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
            </select>
          </label>
          <label>Total <input v-model="form.total" name="total" type="number" step="0.01" min="0" required /></label>
          <label>Due <input v-model="form.due_on" name="due_on" type="date" required /></label>
          <label>Issued <input v-model="form.issued_on" name="issued_on" type="date" /></label>
          <label>Period start <input v-model="form.period_start" name="period_start" type="date" /></label>
          <label>Period end <input v-model="form.period_end" name="period_end" type="date" /></label>
          <label>Invoice number <input v-model="form.invoice_number" name="invoice_number" /></label>
          <label>
            Consumption
            <input v-model="form.consumption_qty" name="consumption_qty" type="number" step="0.001" min="0" />
            <input v-model="form.consumption_unit" name="consumption_unit" aria-label="Consumption unit" placeholder="kWh, GB…" />
          </label>

          <fieldset>
            <legend>Taxes</legend>
            <div v-for="(tax, index) in form.taxes" :key="index" data-test="tax">
              <input v-model="tax.name" aria-label="Tax name" placeholder="Name" />
              <input v-model="tax.amount" aria-label="Tax amount" type="number" step="0.01" min="0" placeholder="Amount" />
              <button type="button" @click="form.taxes.splice(index, 1)">Remove</button>
            </div>
            <button type="button" @click="form.taxes.push({ name: '', amount: '' })">Add a tax</button>
          </fieldset>

          <button type="submit" :disabled="reading">Validate</button>
          <button type="button" data-test="delete" @click="remove">Delete</button>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

.layout {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) 1fr;
  gap: 1rem;
}

.queue {
  list-style: none;
  padding: 0;
}

.queue button.active {
  font-weight: 600;
}

iframe {
  width: 100%;
  height: 24rem;
  border: 1px solid #8884;
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
