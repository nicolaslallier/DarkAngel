// The only place model-written text becomes HTML (rendered with v-html): keep
// every path through DOMPurify.
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { Marked } from 'marked'

const FORBID_TAGS = ['img', 'style', 'form', 'input', 'button', 'textarea', 'select', 'iframe']

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
  return DOMPurify.sanitize(marked.parse(text, { async: false }), { FORBID_TAGS, FORBID_ATTR: ['style'] })
}
