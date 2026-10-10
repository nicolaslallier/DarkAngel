<script setup lang="ts">
import { reactive, watch } from 'vue'

import type { Service, ServiceInput } from '@/api/providers'
import { blankToNull } from '@/format'

const props = defineProps<{ initial?: Partial<Service>; submitLabel: string }>()
const emit = defineEmits<{ submit: [input: ServiceInput] }>()

const CATEGORIES = ['electricity', 'internet', 'mobile', 'tv', 'water', 'gas', 'insurance', 'other']

const form = reactive({
  name: '',
  category: '',
  account_number: '',
  contract_start: '',
  contract_end: '',
  renewal_reminder_days: '' as string | number,
  expected_monthly_cost: '' as string | number,
  auto_pay: false,
  alert_threshold_pct: 20 as string | number,
})

function fill(service: Partial<Service> = {}) {
  form.name = service.name ?? ''
  form.category = service.category ?? ''
  form.account_number = service.account_number ?? ''
  form.contract_start = service.contract_start ?? ''
  form.contract_end = service.contract_end ?? ''
  form.renewal_reminder_days = service.renewal_reminder_days ?? ''
  form.expected_monthly_cost = service.expected_monthly_cost ?? ''
  form.auto_pay = service.auto_pay ?? false
  form.alert_threshold_pct = service.alert_threshold_pct ?? 20
}

watch(() => props.initial, fill, { immediate: true })

function submit() {
  if (!form.name.trim() || !form.category.trim()) return
  const reminder = blankToNull(form.renewal_reminder_days)
  emit('submit', {
    name: form.name.trim(),
    category: form.category.trim(),
    account_number: blankToNull(form.account_number),
    contract_start: blankToNull(form.contract_start),
    contract_end: blankToNull(form.contract_end),
    renewal_reminder_days: reminder === null ? null : Number(reminder),
    expected_monthly_cost: blankToNull(form.expected_monthly_cost),
    auto_pay: form.auto_pay,
    alert_threshold_pct: Number(blankToNull(form.alert_threshold_pct) ?? 20),
  })
}
</script>

<template>
  <form @submit.prevent="submit">
    <label>Name <input v-model="form.name" name="name" required maxlength="200" /></label>
    <label>
      Category
      <input v-model="form.category" name="category" list="service-categories" required />
      <datalist id="service-categories">
        <option v-for="c in CATEGORIES" :key="c" :value="c" />
      </datalist>
    </label>
    <label>Account number <input v-model="form.account_number" name="account_number" /></label>
    <label>Contract start <input v-model="form.contract_start" name="contract_start" type="date" /></label>
    <label>Contract end <input v-model="form.contract_end" name="contract_end" type="date" /></label>
    <label>
      Remind me of the renewal (days before)
      <input v-model="form.renewal_reminder_days" name="renewal_reminder_days" type="number" min="0" max="365" />
    </label>
    <label>
      Expected monthly cost
      <input v-model="form.expected_monthly_cost" name="expected_monthly_cost" type="number" min="0" step="0.01" />
    </label>
    <label>
      Alert when a bill is above the usual by (%)
      <input v-model="form.alert_threshold_pct" name="alert_threshold_pct" type="number" min="0" max="1000" />
    </label>
    <label>
      <input v-model="form.auto_pay" name="auto_pay" type="checkbox" />
      Paid automatically (pre-authorised debit)
    </label>
    <button type="submit">{{ submitLabel }}</button>
  </form>
</template>

<style scoped>
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
