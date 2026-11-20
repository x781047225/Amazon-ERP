// Quick filters use the operator's business day; report dates retain their original meaning.
export const adsToday = (at = new Date()) => new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(at)
export function adsPeriodDates(value: string, at = new Date()): string[] {
  const end = new Date(adsToday(at) + 'T12:00:00Z')
  if (value !== 'today' && value !== 'custom') end.setUTCDate(end.getUTCDate() - 1)
  const start = new Date(end)
  if (['3', '7', '14', '30'].includes(value)) start.setUTCDate(start.getUTCDate() - Number(value) + 1)
  else if (['m3', 'm6', 'y1'].includes(value)) {
    const day = start.getUTCDate()
    start.setUTCDate(1)
    start.setUTCMonth(start.getUTCMonth() - (value === 'm3' ? 3 : value === 'm6' ? 6 : 12))
    const last = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate()
    start.setUTCDate(Math.min(day, last) + 1)
  }
  return [start, end].map(d => d.toISOString().slice(0, 10))
}
