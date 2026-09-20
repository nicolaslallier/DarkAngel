import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiGet, apiRequest } from '@/api/client'
import { accessToken } from '@/auth'

// The client is the only module that calls fetch; auth is the only thing it
// needs from the rest of the app, so it is the only thing mocked.
vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))

const fetchMock = vi.fn()
vi.stubGlobal('fetch', fetchMock)

function response(status: number, body: unknown = {}) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockResolvedValue(response(200))
  vi.mocked(accessToken).mockResolvedValue(null)
})

describe('apiRequest', () => {
  it('prefixes the path with /api and attaches the bearer token', async () => {
    vi.mocked(accessToken).mockResolvedValue('a-token')

    await apiRequest('GET', '/files')

    expect(fetchMock).toHaveBeenCalledWith('/api/files', {
      method: 'GET',
      headers: { Authorization: 'Bearer a-token' },
      body: undefined,
    })
  })

  it('sends no Authorization header when there is no session', async () => {
    await apiRequest('GET', '/files')

    expect(fetchMock.mock.calls[0][1].headers).toEqual({})
  })

  it('passes the body straight through', async () => {
    const form = new FormData()

    await apiRequest('POST', '/files', form)

    expect(fetchMock.mock.calls[0][1].body).toBe(form)
  })

  it('throws with the method, path and status when the response is not ok', async () => {
    fetchMock.mockResolvedValue(response(500))

    await expect(apiRequest('DELETE', '/files/a.txt')).rejects.toThrow(
      'DELETE /files/a.txt failed with 500',
    )
  })
})

describe('apiGet', () => {
  it('returns the parsed JSON body', async () => {
    fetchMock.mockResolvedValue(response(200, [{ name: 'a.txt' }]))

    await expect(apiGet('/files')).resolves.toEqual([{ name: 'a.txt' }])
  })

  it('honours VITE_API_BASE_URL when it is set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test')
    // BASE_URL is read once, at module load, so the module has to be re-imported.
    vi.resetModules()
    const { apiGet: freshGet } = await import('@/api/client')

    await freshGet('/health')

    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/health')
    vi.unstubAllEnvs()
  })
})
