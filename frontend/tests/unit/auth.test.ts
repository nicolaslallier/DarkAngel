import { beforeEach, expect, it, vi } from 'vitest'

import { accessToken, completeSignIn, signIn, signOut } from '@/auth'

// vi.hoisted, because vi.mock's factory is lifted above the imports.
const manager = vi.hoisted(() => ({
  getUser: vi.fn(),
  signinRedirect: vi.fn(async () => {}),
  signinRedirectCallback: vi.fn(),
  signoutRedirect: vi.fn(async () => {}),
}))

// `new UserManager(options)` runs once, at module-init time, before any test
// body runs; Vitest/tinyspy does not record a call made that early against
// `vi.mocked(UserManager).mock.calls`, so the options are captured directly
// instead of read back off the mock's call history.
const construction = vi.hoisted(() => ({
  options: undefined as Record<string, unknown> | undefined,
}))

vi.mock('oidc-client-ts', () => ({
  // An arrow function isn't constructible (`new (() => manager)` throws); a
  // plain function invoked with `new` that returns an object yields that
  // object instead of `this`, so `new UserManager()` resolves to `manager`.
  UserManager: vi.fn(function UserManager(options: Record<string, unknown>) {
    construction.options = options
    return manager
  }),
  WebStorageStateStore: vi.fn(),
  InMemoryWebStorage: vi.fn(),
}))

beforeEach(() => {
  manager.getUser.mockReset()
  manager.signinRedirect.mockClear()
  manager.signinRedirectCallback.mockReset()
  manager.signoutRedirect.mockClear()
})

it('configures the darkangel-spa public client against realm ea', () => {
  expect(construction.options).toMatchObject({
    authority: 'https://keycloak.famillelallier.net/realms/ea',
    client_id: 'darkangel-spa',
    redirect_uri: 'http://localhost:3000/auth/callback',
    post_logout_redirect_uri: 'http://localhost:3000/',
    scope: 'openid profile email',
    automaticSilentRenew: true,
  })
})

it('returns the access token of a live session', async () => {
  manager.getUser.mockResolvedValue({ access_token: 'a-token', expired: false })

  await expect(accessToken()).resolves.toBe('a-token')
})

it('returns null once the token has expired', async () => {
  manager.getUser.mockResolvedValue({ access_token: 'a-token', expired: true })

  await expect(accessToken()).resolves.toBeNull()
})

it('returns null when there is no session at all', async () => {
  manager.getUser.mockResolvedValue(null)

  await expect(accessToken()).resolves.toBeNull()
})

it('carries the page to come back to through the redirect state', async () => {
  await signIn('/files')

  expect(manager.signinRedirect).toHaveBeenCalledWith({ state: '/files' })
})

it('resumes at the in-app path the state holds', async () => {
  manager.signinRedirectCallback.mockResolvedValue({ state: '/files' })

  await expect(completeSignIn()).resolves.toBe('/files')
})

it('falls back to / when the state is not an in-app path', async () => {
  // An open-redirect guard: anything that is not a local path goes home.
  manager.signinRedirectCallback.mockResolvedValue({ state: 'https://evil.example' })

  await expect(completeSignIn()).resolves.toBe('/')
})

it('falls back to / when there is no state', async () => {
  manager.signinRedirectCallback.mockResolvedValue({})

  await expect(completeSignIn()).resolves.toBe('/')
})

it('signs out through the manager', async () => {
  await signOut()

  expect(manager.signoutRedirect).toHaveBeenCalled()
})
