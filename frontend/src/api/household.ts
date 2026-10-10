import { apiGet, apiRequest, apiSend } from './client'

export type Role = 'owner' | 'member' | 'viewer'

export interface Member {
  sub: string
  role: Role
  display_name: string | null
}

export interface Household {
  id: string
  name: string
  role: Role
  members: Member[]
}

export interface Invitation {
  token: string
  expires_in_days: number
}

export function getHousehold(): Promise<Household> {
  return apiGet<Household>('/household')
}

export function createHousehold(name: string): Promise<Household> {
  return apiSend<Household>('POST', '/household', { name })
}

export async function deleteHousehold(): Promise<void> {
  await apiRequest('DELETE', '/household')
}

export function createInvitation(role: 'member' | 'viewer'): Promise<Invitation> {
  return apiSend<Invitation>('POST', '/household/invitations', { role })
}

export function joinHousehold(token: string): Promise<Household> {
  return apiSend<Household>('POST', '/household/join', { token })
}

export function setMemberRole(sub: string, role: 'member' | 'viewer'): Promise<Member> {
  return apiSend<Member>('PATCH', `/household/members/${encodeURIComponent(sub)}`, { role })
}

export async function removeMember(sub: string): Promise<void> {
  await apiRequest('DELETE', `/household/members/${encodeURIComponent(sub)}`)
}

export async function leaveHousehold(): Promise<void> {
  await apiRequest('POST', '/household/leave')
}
