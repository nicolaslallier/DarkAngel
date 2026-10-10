<script setup lang="ts">
import { onMounted } from 'vue'

import { age, bytes, money } from '@/format'
import { useHealthStore } from '@/stores/health'
import { useHouseholdStore } from '@/stores/household'
import { useInfraStore } from '@/stores/infra'
import { useInvoicesStore } from '@/stores/invoices'
import { useMeStore } from '@/stores/me'

const health = useHealthStore()
const me = useMeStore()
const household = useHouseholdStore()
const infra = useInfraStore()
const invoices = useInvoicesStore()

onMounted(async () => {
  void health.load()
  void me.load()
  await household.load()
  // The infra route and the invoice routes both need a household.
  if (household.household) await Promise.all([infra.load(), invoices.loadDashboard()])
})
</script>

<template>
  <section>
    <h1>DarkAngel</h1>
    <p v-if="health.error" class="error">Backend unreachable: {{ health.error }}</p>
    <p v-if="me.error" class="error">API rejected the session: {{ me.error }}</p>
    <p v-else-if="me.me">
      Signed in as <strong>{{ me.me.username ?? me.me.sub }}</strong>
    </p>

    <p v-if="household.error" role="alert" class="error">{{ household.error }}</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to see the dashboard.
    </p>

    <div v-else-if="household.household" class="grid">
      <article>
        <h2>Backups</h2>
        <p v-if="infra.error" class="error" data-test="infra-error">{{ infra.error }}</p>
        <p v-else-if="!infra.backups.length">Nothing collected yet.</p>
        <p v-if="infra.collectorStale" class="error" data-test="collector-stale">
          Data is more than 15 minutes old: is the collector running?
        </p>
        <ul>
          <li
            v-for="b in infra.backups"
            :key="b.instance"
            data-test="backup"
            :class="{ bad: b.stale }"
          >
            <strong>{{ b.instance }}</strong>
            <template v-if="b.last_backup_at">
              {{ age(b.age_hours) }} ago · {{ bytes(b.size_bytes) }}
            </template>
            <template v-else>No backup</template>
          </li>
        </ul>
      </article>

      <article>
        <h2>Infra</h2>
        <p v-if="infra.error" class="error">{{ infra.error }}</p>
        <p v-else-if="!infra.instances.length">Nothing collected yet.</p>
        <ul>
          <li
            v-for="i in infra.instances"
            :key="i.name"
            data-test="instance"
            :class="{ bad: !i.reachable }"
          >
            <strong>{{ i.name }}</strong>
            <template v-if="i.reachable">
              v{{ i.version }} · {{ i.environments }} env · {{ i.stacks }} stacks
            </template>
            <template v-else>Unreachable: {{ i.error }}</template>
          </li>
        </ul>
      </article>

      <article>
        <h2>Invoices</h2>
        <p v-if="invoices.error" class="error">{{ invoices.error }}</p>
        <p v-if="invoices.toReview.length">
          <RouterLink to="/invoices/review">
            {{ invoices.toReview.length }} invoice{{ invoices.toReview.length > 1 ? 's' : '' }} to
            review
          </RouterLink>
        </p>
        <p v-if="!invoices.upcoming.invoices.length && !invoices.upcoming.renewals.length">
          Nothing due.
        </p>
        <ul>
          <li
            v-for="due in invoices.upcoming.invoices.slice(0, 5)"
            :key="due.invoice_id"
            data-test="due"
            :class="{ bad: due.overdue }"
          >
            {{ due.provider_name }} · {{ due.service_name }} · {{ money(due.total) }} ·
            {{ due.due_on }}
          </li>
          <li v-for="r in invoices.upcoming.renewals" :key="r.service_id" data-test="renewal">
            {{ r.provider_name }} · {{ r.service_name }} renews in {{ r.days_left }} d
          </li>
        </ul>
        <RouterLink to="/providers">All providers</RouterLink>
      </article>
    </div>
  </section>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
  gap: 1rem;
}
ul {
  padding-left: 1rem;
}
.error,
.bad {
  color: #e06c75;
}
</style>
