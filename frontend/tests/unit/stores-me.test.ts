import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { fetchMe } from '@/api/me'
import { useMeStore } from '@/stores/me'

vi.mock('@/api/me', () => ({ fetchMe: vi.fn() }))

const me = { sub: 'user-1', username: 'nicolas', email: null, roles: ['ea-editor'] }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

it('load() stores the caller', async () => {
  vi.mocked(fetchMe).mockResolvedValue(me)
  const store = useMeStore()

  await store.load()

  expect(store.me).toEqual(me)
  expect(store.error).toBeNull()
})

it('load() records a 401 as an error message', async () => {
  vi.mocked(fetchMe).mockRejectedValue(new Error('GET /me failed with 401'))
  const store = useMeStore()

  await store.load()

  expect(store.me).toBeNull()
  expect(store.error).toBe('GET /me failed with 401')
  expect(store.loading).toBe(false)
})
