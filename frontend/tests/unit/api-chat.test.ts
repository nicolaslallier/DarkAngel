import { beforeEach, describe, expect, it, vi } from 'vitest'

import { listConversations, sendMessage } from '@/api/chat'
import { accessToken } from '@/auth'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))

const fetchMock = vi.fn()
vi.stubGlobal('fetch', fetchMock)

beforeEach(() => {
  fetchMock.mockReset()
  vi.mocked(accessToken).mockResolvedValue(null)
})

it('listConversations encodes q', async () => {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => [] })

  await listConversations('a b&c')

  expect(fetchMock.mock.calls[0][0]).toBe('/api/chat/conversations?q=a%20b%26c')
})

describe('sendMessage', () => {
  it('posts to /chat/conversations/new/messages and yields typed events', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('event: content\ndata: {"text":"x"}\n\n'))
        c.close()
      },
    })
    fetchMock.mockResolvedValue({ ok: true, status: 200, body })
    const payload = { content: 'hi', model: 'm' }

    const events = []
    for await (const e of sendMessage('new', payload, new AbortController().signal)) events.push(e)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/chat/conversations/new/messages')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify(payload))
    expect(events).toEqual([{ event: 'content', data: { text: 'x' } }])
  })
})
