import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { createHousehold, createInvitation, getHousehold } from '@/api/household'
import HouseholdView from '@/views/HouseholdView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/household', () => ({
  getHousehold: vi.fn(),
  createHousehold: vi.fn(),
  deleteHousehold: vi.fn(async () => {}),
  createInvitation: vi.fn(),
  joinHousehold: vi.fn(),
  setMemberRole: vi.fn(async () => ({})),
  removeMember: vi.fn(async () => {}),
  leaveHousehold: vi.fn(async () => {}),
}))

const owned = {
  id: 'h1',
  name: 'Maison',
  role: 'owner' as const,
  members: [
    { sub: 'alice', role: 'owner' as const },
    { sub: 'bob', role: 'member' as const },
  ],
}

async function render() {
  const wrapper = mount(HouseholdView, { global: { plugins: [createPinia()] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('offers to create a household when the person has none', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))
  vi.mocked(createHousehold).mockResolvedValue(owned)
  const wrapper = await render()

  await wrapper.find('input[name="household-name"]').setValue('Maison')
  await wrapper.find('form').trigger('submit')
  await flushPromises()

  expect(createHousehold).toHaveBeenCalledWith('Maison')
  expect(wrapper.text()).toContain('bob')
})

it('lets the owner generate an invitation link', async () => {
  vi.mocked(getHousehold).mockResolvedValue(owned)
  vi.mocked(createInvitation).mockResolvedValue({ token: 'abc 123', expires_in_days: 7 })
  const wrapper = await render()

  await wrapper.find('select[name="invite-role"]').setValue('viewer')
  await wrapper.find('button[data-test="invite"]').trigger('click')
  await flushPromises()

  expect(createInvitation).toHaveBeenCalledWith('viewer')
  const link = (wrapper.find('input[data-test="invite-link"]').element as HTMLInputElement).value
  expect(link).toContain('/household/join?token=abc%20123')
})

it('hides the owner controls from a member', async () => {
  vi.mocked(getHousehold).mockResolvedValue({ ...owned, role: 'member' })
  const wrapper = await render()

  expect(wrapper.find('button[data-test="invite"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="delete-household"]').exists()).toBe(false)
  expect(wrapper.find('button[data-test="leave"]').exists()).toBe(true)
})

it('does not offer to create a household when loading failed', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new Error('boom'))
  const wrapper = await render()

  expect(wrapper.find('form').exists()).toBe(false)
  expect(wrapper.text()).toContain('boom')
})
