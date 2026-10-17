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
  const message = (detail as { message?: unknown } | undefined)?.message
  if (typeof message === 'string') return message
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join('; ')
  return fallback
}

export async function apiRequest(
  method: string,
  path: string,
  body?: BodyInit,
  contentType?: string,
  signal?: AbortSignal,
): Promise<Response> {
  const token = await accessToken()
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
  if (contentType) headers['Content-Type'] = contentType
  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body, signal })

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

/** JSON in, JSON out. A DELETE or a 204 has no body: use apiRequest for those. */
export async function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  const json = body === undefined ? undefined : JSON.stringify(body)
  const response = await apiRequest(method, path, json, json === undefined ? undefined : 'application/json')
  return (await response.json()) as T
}

/** A server-sent-events response as an async generator of `{event, data}` (data
 *  JSON-parsed). A 204 yields nothing; errors are `ApiError`s from apiRequest. */
export async function* apiStream(
  method: string,
  path: string,
  body: unknown,
  signal: AbortSignal,
): AsyncGenerator<{ event: string; data: unknown }> {
  const json = body === undefined ? undefined : JSON.stringify(body)
  const response = await apiRequest(
    method,
    path,
    json,
    json === undefined ? undefined : 'application/json',
    signal,
  )
  if (response.status === 204 || !response.body) return

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = done ? '' : (frames.pop() ?? '')
    for (const frame of frames) {
      let event = 'message'
      const data: string[] = []
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (data.length) yield { event, data: JSON.parse(data.join('\n')) }
    }
    if (done) return
  }
}
