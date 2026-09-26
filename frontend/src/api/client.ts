import { accessToken } from '@/auth'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** A non-OK response. `body` is the parsed JSON (e.g. a folder delete's
 *  `{detail, folders, files}`), or null when there was none. */
export class ApiError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function messageOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join('; ')
  return fallback
}

export async function apiRequest(
  method: string,
  path: string,
  body?: BodyInit,
  contentType?: string,
): Promise<Response> {
  const token = await accessToken()
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
  if (contentType) headers['Content-Type'] = contentType
  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body })

  if (!response.ok) {
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      // Not JSON (a proxy error page, an empty body): keep the generic message.
    }
    const fallback = `${method} ${path} failed with ${response.status}`
    throw new ApiError(messageOf(payload, fallback), response.status, payload)
  }

  return response
}

export async function apiGet<T>(path: string): Promise<T> {
  return (await (await apiRequest('GET', path)).json()) as T
}
