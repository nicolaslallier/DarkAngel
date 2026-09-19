const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`)

  if (!response.ok) {
    throw new Error(`GET ${path} failed with ${response.status}`)
  }

  return (await response.json()) as T
}
