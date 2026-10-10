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

/** File size in 1024-based units. */
export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`
}

/** An age given in hours: "< 1 h", "5 h", then days from 48 h on. */
export function age(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return '—'
  if (hours < 1) return '< 1 h'
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} d`
}
