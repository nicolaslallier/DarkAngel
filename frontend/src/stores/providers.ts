import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  createProvider,
  createService,
  deleteProvider,
  deleteService,
  listProviders,
  updateProvider,
  updateService,
  type Provider,
  type ProviderInput,
  type ServiceInput,
} from '@/api/providers'

export const useProvidersStore = defineStore('providers', () => {
  const providers = ref<Provider[]>([])
  const error = ref<string | null>(null)
  const loading = ref(false)

  // For a <select>: live services only, labelled with their provider.
  const serviceOptions = computed(() =>
    providers.value.flatMap((p) =>
      p.services.filter((s) => !s.archived).map((s) => ({ id: s.id, label: `${p.name} — ${s.name}` })),
    ),
  )

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    loading.value = true
    error.value = null
    try {
      return await action()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return undefined
    } finally {
      loading.value = false
    }
  }

  // No optimistic updates: every mutation reloads the list.
  async function mutate<T>(action: () => Promise<T>): Promise<T | undefined> {
    const result = await run(async () => {
      const value = await action()
      providers.value = await listProviders()
      return value
    })
    return result
  }

  const load = () => run(async () => void (providers.value = await listProviders()))
  const create = (input: ProviderInput) => mutate(() => createProvider(input))
  const update = (id: string, patch: Partial<ProviderInput>) => mutate(() => updateProvider(id, patch))
  const remove = (id: string) => mutate(() => deleteProvider(id))
  const addService = (providerId: string, input: ServiceInput) =>
    mutate(() => createService(providerId, input))
  const changeService = (id: string, patch: Partial<ServiceInput> & { archived?: boolean }) =>
    mutate(() => updateService(id, patch))
  const removeService = (id: string) => mutate(() => deleteService(id))

  return {
    providers, error, loading, serviceOptions,
    load,
    createProvider: create,
    updateProvider: update,
    deleteProvider: remove,
    createService: addService,
    updateService: changeService,
    deleteService: removeService,
  }
})
