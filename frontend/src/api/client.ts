import { accessToken } from '@/auth'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export async function apiRequest(method: string, path: string, body?: BodyInit): Promise<Response> {
  const token = await accessToken()
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {}
  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body })

  if (!response.ok) {
    throw new Error(`${method} ${path} failed with ${response.status}`)
  }

  return response
}

export async function apiGet<T>(path: string): Promise<T> {
  return (await (await apiRequest('GET', path)).json()) as T
}
