import { expect, it, vi } from 'vitest'

import { router } from '@/router'

vi.mock('@/auth', () => ({
  accessToken: vi.fn(async () => 'a-token'),
  signIn: vi.fn(async () => {}),
  completeSignIn: vi.fn(async () => '/'),
  signOut: vi.fn(async () => {}),
}))

it.each([
  ['/household', 'household'],
  ['/household/join', 'household-join'],
  ['/providers', 'providers'],
  ['/providers/abc', 'provider'],
  ['/services/abc', 'service'],
  ['/invoices/review', 'invoice-review'],
])('%s resolves to %s', (path, name) => {
  expect(router.resolve(path).name).toBe(name)
})
