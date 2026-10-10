import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError } from '@/api/client'
import {
  createHousehold,
  createInvitation,
  deleteHousehold,
  getHousehold,
  joinHousehold,
  leaveHousehold,
  removeMember,
  setMemberRole,
  type Household,
  type Invitation,
} from '@/api/household'

export const useHouseholdStore = defineStore('household', () => {
  const household = ref<Household | null>(null)
  // False until the first load() settles: "no household" is only true after that.
  const loaded = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(false)

  const canWrite = computed(() => household.value !== null && household.value.role !== 'viewer')
  const isOwner = computed(() => household.value?.role === 'owner')

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

  async function load() {
    await run(async () => {
      try {
        household.value = await getHousehold()
      } catch (e) {
        // 409 no_household: the person simply has none yet.
        if (e instanceof ApiError && e.status === 409) household.value = null
        else throw e
      }
      loaded.value = true
    })
  }

  const reload = () => run(async () => void (household.value = await getHousehold()))

  async function create(name: string) {
    return run(async () => void (household.value = await createHousehold(name)))
  }

  async function join(token: string) {
    return run(async () => void (household.value = await joinHousehold(token)))
  }

  async function remove() {
    return run(async () => {
      await deleteHousehold()
      household.value = null
    })
  }

  async function leave() {
    return run(async () => {
      await leaveHousehold()
      household.value = null
    })
  }

  async function invite(role: 'member' | 'viewer'): Promise<Invitation | undefined> {
    return run(() => createInvitation(role))
  }

  async function setRole(sub: string, role: 'member' | 'viewer') {
    await run(() => setMemberRole(sub, role))
    await reload()
  }

  async function removeFromHousehold(sub: string) {
    await run(() => removeMember(sub))
    await reload()
  }

  return {
    household, loaded, error, loading, canWrite, isOwner,
    load, create, join, remove, leave, invite, setRole, removeMember: removeFromHousehold,
  }
})
