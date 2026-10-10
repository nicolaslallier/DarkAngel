import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming, type MonthlyCosts, type Upcoming } from '@/api/costs'
import {
  createManualInvoice,
  deleteInvoice,
  listInvoices,
  setPaid,
  uploadInvoices,
  validateInvoice,
  type Invoice,
  type InvoiceFields,
  type UploadTarget,
} from '@/api/invoices'

export const useInvoicesStore = defineStore('invoices', () => {
  const invoices = ref<Invoice[]>([])
  const upcoming = ref<Upcoming>({ invoices: [], renewals: [] })
  const monthly = ref<MonthlyCosts['months']>([])
  const error = ref<string | null>(null)
  // Set by a 409 on validation: the id of the invoice that already has this number.
  const duplicateOf = ref<string | null>(null)
  const loading = ref(false)

  // Everything that still needs a person: waiting, reading, readable or failed.
  const toReview = computed(() => invoices.value.filter((i) => i.status !== 'validated'))

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    loading.value = true
    error.value = null
    duplicateOf.value = null
    try {
      return await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiError && e.status === 409) {
        const detail = (e.body as { detail?: { existing_id?: string } } | null)?.detail
        duplicateOf.value = detail?.existing_id ?? null
      }
      return undefined
    } finally {
      loading.value = false
    }
  }

  function replace(updated: Invoice) {
    const index = invoices.value.findIndex((i) => i.id === updated.id)
    if (index === -1) invoices.value = [updated, ...invoices.value]
    else invoices.value.splice(index, 1, updated)
  }

  const loadAll = () => run(async () => void (invoices.value = await listInvoices()))

  const loadDashboard = () =>
    run(async () => {
      const [all, soon, months] = await Promise.all([listInvoices(), getUpcoming(), getMonthlyCosts()])
      invoices.value = all
      upcoming.value = soon
      monthly.value = months.months
    })

  const upload = (files: File[], target?: UploadTarget) =>
    run(async () => {
      const created = await uploadInvoices(files, target)
      invoices.value = [...created, ...invoices.value]
      return created
    })

  const validate = (id: string, fields: InvoiceFields) =>
    run(async () => {
      const updated = await validateInvoice(id, fields)
      replace(updated)
      return updated
    })

  const createManual = (fields: InvoiceFields) =>
    run(async () => {
      const created = await createManualInvoice(fields)
      replace(created)
      return created
    })

  const markPaid = (id: string, paid: boolean) =>
    run(async () => {
      const updated = await setPaid(id, paid)
      replace(updated)
      return updated
    })

  const remove = (id: string) =>
    run(async () => {
      await deleteInvoice(id)
      invoices.value = invoices.value.filter((i) => i.id !== id)
      return true
    })

  return {
    invoices, upcoming, monthly, error, duplicateOf, loading, toReview,
    loadAll, loadDashboard, upload, validate, createManual, markPaid, remove,
  }
})
