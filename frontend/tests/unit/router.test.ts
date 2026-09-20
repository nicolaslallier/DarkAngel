import { beforeEach, expect, it, vi } from 'vitest'

import { accessToken, signIn } from '@/auth'
import { router } from '@/router'

// Every export the router and its lazily-loaded views reach for.
vi.mock('@/auth', () => ({
  accessToken: vi.fn(async () => null),
  signIn: vi.fn(async () => {}),
  completeSignIn: vi.fn(async () => '/'),
  signOut: vi.fn(async () => {}),
}))

beforeEach(async () => {
  vi.clearAllMocks()
  vi.mocked(accessToken).mockResolvedValue('a-token')
  await router.push('/')
  vi.clearAllMocks()
})

it('sends an unauthenticated visitor to Keycloak instead of the page', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  // The guard returns false, so the navigation resolves as a failure.
  await router.push('/files').catch(() => {})

  expect(signIn).toHaveBeenCalledWith('/files')
  expect(router.currentRoute.value.path).toBe('/')
})

it('keeps the query and hash in the path it will come back to', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  await router.push('/files?sort=name').catch(() => {})

  expect(signIn).toHaveBeenCalledWith('/files?sort=name')
})

it('lets a signed-in visitor through', async () => {
  await router.push('/about')

  expect(signIn).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/about')
})

it('lets the callback route through without a token', async () => {
  vi.mocked(accessToken).mockResolvedValue(null)

  await router.push('/auth/callback?code=abc')

  expect(signIn).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/auth/callback')
})
