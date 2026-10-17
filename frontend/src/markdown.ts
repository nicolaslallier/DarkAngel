// The only place model-written text becomes HTML (rendered with v-html): keep
// every path through DOMPurify.
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { Marked } from 'marked'

// Allowlist: exactly what marked and our renderers emit (no media, forms, svg).
const ALLOWED_TAGS = [
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'del', 'code', 'pre',
  'span', 'blockquote', 'ul', 'ol', 'li', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
const ALLOWED_ATTR = ['href', 'class', 'title', 'target', 'rel', 'align', 'start']

const escapeHtml = (t: string) =>
  t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const marked = new Marked({
  gfm: true,
  renderer: {
    // remote images would auto-load (exfiltration channel): show a link instead
    image({ href, text }) {
      return `<a href="${escapeHtml(href)}">${escapeHtml(text || href)}</a>`
    },
    code({ text, lang }) {
      const language = lang && hljs.getLanguage(lang) ? lang : undefined
      // no highlightAuto: it would re-run on every streamed delta
      const body = language ? hljs.highlight(text, { language }).value : escapeHtml(text)
      return `<pre><code class="hljs${language ? ` language-${language}` : ''}">${body}</code></pre>\n`
    },
  },
})

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && node.hasAttribute('href')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }), { ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false })
}
