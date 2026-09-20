import { apiGet } from './client'

export interface Me {
  sub: string
  username: string | null
  email: string | null
  roles: string[]
}

export function fetchMe(): Promise<Me> {
  return apiGet<Me>('/me')
}
