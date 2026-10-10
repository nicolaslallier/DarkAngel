const currency = new Intl.NumberFormat('fr-CA', { style: 'currency', currency: 'CAD' })

/** Money arrives as a decimal string (the API never sends floats for amounts). */
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return currency.format(Number(value))
}

/** Trimmed text, or null when empty: what the API wants for an optional field. */
export function blankToNull(value: string | number | null | undefined): string | null {
  const text = String(value ?? '').trim()
  return text === '' ? null : text
}
