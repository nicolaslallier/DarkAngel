import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '@/markdown'

describe('renderMarkdown', () => {
  it('strips script tags', () => {
    expect(renderMarkdown('hi <script>alert(1)</script>')).not.toContain('<script')
  })
  it('strips onerror attributes', () => {
    expect(renderMarkdown('<img src=x onerror=alert(1)>')).not.toContain('onerror')
  })
  it('drops javascript: links', () => {
    expect(renderMarkdown('[x](javascript:alert(1))').toLowerCase()).not.toContain('javascript:')
  })
  it('drops data: and vbscript: links', () => {
    expect(renderMarkdown('[x](data:text/html;base64,PHNjcmlwdD4=)')).not.toContain('data:')
    expect(renderMarkdown('[x](vbscript:msgbox(1))')).not.toContain('vbscript:')
    expect(renderMarkdown('<a href="data:text/html,x">y</a>')).not.toContain('data:')
  })
  it('strips iframe, style and form', () => {
    const html = renderMarkdown(
      '<iframe src="https://e.com"></iframe><style>a{}</style><form action="/x"><input></form>',
    )
    expect(html).not.toMatch(/<(iframe|style|form)/)
  })
  it('highlights fenced code', () => {
    expect(renderMarkdown('```js\nconst a = 1\n```')).toContain('hljs')
  })
  it('opens links in a new tab safely', () => {
    const html = renderMarkdown('[x](https://example.com)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })
  it('only touches anchors with target/rel', () => {
    const html = renderMarkdown('![i](https://example.com/a.png) **b**')
    expect(html).not.toContain('target=')
    expect(html).not.toContain('rel=')
  })
  it('renders GFM tables', () => {
    expect(renderMarkdown('|a|b|\n|-|-|\n|1|2|')).toContain('<table')
  })
})
