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
    const ok = await run(async () => {
      try {
        household.value = await getHousehold()
      } catch (e) {
        // 409 no_household: the person simply has none yet. Any other 409 is a real error.
        if (e instanceof ApiError && e.status === 409 && e.message === 'no_household') {
          household.value = null
        } else throw e
      }
      loaded.value = true
      return true
    })
    return ok === true
  }

  const reload = () =>
    run(async () => {
      household.value = await getHousehold()
      return true
    })

  async function create(name: string) {
    return run(async () => {
      household.value = await createHousehold(name)
      return true
    })
  }

  async function join(token: string) {
    return run(async () => {
      household.value = await joinHousehold(token)
      return true
    })
  }

  async function remove() {
    return run(async () => {
      await deleteHousehold()
      household.value = null
      return true
    })
  }

  async function leave() {
    return run(async () => {
      await leaveHousehold()
      household.value = null
      return true
    })
  }

  async function invite(role: 'member' | 'viewer'): Promise<Invitation | undefined> {
    return run(() => createInvitation(role))
  }

  // The reload only follows a mutation that worked, so it never wipes that mutation's error.
  async function setRole(sub: string, role: 'member' | 'viewer') {
    const ok = await run(async () => (await setMemberRole(sub, role), true))
    if (ok) await reload()
    return ok
  }

  async function removeFromHousehold(sub: string) {
    const ok = await run(async () => (await removeMember(sub), true))
    if (ok) await reload()
    return ok
  }

  return {
    household, loaded, error, loading, canWrite, isOwner,
    load, create, join, remove, leave, invite, setRole, removeMember: removeFromHousehold,
  }
})
