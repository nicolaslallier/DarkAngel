import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '@/markdown'

describe('renderMarkdown', () => {
  it('strips script tags', () => {
    const html = renderMarkdown('hi <script>alert(1)</script>')
    expect(html).not.toContain('<script')
    expect(html).toContain('hi')
  })
  it('strips onerror attributes', () => {
    const html = renderMarkdown('ok <img src=x onerror=alert(1)>')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('<img')
    expect(html).toContain('ok')
  })
  it('drops javascript: links', () => {
    const html = renderMarkdown('[click](javascript:alert(1))').toLowerCase()
    expect(html).not.toContain('javascript:')
    expect(html).toContain('click')
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
  it('strips inline style attributes but keeps the element', () => {
    const html = renderMarkdown('<p style="position:fixed">x</p>')
    expect(html).toContain('<p>x</p>')
    expect(html).not.toContain('style')
  })
  it('renders markdown images as links, never <img>', () => {
    const html = renderMarkdown('![cat pic](https://attacker.example/?q=secret)')
    expect(html).not.toContain('<img')
    expect(html).toMatch(/<a [^>]*rel="noopener noreferrer"[^>]*>cat pic<\/a>/)
    expect(renderMarkdown('![](https://a.example/x.png)')).toContain('>https://a.example/x.png</a>')
  })
  it('renders code containing html as inert escaped text', () => {
    const html = renderMarkdown('```\n<img src=x onerror=alert(1)>\n```')
    expect(html).toContain('&lt;img')
    expect(html).not.toContain('<img')
    expect(html).toContain('hljs')
  })
  it('only adds target/rel to anchors', () => {
    const html = renderMarkdown('**b** text')
    expect(html).not.toContain('target=')
    expect(html).not.toContain('rel=')
  })
  it('renders GFM tables', () => {
    expect(renderMarkdown('|a|b|\n|-|-|\n|1|2|')).toContain('<table')
  })
})
